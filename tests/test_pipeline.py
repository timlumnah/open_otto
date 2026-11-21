import pytest

from core import pipeline
from core.pipeline import PipelineError


def write_csv(tmp_path, text, name="in.csv"):
    p = tmp_path / name
    p.write_text(text)
    return p


# ---- add_org ----

def test_add_org_defaults_to_first_stage(staged_db):
    org_id = pipeline.add_org(staged_db, "gigs", "New Venue", city="Riverton")
    key = staged_db.execute(
        "SELECT s.key FROM orgs o JOIN pipeline_stages s ON s.id = o.stage_id WHERE o.id = ?", (org_id,)
    ).fetchone()[0]
    assert key == "prospect"


def test_add_org_with_primary_contact_and_audit(staged_db):
    org_id = pipeline.add_org(
        staged_db, "gigs", "V", contact={"name": "Pat", "email": "pat@example.com"}
    )
    c = staged_db.execute("SELECT name, is_primary FROM contacts WHERE org_id = ?", (org_id,)).fetchone()
    assert (c["name"], c["is_primary"]) == ("Pat", 1)
    assert staged_db.execute(
        "SELECT actor FROM audit_log WHERE action = 'org_created' AND entity_id = ?", (org_id,)
    ).fetchone()["actor"] == "user"


@pytest.mark.parametrize("kwargs,msg", [
    ({"pack": "nope", "name": "X"}, "unknown pack"),
    ({"pack": "gigs", "name": "  "}, "name is required"),
    ({"pack": "gigs", "name": "X", "stage": "warp"}, "unknown stage"),
    ({"pack": "gigs", "name": "X", "bogus": "1"}, "unknown org fields"),
])
def test_add_org_rejects_bad_input(staged_db, kwargs, msg):
    pack, name = kwargs.pop("pack"), kwargs.pop("name")
    with pytest.raises(PipelineError, match=msg):
        pipeline.add_org(staged_db, pack, name, **kwargs)


def test_add_org_duplicate_is_a_clean_error(staged_db):
    pipeline.add_org(staged_db, "gigs", "Dup", city="X")
    with pytest.raises(PipelineError, match="could not add"):
        pipeline.add_org(staged_db, "gigs", "Dup", city="X")


def test_failed_add_leaves_no_partial_rows(staged_db):
    pipeline.add_org(staged_db, "gigs", "Dup", city="X")
    before = staged_db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    with pytest.raises(PipelineError):
        pipeline.add_org(staged_db, "gigs", "Dup", city="X", contact={"name": "Ghost"})
    assert staged_db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == before


# ---- find_org ----

def test_find_org_exact_beats_substring(demo_db):
    assert pipeline.find_org(demo_db, "the blue heron room")["name"] == "The Blue Heron Room"


def test_find_org_unique_substring(demo_db):
    assert pipeline.find_org(demo_db, "heron")["name"] == "The Blue Heron Room"


def test_find_org_ambiguous(demo_db):
    with pytest.raises(PipelineError, match="ambiguous"):
        pipeline.find_org(demo_db, "e")


def test_find_org_missing(demo_db):
    with pytest.raises(PipelineError, match="no org matches"):
        pipeline.find_org(demo_db, "zzzz")


# ---- import_csv ----

def test_import_creates_orgs_and_contacts(staged_db, tmp_path):
    p = write_csv(tmp_path,
        "name,city,kind,contact_name,contact_email\n"
        "Venue A,Riverton,bar,Ann,ann@example.com\n"
        "Venue B,Eastford,cafe,,\n")
    r = pipeline.import_csv(staged_db, "gigs", p)
    assert (r.created, r.skipped, r.errors) == (2, 0, [])
    assert staged_db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 1
    assert staged_db.execute("SELECT source FROM orgs WHERE name = 'Venue A'").fetchone()[0] == "csv"
    assert staged_db.execute(
        "SELECT actor FROM audit_log WHERE action = 'org_created' LIMIT 1"
    ).fetchone()[0] == "import"


def test_import_is_idempotent(staged_db, tmp_path):
    p = write_csv(tmp_path, "name,city\nVenue A,Riverton\n")
    pipeline.import_csv(staged_db, "gigs", p)
    r = pipeline.import_csv(staged_db, "gigs", p)
    assert (r.created, r.skipped) == (0, 1)


def test_import_dedupes_orgs_with_no_city(staged_db, tmp_path):
    p = write_csv(tmp_path, "name\nNo City Venue\n")
    pipeline.import_csv(staged_db, "gigs", p)
    assert pipeline.import_csv(staged_db, "gigs", p).skipped == 1


def test_import_bad_rows_reported_good_rows_kept(staged_db, tmp_path):
    p = write_csv(tmp_path,
        "name,stage\n"
        "Good,prospect\n"
        ",prospect\n"
        "BadStage,warp\n"
        "AlsoGood,booked\n")
    r = pipeline.import_csv(staged_db, "gigs", p)
    assert r.created == 2
    assert len(r.errors) == 2
    assert "line 3" in r.errors[0] and "line 4" in r.errors[1]


def test_import_requires_name_column(staged_db, tmp_path):
    p = write_csv(tmp_path, "city\nRiverton\n")
    with pytest.raises(PipelineError, match="'name' column"):
        pipeline.import_csv(staged_db, "gigs", p)


def test_import_handles_bom_and_header_case(staged_db, tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes("﻿Name,City\nVenue A,Riverton\n".encode("utf-8"))
    assert pipeline.import_csv(staged_db, "gigs", p).created == 1


# ---- move_stage ----

def test_move_stage_audits_via_trigger(demo_db):
    pipeline.move_stage(demo_db, "Copper Kettle", "booked")
    assert demo_db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'stage_changed'"
    ).fetchone()[0] == 1


def test_move_stage_same_stage_rejected(demo_db):
    with pytest.raises(PipelineError, match="already in"):
        pipeline.move_stage(demo_db, "Copper Kettle", "pitched")


def test_move_stage_uses_orgs_own_pack(demo_db):
    with pytest.raises(PipelineError, match="unknown stage 'interview' for pack 'gigs'"):
        pipeline.move_stage(demo_db, "Copper Kettle", "interview")


# ---- log_interaction ----

def test_log_interaction_attaches_primary_contact_and_audits(demo_db):
    iid = pipeline.log_interaction(demo_db, "Maple Street", "email", "outbound", "Sent pitch")
    row = demo_db.execute("SELECT contact_id FROM interactions WHERE id = ?", (iid,)).fetchone()
    assert row["contact_id"] is not None
    assert demo_db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'interaction_logged'"
    ).fetchone()[0] == 1


def test_logging_outbound_makes_org_a_followup_after_silence(demo_db):
    pipeline.log_interaction(demo_db, "Maple Street", "email", "outbound", "Sent pitch")
    assert "Maple Street Taproom" not in {r["name"] for r in pipeline.followups(demo_db)}
    demo_db.execute(
        "UPDATE interactions SET occurred_at = datetime('now', '-9 days') WHERE summary = 'Sent pitch'"
    )
    assert "Maple Street Taproom" in {r["name"] for r in pipeline.followups(demo_db)}


@pytest.mark.parametrize("args,msg", [
    (("carrier_pigeon", "outbound", "x"), "unknown channel"),
    (("email", "sideways", "x"), "unknown direction"),
    (("email", "outbound", "   "), "summary is required"),
])
def test_log_interaction_validates(demo_db, args, msg):
    with pytest.raises(PipelineError, match=msg):
        pipeline.log_interaction(demo_db, "Maple Street", *args)


# ---- list / followups ----

def test_list_filters(demo_db):
    assert len(pipeline.list_orgs(demo_db)) == 8
    assert {r["name"] for r in pipeline.list_orgs(demo_db, stage="pitched")} == {
        "Copper Kettle Cafe", "Lantern Hill Winery"
    }
    assert pipeline.list_orgs(demo_db, pack="jobs") == []


def test_followups_min_days_can_raise_threshold(demo_db):
    assert [r["name"] for r in pipeline.followups(demo_db)] == ["Lantern Hill Winery"]
    assert pipeline.followups(demo_db, min_days=30) == []
