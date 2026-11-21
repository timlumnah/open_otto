"""Tasks: things the user (or scheduler or agent) needs to do."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from core.pipeline import PipelineError, _audit, find_org

_REL_DAYS = re.compile(r"^\+(\d{1,4})d$")


def parse_due(text: str | None) -> str | None:
    """Accept YYYY-MM-DD or +Nd. Returns 'YYYY-MM-DD HH:MM:SS' (UTC), matching the seed format."""
    if not text:
        return None
    text = text.strip()
    rel = _REL_DAYS.match(text)
    if rel:
        return f"+{int(rel.group(1))} days"  # resolved by SQLite, see add_task
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise PipelineError(f"bad due date '{text}'. Use YYYY-MM-DD or +Nd (example: +3d)") from None
    return f"{text} 00:00:00"


def add_task(conn, title: str, org_name: str | None = None, due: str | None = None,
             created_by: str = "user") -> int:
    if not (title or "").strip():
        raise PipelineError("task title is required")
    org_id = find_org(conn, org_name)["id"] if org_name else None
    parsed = parse_due(due)
    with conn:
        if parsed and parsed.startswith("+"):
            due_sql, due_arg = "datetime('now', ?)", parsed
        else:
            due_sql, due_arg = "?", parsed
        cur = conn.execute(
            f"INSERT INTO tasks (org_id, title, due_at, created_by) VALUES (?, ?, {due_sql}, ?)",
            (org_id, title.strip(), due_arg, created_by),
        )
        _audit(conn, created_by, "task_created", "task", cur.lastrowid, {"org_id": org_id})
    return cur.lastrowid


def _close(conn, task_id: int, status: str) -> None:
    row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise PipelineError(f"no task #{task_id}")
    if row["status"] != "open":
        raise PipelineError(f"task #{task_id} is already {row['status']}")
    with conn:
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = datetime('now') WHERE id = ?",
            (status, task_id),
        )
        _audit(conn, "user", f"task_{status}", "task", task_id)


def complete_task(conn, task_id: int) -> None:
    _close(conn, task_id, "done")


def drop_task(conn, task_id: int) -> None:
    _close(conn, task_id, "dropped")


def list_tasks(conn, include_closed: bool = False) -> list[sqlite3.Row]:
    where = "" if include_closed else "WHERE t.status = 'open'"
    return conn.execute(
        f"SELECT t.id, t.title, t.status, t.due_at, o.name AS org, "
        f"       (t.status = 'open' AND t.due_at IS NOT NULL AND t.due_at < datetime('now')) AS overdue "
        f"FROM tasks t LEFT JOIN orgs o ON o.id = t.org_id {where} "
        f"ORDER BY t.due_at IS NULL, t.due_at, t.id"
    ).fetchall()
