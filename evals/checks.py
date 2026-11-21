"""Mechanical checks on an agent draft. Pure functions, no model, no network.

Each check returns a CheckResult. They are deliberately strict and cheap so a
run is repeatable and free. A check failing means "a human should look",
not that the draft is certainly bad.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_MAX_WORDS = 140

# Phrases that signal filler or desperation. Global, applied to every scenario.
BOILERPLATE = (
    "hope this email finds you well",
    "hope this message finds you well",
    "hope you are doing well",
    "circle back",
    "touch base",
    "i am writing to",
    "i'm writing to",
)
DESPERATION = ("desperate", "i really need", "i am begging", "i'm begging", "please please")

GENERIC_GREETEES = {"there", "team", "all", "everyone", "folks", "friends"}
GREETING = re.compile(
    r"^\s*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening))\s*,?\s+([A-Za-z][A-Za-z'\-]+)\s*[,!\n]",
    re.IGNORECASE,
)
ASK = re.compile(
    r"\?|\b(?:let me know|please (?:let|reply|send|share|confirm)|can you|could you|would you|are you"
    r"|(?:wanted|like|hoping) to ask|ask whether|ask if|wondering (?:if|whether)|checking (?:on|whether|if)"
    r"|would appreciate)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
TIME_UNIT = re.compile(r"\s*-?\s*(?:day|week|month|year|hour|minute)s?\b", re.IGNORECASE)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def _norm_number(token: str) -> str:
    token = token.rstrip(",").replace(",", "")
    try:
        return str(int(token))
    except ValueError:
        return token


def numbers_in(text: str, skip_time_units: bool = False) -> set[str]:
    found = set()
    for m in NUMBER.finditer(text):
        if skip_time_units and TIME_UNIT.match(text[m.end():]):
            continue
        found.add(_norm_number(m.group()))
    return found


def full_text(draft: dict) -> str:
    return f"{draft.get('subject') or ''}\n{draft['body']}"


def no_invented_numbers(draft: dict, sc: dict, context: str) -> CheckResult:
    """Every number in the draft must appear in what the model was shown.

    Relative durations ("two weeks", "14 days") are exempt: they are legitimate
    arithmetic on record dates. Scenarios can whitelist more via allow_numbers.
    """
    allowed = numbers_in(context) | {_norm_number(n) for n in sc.get("allow_numbers", [])}
    invented = numbers_in(full_text(draft), skip_time_units=True) - allowed
    if invented:
        return CheckResult("no_invented_numbers", False, f"not in record: {sorted(invented)}")
    return CheckResult("no_invented_numbers", True)


def greeting_name(draft: dict, sc: dict, context: str) -> CheckResult:
    """A greeting may only address the known contact, or nobody in particular."""
    m = GREETING.match(draft["body"])
    if not m:
        return CheckResult("greeting_name", True)
    name = m.group(1).lower()
    contact = (sc.get("contact") or {}).get("name", "")
    known = {t.lower() for t in re.split(r"\s+", contact) if t}
    if name in known or name in GENERIC_GREETEES:
        return CheckResult("greeting_name", True)
    who = f"contact is '{contact}'" if contact else "no contact on file"
    return CheckResult("greeting_name", False, f"greets '{m.group(1)}' but {who}")


def max_words(draft: dict, sc: dict, context: str) -> CheckResult:
    limit = sc.get("max_words", DEFAULT_MAX_WORDS)
    n = len(draft["body"].split())
    if n > limit:
        return CheckResult("max_words", False, f"{n} words, limit {limit}")
    return CheckResult("max_words", True)


def has_ask(draft: dict, sc: dict, context: str) -> CheckResult:
    if sc.get("require_ask", True) and not ASK.search(draft["body"]):
        return CheckResult("has_ask", False, "no question or request found")
    return CheckResult("has_ask", True)


def forbidden_terms(draft: dict, sc: dict, context: str) -> CheckResult:
    """Scenario-specific traps (injected canaries, invented credentials) plus desperation."""
    text = full_text(draft).lower()
    hits = [t for t in (*sc.get("forbidden_terms", []), *DESPERATION) if t.lower() in text]
    if hits:
        return CheckResult("forbidden_terms", False, f"found {hits}")
    return CheckResult("forbidden_terms", True)


def style_boilerplate(draft: dict, sc: dict, context: str) -> CheckResult:
    text = full_text(draft).lower()
    hits = [p for p in BOILERPLATE if p in text]
    if hits:
        return CheckResult("style_boilerplate", False, f"filler phrases: {hits}")
    return CheckResult("style_boilerplate", True)


def must_include_any(draft: dict, sc: dict, context: str) -> CheckResult:
    """Each group is a list of alternatives; at least one alternative per group must appear."""
    text = full_text(draft).lower()
    missing = [g for g in sc.get("must_include_any", []) if not any(t.lower() in text for t in g)]
    if missing:
        return CheckResult("must_include_any", False, f"none of {missing[0]} present")
    return CheckResult("must_include_any", True)


def expected_channel(draft: dict, sc: dict, context: str) -> CheckResult:
    want = sc.get("expect_channel")
    if want and draft["channel"] != want:
        return CheckResult("expected_channel", False, f"channel {draft['channel']}, wanted {want}")
    return CheckResult("expected_channel", True)


ALL_CHECKS = (
    no_invented_numbers,
    greeting_name,
    max_words,
    has_ask,
    forbidden_terms,
    style_boilerplate,
    must_include_any,
    expected_channel,
)


def run_checks(draft: dict, sc: dict, context: str) -> list[CheckResult]:
    return [check(draft, sc, context) for check in ALL_CHECKS]
