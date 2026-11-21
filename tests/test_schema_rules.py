"""Tests that the database itself enforces open_otto's rules."""

import sqlite3

import pytest

from tests.conftest import stage_id


# ---- seed sanity ----

def test_pack_stages_cover_all_three_packs(staged_db):
    packs = {r[0] for r in staged_db.execute("SELECT DISTINCT pack FROM pipeline_stages")}
    assert packs == {"gigs", "jobs", "freelance"}


def test_every_pack_has_a_terminal_stage(staged_db):
    for pack in ("gigs", "jobs", "freelance"):
        n = staged_db.execute(
            "SELECT COUNT(*) FROM pipeline_stages WHERE pack = ? AND is_terminal = 1", (pack,)
        ).fetchone()[0]
        assert n >= 1, pack


def test_demo_seed_counts(demo_db):
    counts = {
        t: demo_db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("orgs", "contacts", "interactions", "tasks", "drafts")
    }
    assert counts == {"orgs": 8, "contacts": 7, "interactions": 10, "tasks": 3, "drafts": 1}


def test_demo_has_no_orphans(demo_db):
    assert demo_db.execute("PRAGMA foreign_key_check").fetchall() == []


# ---- audit log is append-only ----

def test_audit_log_blocks_update(demo_db):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        demo_db.execute("UPDATE audit_log SET actor = 'someone_else'")


def test_audit_log_blocks_delete(demo_db):
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        demo_db.execute("DELETE FROM audit_log")


def test_audit_detail_must_be_valid_json(empty_db):
    with pytest.raises(sqlite3.IntegrityError):
        empty_db.execute(
            "INSERT INTO audit_log (actor, action, entity_type, detail) VALUES ('t', 'a', 'x', 'not json')"
        )


# ---- draft approval gate ----

def test_cannot_approve_without_decided_at(demo_db):
    with pytest.raises(sqlite3.IntegrityError):
        demo_db.execute("UPDATE drafts SET status = 'approved' WHERE id = 1")


def test_cannot_mark_sent_without_sent_at(demo_db):
    with pytest.raises(sqlite3.IntegrityError):
        demo_db.execute(
            "UPDATE drafts SET status = 'sent', decided_at = datetime('now') WHERE id = 1"
        )


def test_invalid_draft_status_rejected(demo_db):
    with pytest.raises(sqlite3.IntegrityError):
        demo_db.execute("UPDATE drafts SET status = 'yolo' WHERE id = 1")


def test_agent_draft_starts_pending(demo_db):
    row = demo_db.execute("SELECT status, created_by FROM drafts WHERE id = 1").fetchone()
    assert (row["status"], row["created_by"]) == ("pending", "agent")


def test_full_approve_and_send_flow_is_audited(demo_db):
    demo_db.execute("UPDATE drafts SET status = 'approved', decided_at = datetime('now') WHERE id = 1")
    demo_db.execute("UPDATE drafts SET status = 'sent', sent_at = datetime('now') WHERE id = 1")
    actions = [
        r[0] for r in demo_db.execute(
            "SELECT action FROM audit_log WHERE entity_type = 'draft' ORDER BY id"
        )
    ]
    assert actions == ["draft_approved", "draft_sent"]


def test_sent_draft_body_is_immutable(demo_db):
    demo_db.execute("UPDATE drafts SET status = 'approved', decided_at = datetime('now') WHERE id = 1")
    demo_db.execute("UPDATE drafts SET status = 'sent', sent_at = datetime('now') WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        demo_db.execute("UPDATE drafts SET body = 'rewritten history' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        demo_db.execute("UPDATE drafts SET final_body = 'rewritten history' WHERE id = 1")


def test_unsent_draft_body_is_editable(demo_db):
    demo_db.execute("UPDATE drafts SET body = 'edited' WHERE id = 1")
    assert demo_db.execute("SELECT body FROM drafts WHERE id = 1").fetchone()[0] == "edited"


# ---- stage changes ----

def test_stage_change_is_audited_and_stamped(demo_db):
    org = demo_db.execute("SELECT id, stage_changed_at FROM orgs WHERE name = 'Copper Kettle Cafe'").fetchone()
    booked = stage_id(demo_db, "gigs", "booked")
    demo_db.execute("UPDATE orgs SET stage_id = ? WHERE id = ?", (booked, org["id"]))
    row = demo_db.execute(
        "SELECT actor, detail FROM audit_log WHERE action = 'stage_changed' AND entity_id = ?", (org["id"],)
    ).fetchone()
    assert row["actor"] == "system"
    assert f'"to":{booked}' in row["detail"]
    new_ts = demo_db.execute("SELECT stage_changed_at FROM orgs WHERE id = ?", (org["id"],)).fetchone()[0]
    assert new_ts >= org["stage_changed_at"]


def test_non_stage_update_writes_no_audit_row(demo_db):
    before = demo_db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    demo_db.execute("UPDATE orgs SET notes = 'tweaked' WHERE name = 'Copper Kettle Cafe'")
    after = demo_db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert before == after


def test_org_without_valid_stage_rejected(empty_db):
    with pytest.raises(sqlite3.IntegrityError):
        empty_db.execute("INSERT INTO orgs (pack, name, stage_id) VALUES ('gigs', 'Nowhere', 9999)")


def test_org_unique_per_pack_name_city(staged_db):
    s = stage_id(staged_db, "gigs", "prospect")
    staged_db.execute("INSERT INTO orgs (pack, name, city, stage_id) VALUES ('gigs', 'Dup', 'X', ?)", (s,))
    with pytest.raises(sqlite3.IntegrityError):
        staged_db.execute("INSERT INTO orgs (pack, name, city, stage_id) VALUES ('gigs', 'Dup', 'X', ?)", (s,))


# ---- contacts, tasks, interactions ----

def test_one_primary_contact_per_org(demo_db):
    org_id = demo_db.execute("SELECT id FROM orgs WHERE name = 'Copper Kettle Cafe'").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        demo_db.execute("INSERT INTO contacts (org_id, name, is_primary) VALUES (?, 'Second', 1)", (org_id,))


def test_many_non_primary_contacts_allowed(demo_db):
    org_id = demo_db.execute("SELECT id FROM orgs WHERE name = 'Copper Kettle Cafe'").fetchone()[0]
    for n in ("A", "B"):
        demo_db.execute("INSERT INTO contacts (org_id, name) VALUES (?, ?)", (org_id, n))


def test_closed_task_requires_completed_at(demo_db):
    with pytest.raises(sqlite3.IntegrityError):
        demo_db.execute("UPDATE tasks SET status = 'done' WHERE id = 1")


def test_closing_task_with_timestamp_works(demo_db):
    demo_db.execute("UPDATE tasks SET status = 'done', completed_at = datetime('now') WHERE id = 1")


def test_interaction_rejects_bad_channel_and_direction(demo_db):
    org_id = demo_db.execute("SELECT id FROM orgs LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        demo_db.execute(
            "INSERT INTO interactions (org_id, channel, direction, summary) VALUES (?, 'carrier_pigeon', 'outbound', 'x')",
            (org_id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        demo_db.execute(
            "INSERT INTO interactions (org_id, channel, direction, summary) VALUES (?, 'email', 'sideways', 'x')",
            (org_id,),
        )


def test_deleting_org_cascades_but_keeps_audit(demo_db):
    org_id = demo_db.execute("SELECT id FROM orgs WHERE name = 'Copper Kettle Cafe'").fetchone()[0]
    audit_before = demo_db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    demo_db.execute("DELETE FROM orgs WHERE id = ?", (org_id,))
    for table in ("contacts", "interactions", "tasks", "drafts"):
        n = demo_db.execute(f"SELECT COUNT(*) FROM {table} WHERE org_id = ?", (org_id,)).fetchone()[0]
        assert n == 0, table
    assert demo_db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audit_before


# ---- follow-up views ----

def names(conn, view):
    return {r["name"] for r in conn.execute(f"SELECT name FROM {view}")}


def test_followup_candidates_in_demo(demo_db):
    assert names(demo_db, "v_followup_candidates") == {"Lantern Hill Winery"}


def test_pending_draft_suppresses_followup(demo_db):
    # Copper Kettle is 14 days silent but has a pending draft.
    assert "Copper Kettle Cafe" not in names(demo_db, "v_followup_candidates")
    demo_db.execute("UPDATE drafts SET status = 'rejected', decided_at = datetime('now') WHERE id = 1")
    assert "Copper Kettle Cafe" in names(demo_db, "v_followup_candidates")


def test_open_task_suppresses_followup(demo_db):
    org_id = demo_db.execute("SELECT id FROM orgs WHERE name = 'Lantern Hill Winery'").fetchone()[0]
    demo_db.execute("INSERT INTO tasks (org_id, title) VALUES (?, 'already on it')", (org_id,))
    assert "Lantern Hill Winery" not in names(demo_db, "v_followup_candidates")


def test_inbound_last_means_ball_is_ours_not_silent(demo_db):
    # Blue Heron: Dana replied last, so this is not a "they ghosted us" case.
    assert "The Blue Heron Room" not in names(demo_db, "v_followup_candidates")


def test_terminal_stage_never_a_followup(demo_db):
    assert "Pinecrest Golf Club" not in names(demo_db, "v_followup_candidates")


def test_org_with_no_outbound_is_not_a_followup(demo_db):
    # Never contacted yet: nothing to follow up on.
    assert "Maple Street Taproom" not in names(demo_db, "v_followup_candidates")


def test_activity_view_days_silent(demo_db):
    row = demo_db.execute("SELECT days_silent FROM v_org_activity WHERE name = 'Lantern Hill Winery'").fetchone()
    assert row["days_silent"] == 22
