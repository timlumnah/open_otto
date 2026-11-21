"""Eval harness: score a model's drafts against fixed scenarios.

    python -m evals --model ollama:llama3.1
    python -m evals --model anthropic:claude-haiku-5-5 --runs 3 --json results.json
    python -m evals --selftest        # prove the checks work, no model needed

Each scenario builds a fresh in-memory database, asks the model for a draft through
the same code path the real app uses (agent.drafting), then runs mechanical checks.
Infrastructure failures (model unreachable) are reported as errors and never scored
as the model being bad.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent import drafting
from agent.models import FakeModel, Model, ModelError, get_model
from core import db, pipeline
from evals.checks import CheckResult, run_checks

SCENARIOS_PATH = Path(__file__).resolve().parent / "scenarios.json"


@dataclass
class RunResult:
    scenario_id: str
    status: str  # "ok" (draft produced), "rejected" (guardrail/parse failure), "error" (infrastructure)
    checks: list[CheckResult] = field(default_factory=list)
    draft: dict | None = None
    detail: str = ""

    @property
    def passed(self) -> bool:
        if self.status == "error":
            return False
        if self.status == "rejected":
            return False
        return all(c.passed for c in self.checks)

    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    scenarios = json.loads(Path(path).read_text())
    ids = [s["id"] for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids")
    return scenarios


def seed_scenario(conn, sc: dict) -> str:
    """Build the scenario's world in a fresh database. Returns the org name."""
    org = dict(sc["org"])
    name = org.pop("name")
    stage = org.pop("stage", None)
    contact = sc.get("contact")
    pipeline.add_org(conn, sc["pack"], name, stage=stage, contact=contact, actor="eval", **org)
    org_id = conn.execute("SELECT id FROM orgs WHERE name = ?", (name,)).fetchone()[0]
    contact_id = conn.execute(
        "SELECT id FROM contacts WHERE org_id = ? AND is_primary = 1", (org_id,)
    ).fetchone()
    with conn:
        for i in sc.get("interactions", []):
            conn.execute(
                "INSERT INTO interactions (org_id, contact_id, channel, direction, summary, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now', ?))",
                (org_id, contact_id[0] if contact_id else None, i["channel"], i["direction"],
                 i["summary"], f"-{int(i['days_ago'])} days"),
            )
    return name


def run_scenario(sc: dict, model: Model, harden: bool = True) -> RunResult:
    conn = db.connect()
    try:
        db.migrate(conn)
        db.run_seed(conn, db.DEFAULT_SEEDS_DIR / "001_pack_stages.sql")
        name = seed_scenario(conn, sc)
        org_id = conn.execute("SELECT id FROM orgs WHERE name = ?", (name,)).fetchone()[0]
        voice = sc.get("voice")
        context = drafting.build_context(conn, org_id, voice, harden)
        try:
            draft_id = drafting.draft_for_org(conn, model, name, voice, harden=harden)
        except drafting.DraftError as exc:
            if isinstance(exc.__cause__, ModelError):
                return RunResult(sc["id"], "error", detail=str(exc))
            return RunResult(sc["id"], "rejected", detail=str(exc))
        row = conn.execute("SELECT subject, body, channel FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        draft = {"subject": row["subject"], "body": row["body"], "channel": row["channel"]}
        return RunResult(sc["id"], "ok", run_checks(draft, sc, context), draft)
    finally:
        conn.close()


@dataclass
class ScenarioSummary:
    scenario_id: str
    runs: list[RunResult]
    tags: list[str] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return sum(r.passed for r in self.runs)

    @property
    def errors(self) -> int:
        return sum(r.status == "error" for r in self.runs)


@dataclass
class Report:
    model: str
    runs: int
    summaries: list[ScenarioSummary]
    started_at: str
    hardened: bool = True

    @property
    def errors(self) -> int:
        return sum(s.errors for s in self.summaries)

    @property
    def scored_runs(self) -> list[RunResult]:
        return [r for s in self.summaries for r in s.runs if r.status != "error"]

    @property
    def score(self) -> float:
        """Fraction of scored runs that passed every check. Errors are excluded."""
        scored = self.scored_runs
        return sum(r.passed for r in scored) / len(scored) if scored else 0.0

    @property
    def check_rate(self) -> tuple[int, int]:
        results = [c for r in self.scored_runs for c in r.checks]
        return sum(c.passed for c in results), len(results)


def evaluate(model: Model, scenarios: list[dict], runs: int = 1, harden: bool = True) -> Report:
    summaries = [
        ScenarioSummary(sc["id"], [run_scenario(sc, model, harden) for _ in range(runs)], sc.get("tags", []))
        for sc in scenarios
    ]
    return Report(model.name, runs, summaries,
                  datetime.now(timezone.utc).isoformat(timespec="seconds"), harden)


def format_report(report: Report) -> str:
    mode = "hardened" if report.hardened else "baseline (no injection hardening)"
    lines = [f"model: {report.model}   runs per scenario: {report.runs}   mode: {mode}", ""]
    for s in report.summaries:
        if s.errors == len(s.runs):
            status, why = "ERR ", s.runs[0].detail
        else:
            ok = s.passes == len([r for r in s.runs if r.status != "error"])
            status = "PASS" if ok else "FAIL"
            reasons = []
            for r in s.runs:
                if r.status == "rejected":
                    reasons.append(f"rejected: {r.detail}")
                reasons += [f"{c.name}: {c.detail}" for c in r.failed_checks()]
            why = "; ".join(dict.fromkeys(reasons))
        lines.append(f"{status}  {s.scenario_id:<28} {s.passes}/{len(s.runs)}  {why}".rstrip())
    passed, total = report.check_rate
    scored = len(report.scored_runs)
    lines += [
        "",
        f"runs passed: {sum(r.passed for r in report.scored_runs)}/{scored}   "
        f"score: {report.score:.0%}   checks passed: {passed}/{total}   errors: {report.errors}",
    ]
    for tag in sorted({t for s in report.summaries for t in s.tags}):
        runs_ = [r for s in report.summaries if tag in s.tags for r in s.runs if r.status != "error"]
        lines.append(f"  {tag}: {sum(r.passed for r in runs_)}/{len(runs_)} runs passed")
    return "\n".join(lines)


def report_to_dict(report: Report) -> dict:
    return {
        "model": report.model,
        "runs": report.runs,
        "hardened": report.hardened,
        "started_at": report.started_at,
        "score": report.score,
        "errors": report.errors,
        "scenarios": [
            {
                "id": s.scenario_id,
                "tags": s.tags,
                "passes": s.passes,
                "runs": [
                    {"status": r.status, "passed": r.passed, "detail": r.detail, "draft": r.draft,
                     "checks": [asdict(c) for c in r.checks]}
                    for r in s.runs
                ],
            }
            for s in report.summaries
        ],
    }


def selftest(scenarios: list[dict]) -> list[str]:
    """Verify the harness itself. Returns a list of problems (empty means healthy).

    Every reference reply must pass all checks. Every bad reply must fail the specific
    check named in its should_fail field.
    """
    problems = []
    for sc in scenarios:
        result = run_scenario(sc, FakeModel(json.dumps(sc["reference_reply"]), name="reference"))
        if not result.passed:
            why = result.detail or "; ".join(f"{c.name}: {c.detail}" for c in result.failed_checks())
            problems.append(f"{sc['id']}: reference reply should pass but did not ({why})")
        for bad in sc.get("bad_replies", []):
            reply = bad["reply"] if isinstance(bad["reply"], str) else json.dumps(bad["reply"])
            # Hardening off: this exercises the scoring checks themselves.
            result = run_scenario(sc, FakeModel(reply, name="adversarial"), harden=False)
            failed = {c.name for c in result.failed_checks()}
            if result.status != "ok":
                problems.append(f"{sc['id']}: bad reply for '{bad['should_fail']}' was not even drafted ({result.detail})")
            elif bad["should_fail"] not in failed:
                problems.append(
                    f"{sc['id']}: bad reply should fail '{bad['should_fail']}' but failed {sorted(failed) or 'nothing'}"
                )
            # Defense: for a detectable injection, none of its payload words may reach the model.
            tags = sc.get("tags", [])
            if "injection" in tags and "evasive" not in tags and bad is sc["bad_replies"][0]:
                spy = FakeModel(reply, name="adversarial")
                run_scenario(sc, spy, harden=True)
                seen = spy.calls[0][1].lower() if spy.calls else ""
                leaked = [t for t in sc.get("forbidden_terms", []) if t.lower() in seen]
                if leaked:
                    problems.append(f"{sc['id']}: hardening left injected text in the prompt: {leaked}")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evals", description="Score a model's outreach drafts.")
    p.add_argument("--model", help="provider:model, or set $OPEN_OTTO_MODEL")
    p.add_argument("--scenario", action="append", help="run only this scenario id (repeatable)")
    p.add_argument("--runs", type=int, default=1, help="runs per scenario (models are not deterministic)")
    p.add_argument("--json", help="also write full results to this file")
    p.add_argument("--min-score", type=float, help="exit 1 if the score is below this (0 to 1)")
    p.add_argument("--no-hardening", action="store_true",
                   help="baseline mode: skip injection hardening (for before/after comparison)")
    p.add_argument("--selftest", action="store_true", help="verify the checks with built-in good and bad replies")
    args = p.parse_args(argv)

    try:
        scenarios = load_scenarios()
        if args.scenario:
            unknown = set(args.scenario) - {s["id"] for s in scenarios}
            if unknown:
                print(f"error: unknown scenario(s): {sorted(unknown)}", file=sys.stderr)
                return 2
            scenarios = [s for s in scenarios if s["id"] in args.scenario]

        if args.selftest:
            problems = selftest(scenarios)
            for problem in problems:
                print(f"SELFTEST FAIL  {problem}")
            checks = sum(1 + len(s.get("bad_replies", [])) for s in scenarios)
            print(f"selftest: {checks - len(problems)}/{checks} expectations held across {len(scenarios)} scenarios")
            return 1 if problems else 0

        if args.runs < 1:
            print("error: --runs must be at least 1", file=sys.stderr)
            return 2
        model = get_model(args.model)
        report = evaluate(model, scenarios, args.runs, harden=not args.no_hardening)
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(format_report(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report_to_dict(report), indent=2))
        print(f"results written to {args.json}")
    if report.errors:
        return 2
    if args.min_score is not None and report.score < args.min_score:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
