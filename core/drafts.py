"""The draft queue. Nothing is sent by open_otto; the human sends, then records it here.

Lifecycle: pending -> approved -> sent, or pending -> rejected.
The database enforces that approval is recorded before anything counts as sent.
"""

from __future__ import annotations

import sqlite3

from core.pipeline import PipelineError, _audit


def _get(conn, draft_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if row is None:
        raise PipelineError(f"no draft #{draft_id}")
    return row


def get_draft(conn, draft_id: int) -> sqlite3.Row:
    """One draft with org and contact names joined in."""
    _get(conn, draft_id)
    return conn.execute(
        "SELECT d.*, o.name AS org, c.name AS contact, c.email AS contact_email, c.phone AS contact_phone "
        "FROM drafts d JOIN orgs o ON o.id = d.org_id LEFT JOIN contacts c ON c.id = d.contact_id "
        "WHERE d.id = ?",
        (draft_id,),
    ).fetchone()


def list_drafts(conn, status: str | None = "pending") -> list[sqlite3.Row]:
    where, args = ("WHERE d.status = ?", [status]) if status else ("", [])
    return conn.execute(
        f"SELECT d.id, d.status, d.channel, d.subject, d.created_by, d.model, d.created_at, o.name AS org "
        f"FROM drafts d JOIN orgs o ON o.id = d.org_id {where} ORDER BY d.id",
        args,
    ).fetchall()


def approve(conn, draft_id: int, edit: str | None = None) -> None:
    """Approve a pending draft, optionally replacing its text with the user's edit."""
    d = _get(conn, draft_id)
    if d["status"] != "pending":
        raise PipelineError(f"draft #{draft_id} is {d['status']}, only pending drafts can be approved")
    if edit is not None and not edit.strip():
        raise PipelineError("edited text cannot be empty")
    with conn:
        conn.execute(
            "UPDATE drafts SET status = 'approved', decided_at = datetime('now'), final_body = ? WHERE id = ?",
            (edit.strip() if edit else None, draft_id),
        )


def reject(conn, draft_id: int) -> None:
    d = _get(conn, draft_id)
    if d["status"] != "pending":
        raise PipelineError(f"draft #{draft_id} is {d['status']}, only pending drafts can be rejected")
    with conn:
        conn.execute(
            "UPDATE drafts SET status = 'rejected', decided_at = datetime('now') WHERE id = ?",
            (draft_id,),
        )


def mark_sent(conn, draft_id: int) -> int:
    """Record that the user sent an approved draft. Logs the outbound interaction. Returns its id."""
    d = _get(conn, draft_id)
    if d["status"] != "approved":
        raise PipelineError(
            f"draft #{draft_id} is {d['status']}; approve it before marking it sent"
        )
    label = d["subject"] or (d["final_body"] or d["body"])[:60]
    with conn:
        conn.execute(
            "UPDATE drafts SET status = 'sent', sent_at = datetime('now') WHERE id = ?", (draft_id,)
        )
        cur = conn.execute(
            "INSERT INTO interactions (org_id, contact_id, draft_id, channel, direction, summary) "
            "VALUES (?, ?, ?, ?, 'outbound', ?)",
            (d["org_id"], d["contact_id"], draft_id, d["channel"], f"Sent draft #{draft_id}: {label}"),
        )
        _audit(conn, "user", "interaction_logged", "interaction", cur.lastrowid,
               {"org_id": d["org_id"], "draft_id": draft_id})
    return cur.lastrowid
