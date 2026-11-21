"""Pipeline operations: adding orgs, importing CSV, moving stages, logging contact.

Every function takes an open connection and commits its own work. Anything that
changes the record also writes an audit_log row.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

CHANNELS = ("email", "phone", "text", "social", "in_person", "other")
DIRECTIONS = ("outbound", "inbound")

CSV_ORG_COLUMNS = ("kind", "website", "city", "region", "notes", "source")


class PipelineError(Exception):
    """A user-facing problem: bad input, unknown org, unknown stage."""


@dataclass
class ImportResult:
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _audit(conn, actor, action, entity_type, entity_id, detail=None):
    conn.execute(
        "INSERT INTO audit_log (actor, action, entity_type, entity_id, detail) VALUES (?, ?, ?, ?, ?)",
        (actor, action, entity_type, entity_id, json.dumps(detail) if detail is not None else None),
    )


def packs(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT pack FROM pipeline_stages ORDER BY pack")]


def stage_keys(conn, pack: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT key FROM pipeline_stages WHERE pack = ? ORDER BY position", (pack,)
        )
    ]


def _stage_id(conn, pack: str, key: str) -> int:
    row = conn.execute(
        "SELECT id FROM pipeline_stages WHERE pack = ? AND key = ?", (pack, key)
    ).fetchone()
    if row is None:
        valid = ", ".join(stage_keys(conn, pack)) or "(pack has no stages)"
        raise PipelineError(f"unknown stage '{key}' for pack '{pack}'. Valid: {valid}")
    return row[0]


def find_org(conn, name: str, pack: str | None = None) -> sqlite3.Row:
    """Find one org by case-insensitive exact name, then by unique substring."""
    clause, args = ("AND pack = ?", [pack]) if pack else ("", [])
    rows = conn.execute(
        f"SELECT * FROM orgs WHERE lower(name) = lower(?) {clause}", [name, *args]
    ).fetchall()
    if not rows:
        rows = conn.execute(
            f"SELECT * FROM orgs WHERE lower(name) LIKE '%' || lower(?) || '%' {clause}",
            [name, *args],
        ).fetchall()
    if not rows:
        raise PipelineError(f"no org matches '{name}'")
    if len(rows) > 1:
        names = ", ".join(f"{r['name']} ({r['city'] or 'no city'})" for r in rows)
        raise PipelineError(f"'{name}' is ambiguous: {names}")
    return rows[0]


def add_org(
    conn,
    pack: str,
    name: str,
    *,
    stage: str | None = None,
    contact: dict | None = None,
    actor: str = "user",
    **fields,
) -> int:
    """Create an org (and optional primary contact). Returns the org id."""
    name = (name or "").strip()
    if not name:
        raise PipelineError("org name is required")
    if pack not in packs(conn):
        raise PipelineError(f"unknown pack '{pack}'. Valid: {', '.join(packs(conn))}")
    stage_key = stage or stage_keys(conn, pack)[0]
    stage_id = _stage_id(conn, pack, stage_key)
    unknown = set(fields) - set(CSV_ORG_COLUMNS)
    if unknown:
        raise PipelineError(f"unknown org fields: {sorted(unknown)}")
    cols = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in fields.items()}

    try:
        with conn:
            cur = conn.execute(
                f"INSERT INTO orgs (pack, name, stage_id{''.join(', ' + c for c in cols)}) "
                f"VALUES (?, ?, ?{', ?' * len(cols)})",
                [pack, name, stage_id, *cols.values()],
            )
            org_id = cur.lastrowid
            if contact and (contact.get("name") or "").strip():
                conn.execute(
                    "INSERT INTO contacts (org_id, name, role, email, phone, is_primary) "
                    "VALUES (?, ?, ?, ?, ?, 1)",
                    (
                        org_id,
                        contact["name"].strip(),
                        (contact.get("role") or "").strip() or None,
                        (contact.get("email") or "").strip() or None,
                        (contact.get("phone") or "").strip() or None,
                    ),
                )
            _audit(conn, actor, "org_created", "org", org_id, {"pack": pack, "name": name})
    except sqlite3.IntegrityError as exc:
        raise PipelineError(f"could not add '{name}': {exc}") from exc
    return org_id


def import_csv(conn, pack: str, path: str | Path) -> ImportResult:
    """Import orgs from a CSV. Existing orgs (same pack, name, city) are skipped.

    Required column: name. Optional: kind, website, city, region, notes, source,
    stage, contact_name, contact_role, contact_email, contact_phone.
    A bad row is reported and skipped; it does not abort the rest.
    """
    result = ImportResult()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "name" not in [h.strip().lower() for h in reader.fieldnames]:
            raise PipelineError("CSV must have a 'name' column")
        for line_no, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            name = row.get("name", "")
            if not name:
                result.errors.append(f"line {line_no}: missing name")
                continue
            city = row.get("city") or None
            exists = conn.execute(
                "SELECT 1 FROM orgs WHERE pack = ? AND name = ? AND city IS ?", (pack, name, city)
            ).fetchone()
            if exists:
                result.skipped += 1
                continue
            fields = {c: row.get(c, "") for c in CSV_ORG_COLUMNS}
            fields["source"] = fields["source"] or "csv"
            contact = {
                "name": row.get("contact_name", ""),
                "role": row.get("contact_role", ""),
                "email": row.get("contact_email", ""),
                "phone": row.get("contact_phone", ""),
            }
            try:
                add_org(
                    conn, pack, name, stage=row.get("stage") or None,
                    contact=contact, actor="import", **fields,
                )
                result.created += 1
            except PipelineError as exc:
                result.errors.append(f"line {line_no}: {exc}")
    return result


def move_stage(conn, org_name: str, stage_key: str, pack: str | None = None) -> sqlite3.Row:
    """Move an org to another stage in its own pack. The stage-change trigger audits it."""
    org = find_org(conn, org_name, pack)
    new_id = _stage_id(conn, org["pack"], stage_key)
    if new_id == org["stage_id"]:
        raise PipelineError(f"{org['name']} is already in '{stage_key}'")
    with conn:
        conn.execute("UPDATE orgs SET stage_id = ? WHERE id = ?", (new_id, org["id"]))
    return find_org(conn, org["name"], org["pack"])


def log_interaction(
    conn,
    org_name: str,
    channel: str,
    direction: str,
    summary: str,
    pack: str | None = None,
) -> int:
    """Record something that actually happened with an org. Returns the interaction id."""
    if channel not in CHANNELS:
        raise PipelineError(f"unknown channel '{channel}'. Valid: {', '.join(CHANNELS)}")
    if direction not in DIRECTIONS:
        raise PipelineError(f"unknown direction '{direction}'. Valid: {', '.join(DIRECTIONS)}")
    if not (summary or "").strip():
        raise PipelineError("summary is required")
    org = find_org(conn, org_name, pack)
    contact = conn.execute(
        "SELECT id FROM contacts WHERE org_id = ? AND is_primary = 1", (org["id"],)
    ).fetchone()
    with conn:
        cur = conn.execute(
            "INSERT INTO interactions (org_id, contact_id, channel, direction, summary) "
            "VALUES (?, ?, ?, ?, ?)",
            (org["id"], contact[0] if contact else None, channel, direction, summary.strip()),
        )
        _audit(conn, "user", "interaction_logged", "interaction", cur.lastrowid,
               {"org_id": org["id"], "channel": channel, "direction": direction})
    return cur.lastrowid


def list_orgs(conn, pack: str | None = None, stage: str | None = None) -> list[sqlite3.Row]:
    where, args = [], []
    if pack:
        where.append("a.pack = ?")
        args.append(pack)
    if stage:
        where.append("a.stage_key = ?")
        args.append(stage)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return conn.execute(
        f"SELECT a.name, a.pack, a.stage_key, a.days_silent, o.city "
        f"FROM v_org_activity a JOIN orgs o ON o.id = a.org_id {clause} "
        f"ORDER BY a.pack, a.stage_key, a.name",
        args,
    ).fetchall()


def followups(conn, pack: str | None = None, min_days: int = 7) -> list[sqlite3.Row]:
    """Orgs due for a follow-up. The view enforces a 7 day floor; min_days can only raise it."""
    clause, args = ("AND pack = ?", [pack]) if pack else ("", [])
    return conn.execute(
        f"SELECT * FROM v_followup_candidates WHERE days_silent >= ? {clause} "
        f"ORDER BY days_silent DESC",
        [min_days, *args],
    ).fetchall()
