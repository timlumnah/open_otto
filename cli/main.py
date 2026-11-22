"""open_otto command line.

    python -m cli init [--demo]
    python -m cli import gigs venues.csv
    python -m cli list [--pack gigs] [--stage pitched]
    python -m cli followups [--pack gigs] [--days 10]
    python -m cli log "Blue Heron" email inbound "Dana confirmed Thursday"
    python -m cli stage "Blue Heron" booked

The database path comes from --db, then $OPEN_OTTO_DB, then ./open_otto.db.
"""

from __future__ import annotations

import argparse
import os
import sys

from agent import drafting
from agent.models import ModelError, get_model
from core import db, drafts, pipeline, tasks


def _db_path(arg: str | None) -> str:
    return arg or os.environ.get("OPEN_OTTO_DB") or "open_otto.db"


def _require_initialized(path: str) -> None:
    if path != ":memory:" and not os.path.exists(path):
        raise pipeline.PipelineError(f"no database at {path}. Run: python -m cli init")


def cmd_init(conn, args) -> int:
    applied = db.migrate(conn)
    has_stages = conn.execute("SELECT COUNT(*) FROM pipeline_stages").fetchone()[0]
    if not has_stages:
        db.run_seed(conn, db.DEFAULT_SEEDS_DIR / "001_pack_stages.sql")
    print(f"migrations applied: {applied or 'none (already up to date)'}")
    if args.demo:
        if conn.execute("SELECT COUNT(*) FROM orgs").fetchone()[0]:
            raise pipeline.PipelineError("refusing to load demo data into a database that already has orgs")
        db.run_seed(conn, db.DEFAULT_SEEDS_DIR / "demo_gigs.sql")
        print("demo data loaded (fictional gigs pack)")
    return 0


def cmd_import(conn, args) -> int:
    result = pipeline.import_csv(conn, args.pack, args.file)
    print(f"created {result.created}, skipped {result.skipped} existing, {len(result.errors)} errors")
    for err in result.errors:
        print(f"  {err}", file=sys.stderr)
    return 1 if result.errors else 0


def cmd_list(conn, args) -> int:
    rows = pipeline.list_orgs(conn, args.pack, args.stage)
    if not rows:
        print("nothing found")
        return 0
    for r in rows:
        where = f" ({r['city']})" if r["city"] else ""
        print(f"{r['pack']:<10} {r['stage_key']:<10} {r['name']}{where}  [{r['days_silent']}d]")
    return 0


def cmd_followups(conn, args) -> int:
    rows = pipeline.followups(conn, args.pack, args.days)
    if not rows:
        print("no follow-ups due")
        return 0
    for r in rows:
        print(f"{r['days_silent']:>4}d silent  {r['name']}  [{r['pack']}/{r['stage_key']}]")
    return 0


def cmd_log(conn, args) -> int:
    pipeline.log_interaction(conn, args.org, args.channel, args.direction, args.summary, args.pack)
    print("logged")
    return 0


def cmd_stage(conn, args) -> int:
    org = pipeline.move_stage(conn, args.org, args.stage, args.pack)
    print(f"{org['name']} -> {args.stage}")
    return 0


def cmd_task(conn, args) -> int:
    if args.task_cmd == "add":
        tid = tasks.add_task(conn, args.title, args.org, args.due)
        print(f"task #{tid} added")
    elif args.task_cmd == "done":
        tasks.complete_task(conn, args.id)
        print(f"task #{args.id} done")
    elif args.task_cmd == "drop":
        tasks.drop_task(conn, args.id)
        print(f"task #{args.id} dropped")
    else:
        rows = tasks.list_tasks(conn, args.all)
        if not rows:
            print("no tasks")
        for r in rows:
            due = (r["due_at"] or "no date")[:10]
            flag = "  OVERDUE" if r["overdue"] else ""
            who = f" [{r['org']}]" if r["org"] else ""
            print(f"#{r['id']:<3} {due}  {r['title']}{who}{flag}")
    return 0


def cmd_drafts(conn, args) -> int:
    rows = drafts.list_drafts(conn, None if args.all else args.status)
    if not rows:
        print("no drafts")
    for r in rows:
        print(f"#{r['id']:<3} {r['status']:<9} {r['org']}  ({r['created_by']}"
              f"{', ' + r['model'] if r['model'] else ''})  {r['subject'] or ''}")
    return 0


def cmd_show(conn, args) -> int:
    d = drafts.get_draft(conn, args.id)
    to = d["contact"] or "unknown contact"
    reach = d["contact_email"] or d["contact_phone"] or "no address on file"
    print(f"draft #{d['id']}  {d['status']}  {d['channel']}  by {d['created_by']}"
          f"{' (' + d['model'] + ')' if d['model'] else ''}")
    print(f"org: {d['org']}   to: {to} <{reach}>")
    if d["subject"]:
        print(f"subject: {d['subject']}")
    print()
    print(d["final_body"] or d["body"])
    if d["final_body"]:
        print("\n(edited by you before approval)")
    return 0


def cmd_approve(conn, args) -> int:
    drafts.approve(conn, args.id, args.edit)
    print(f"draft #{args.id} approved. Send it yourself, then run: python -m cli sent {args.id}")
    return 0


def cmd_reject(conn, args) -> int:
    drafts.reject(conn, args.id)
    print(f"draft #{args.id} rejected")
    return 0


def cmd_sent(conn, args) -> int:
    drafts.mark_sent(conn, args.id)
    print(f"draft #{args.id} recorded as sent and logged as an outbound interaction")
    return 0


def _voice(args) -> str | None:
    path = args.voice or os.environ.get("OPEN_OTTO_VOICE")
    if not path:
        return None
    if not os.path.exists(path):
        raise pipeline.PipelineError(f"voice file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extras():
    """Optional layers from the environment: OPEN_OTTO_GUARD_MODEL=provider:model, OPEN_OTTO_REPAIR=1."""
    spec = os.environ.get("OPEN_OTTO_GUARD_MODEL")
    return (get_model(spec) if spec else None), os.environ.get("OPEN_OTTO_REPAIR") == "1"


def cmd_draft(conn, args) -> int:
    model = get_model(args.model)
    guard, repair = _extras()
    did = drafting.draft_for_org(conn, model, args.org, _voice(args), args.force,
                                 guard=guard, repair=repair,
                                 on_warning=lambda m: print(f"warning: {m}", file=sys.stderr))
    print(f"draft #{did} queued as pending. Review: python -m cli show {did}")
    return 0


def cmd_draft_followups(conn, args) -> int:
    model = get_model(args.model)
    guard, repair = _extras()
    outcomes = drafting.draft_followups(conn, model, args.pack, _voice(args), args.days,
                                        guard=guard, repair=repair)
    if not outcomes:
        print("no follow-ups due")
        return 0
    for o in outcomes:
        print(f"{o.org}: draft #{o.draft_id}" if o.draft_id else f"{o.org}: FAILED {o.error}")
    return 1 if any(o.error for o in outcomes) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="open_otto", description=__doc__.split("\n")[0])
    p.add_argument("--db", help="database file (default: $OPEN_OTTO_DB or ./open_otto.db)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="create or update the database")
    s.add_argument("--demo", action="store_true", help="load fictional demo data")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("import", help="import orgs from a CSV")
    s.add_argument("pack")
    s.add_argument("file")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("list", help="list orgs")
    s.add_argument("--pack")
    s.add_argument("--stage")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("followups", help="who is due for a follow-up")
    s.add_argument("--pack")
    s.add_argument("--days", type=int, default=7, help="minimum days of silence (7 or more)")
    s.set_defaults(fn=cmd_followups)

    s = sub.add_parser("log", help="record an interaction")
    s.add_argument("org")
    s.add_argument("channel", choices=pipeline.CHANNELS)
    s.add_argument("direction", choices=pipeline.DIRECTIONS)
    s.add_argument("summary")
    s.add_argument("--pack")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("stage", help="move an org to another stage")
    s.add_argument("org")
    s.add_argument("stage")
    s.add_argument("--pack")
    s.set_defaults(fn=cmd_stage)

    s = sub.add_parser("task", help="manage tasks")
    tsub = s.add_subparsers(dest="task_cmd", required=True)
    t = tsub.add_parser("add")
    t.add_argument("title")
    t.add_argument("--org")
    t.add_argument("--due", help="YYYY-MM-DD or +Nd")
    t = tsub.add_parser("done")
    t.add_argument("id", type=int)
    t = tsub.add_parser("drop")
    t.add_argument("id", type=int)
    t = tsub.add_parser("list")
    t.add_argument("--all", action="store_true", help="include done and dropped")
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("drafts", help="list drafts (default: pending)")
    s.add_argument("--status", default="pending", choices=("pending", "approved", "rejected", "sent"))
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_drafts)

    s = sub.add_parser("show", help="show one draft")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("approve", help="approve a pending draft")
    s.add_argument("id", type=int)
    s.add_argument("--edit", help="replace the draft text with your own wording")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("reject", help="reject a pending draft")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_reject)

    s = sub.add_parser("sent", help="record that you sent an approved draft")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_sent)

    for name, fn, help_ in (
        ("draft", cmd_draft, "have the AI draft a follow-up for one org"),
        ("draft-followups", cmd_draft_followups, "draft for every org due a follow-up"),
    ):
        s = sub.add_parser(name, help=help_)
        if name == "draft":
            s.add_argument("org")
            s.add_argument("--force", action="store_true", help="draft even if one is already pending")
        else:
            s.add_argument("--pack")
            s.add_argument("--days", type=int, default=7)
        s.add_argument("--model", help="provider:model, or set $OPEN_OTTO_MODEL")
        s.add_argument("--voice", help="text file describing your voice and background, or $OPEN_OTTO_VOICE")
        s.set_defaults(fn=fn)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = _db_path(args.db)
    try:
        if args.command != "init":
            _require_initialized(path)
        conn = db.connect(path)
        try:
            return args.fn(conn, args)
        finally:
            conn.close()
    except (pipeline.PipelineError, db.MigrationError, drafting.DraftError, ModelError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
