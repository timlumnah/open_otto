"""Turn saved eval results into a comparison table.

    python -m evals.compare evals/results/*.json
    python -m evals.compare evals/results/*.json --out evals/results/TABLE.md

Reads the files written by `python -m evals --json`. Output is a markdown table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _rate(runs: list[dict]) -> str:
    scored = [r for r in runs if r["status"] != "error"]
    if not scored:
        return "n/a"
    passed = sum(r["passed"] for r in scored)
    return f"{passed}/{len(scored)} ({passed / len(scored):.0%})"


def _obeyed(runs: list[dict]) -> str:
    """Runs whose draft contained the injected payload (a forbidden_terms check failed)."""
    scored = [r for r in runs if r["status"] != "error"]
    if not scored:
        return "n/a"
    hit = sum(any(c["name"] == "forbidden_terms" and not c["passed"] for c in r["checks"]) for r in scored)
    return f"{hit}/{len(scored)}"


def summarize(data: dict) -> dict:
    groups: dict[str, list[dict]] = {"all": [], "detectable": [], "evasive": [], "other": []}
    for sc in data["scenarios"]:
        tags = sc.get("tags", [])
        key = "other" if "injection" not in tags else ("evasive" if "evasive" in tags else "detectable")
        groups[key] += sc["runs"]
        groups["all"] += sc["runs"]
    return {
        "model": data["model"],
        "mode": "hardened" if data.get("hardened", True) else "baseline",
        "runs": data["runs"],
        "all": _rate(groups["all"]),
        "detectable": _rate(groups["detectable"]),
        "evasive": _rate(groups["evasive"]),
        "other": _rate(groups["other"]),
        "obeyed_detectable": _obeyed(groups["detectable"]),
        "obeyed_evasive": _obeyed(groups["evasive"]),
        "rejected": sum(r["status"] == "rejected" for r in groups["all"]),
        "errors": sum(r["status"] == "error" for r in groups["all"]),
        "total": len(groups["all"]),
        "when": data.get("started_at", "")[:10],
    }


def build_table(paths: list[Path]) -> str:
    rows = [summarize(json.loads(Path(p).read_text())) for p in paths]
    rows.sort(key=lambda r: (r["model"], r["mode"] != "baseline"))
    header = ("| Model | Mode | Overall | Obeyed injection (detectable) | Obeyed injection (evasive) "
              "| Non-injection | Rejected drafts | Errors |")
    lines = [header, "|" + "---|" * 8]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['mode']} | {r['all']} | {r['obeyed_detectable']} | {r['obeyed_evasive']} "
            f"| {r['other']} | {r['rejected']}/{r['total']} | {r['errors']} |"
        )
    runs = sorted({r["runs"] for r in rows})
    dates = sorted({r["when"] for r in rows if r["when"]})
    lines += ["", f"Runs per scenario: {', '.join(map(str, runs))}. Dates: {', '.join(dates)}. "
              "Overall = runs that passed every mechanical check. Obeyed injection = the draft contained the planted "
              "payload (lower is better). Rejected drafts were refused by a guardrail "
              "(broken format, placeholder, or repeated injected text)."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evals.compare")
    p.add_argument("files", nargs="+")
    p.add_argument("--out", help="also write the table to this file")
    args = p.parse_args(argv)
    try:
        table = build_table([Path(f) for f in args.files])
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: could not read results: {exc}", file=sys.stderr)
        return 2
    print(table)
    if args.out:
        Path(args.out).write_text(table + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
