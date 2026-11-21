"""Prompt-injection hardening for record text.

Org notes, contact fields and message summaries can come from CSV imports or pasted
emails, so they are untrusted. Two cheap, mechanical layers live here:

  1. sanitize_text: drop sentences that look like instructions aimed at an AI, and drop
     any field that tries to close the <record> block early.
  2. leaked_tokens: after the model answers, find distinctive tokens from the removed
     text (codes, ALL-CAPS words, percentages). If the draft repeats one, the model was
     steered and the draft is rejected.

This is a heuristic filter, not a proof. It catches common phrasings; paraphrased
injections can slip through, which is why the evals include scenarios it misses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REMOVED_MARKER = "[removed: text that looked like instructions to an AI]"

# Sentences matching any of these are removed. Patterns avoid common benign phrasing
# such as "you must send a W9" or "please send dates".
INJECTION_PATTERNS = (
    re.compile(r"\bignore (?:all |any |every )?(?:the )?(?:previous|prior|above|earlier|preceding)\b", re.I),
    re.compile(r"\b(?:disregard|forget)\b[^.!?\n]{0,40}\b(?:instructions?|rules|guidelines|above|previous|prior)\b", re.I),
    re.compile(r"\b(?:system|developer|admin(?:istrator)?)\s+(?:override|prompt|message|instructions?)\b", re.I),
    re.compile(r"\b(?:new|updated|real)\s+(?:instructions?|task|rules)\s*:", re.I),
    re.compile(r"\b(?:note|message|instructions?|attention)\s+(?:to|for)\s+(?:the\s+)?(?:ai|a\.i\.|assistant|model|llm|bot|chatbot)\b", re.I),
    re.compile(r"\b(?:assistant|ai|model|llm|chatbot)\s*[,:]\s*(?:please\s+)?(?:write|say|include|add|tell|offer|respond|reply|end|start|append|ignore|output|mention)\b", re.I),
    re.compile(r"\b(?:you are now|act as|pretend (?:to be|you)|from now on)\b", re.I),
    re.compile(r"\b(?:end|finish|conclude|append|start|begin)\s+(?:the |your )?(?:message|reply|email|draft|response)\b[^.!?\n]{0,30}\bwith\b", re.I),
    re.compile(r"\b(?:include|add|insert|use|write|say|type)\b[^.!?\n]{0,30}\b(?:the )?(?:secret )?(?:code|code word|passphrase|password)\b", re.I),
)
DELIMITER_ESCAPE = re.compile(r"</?\s*record\b", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

_CAPS_TOKEN = re.compile(r"\b[A-Z][A-Z0-9\-]{3,}\b")
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")
_CODE_LIKE = re.compile(r"\b[A-Za-z]+-\d+\b")


@dataclass(frozen=True)
class Finding:
    field: str
    kind: str  # "instruction" or "delimiter"
    removed: str


def _neutralize_brackets(text: str) -> str:
    return text.replace("<", "‹").replace(">", "›")


def sanitize_text(value: str, field: str) -> tuple[str, list[Finding]]:
    """Return (clean_value, findings). Benign text passes through unchanged."""
    if not value:
        return value, []
    if DELIMITER_ESCAPE.search(value):
        return REMOVED_MARKER, [Finding(field, "delimiter", value[:300])]
    kept, findings = [], []
    for sentence in SENTENCE_SPLIT.split(value):
        if not sentence.strip():
            continue
        if any(p.search(sentence) for p in INJECTION_PATTERNS):
            findings.append(Finding(field, "instruction", sentence.strip()[:300]))
        else:
            kept.append(sentence)
    if not findings:
        return _neutralize_brackets(value), []
    clean = " ".join(kept)
    return _neutralize_brackets(f"{clean} {REMOVED_MARKER}".strip()), findings


def distinctive_tokens(findings: list[Finding], clean_context: str) -> set[str]:
    """Tokens from removed text that would reveal the model obeyed it.

    Tokens that also appear in the clean context are excluded so legitimate words
    never trigger a false rejection.
    """
    haystack = clean_context.lower()
    tokens: set[str] = set()
    for f in findings:
        for rx in (_CAPS_TOKEN, _PERCENT, _CODE_LIKE):
            tokens.update(m.group().strip() for m in rx.finditer(f.removed))
    return {t for t in tokens if t.lower() not in haystack}


def leaked_tokens(text: str, tokens: set[str]) -> list[str]:
    """Tokens from removed text that appear in the model's output."""
    return sorted(
        t for t in tokens if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text, re.IGNORECASE)
    )
