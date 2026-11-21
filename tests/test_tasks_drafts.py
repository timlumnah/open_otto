import re

import pytest

from core import drafts, tasks
from core.pipeline import PipelineError


# ---- tasks ----

def test_parse_due_formats():
    assert tasks.parse_due(None) is None
    assert tasks.parse_due("2030-01-05") == "2030-01-05 00:00:00"
    assert tasks.parse_due("+3d") == "+3 days"


@pytest.mark.parametrize("bad", ["tomorrow", "2030-13-40", "3d", "+d", "2030/01/05"])
def test_parse_due_rejects_garbage(bad):
    with pytest.raises(PipelineError, match="bad due date"):
        tasks.parse_due(bad)


def test_add_task_with_org_and_relative_due(demo_db):
    tid = tasks.add_task(demo_db, "Send press kit", "old mill", "+2d")
    row = demo_db.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert row["org_id"] is not None
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", row["due_at"])
    assert demo_db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'task_created' AND entity_id = ?", (tid,)
    ).fetchone()[0] == 1


def test_add_task_without_org_or_due(demo_db):
    tid = tasks.add_task(demo_db, "Renew insurance")
    row = demo_db.execute("SELECT org_id, due_at FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert (row["org_id"], row["due_at"]) == (None, None)


def test_add_task_validates(demo_db):
    with pytest.raises(PipelineError, match="title is required"):
        tasks.add_task(demo_db, "  ")
    with pytest.raises(PipelineError, match="no org matches"):
        tasks.add_task(demo_db, "x", "zzzz")


def test_complete_and_drop_flow(demo_db):
    tasks.complete_task(demo_db, 1)
    row = demo_db.execute("SELECT status, completed_at FROM tasks WHERE id = 1").fetchone()
    assert row["status"] == "done" and row["completed_at"]
    with pytest.raises(PipelineError, match="already done"):
        tasks.complete_task(demo_db, 1)
    tasks.drop_task(demo_db, 2)
    assert demo_db.execute("SELECT status FROM tasks WHERE id = 2").fetchone()[0] == "dropped"
    with pytest.raises(PipelineError, match="no task #999"):
        tasks.complete_task(demo_db, 999)


def test_list_tasks_orders_by_due_and_flags_overdue(demo_db):
    rows = tasks.list_tasks(demo_db)
    assert [r["title"] for r in rows][0].startswith("Ask Greg")  # due 2 days ago
    assert rows[0]["overdue"] == 1
    assert all(r["overdue"] == 0 for r in rows[1:])


def test_list_tasks_hides_closed_unless_asked(demo_db):
    tasks.complete_task(demo_db, 1)
    assert len(tasks.list_tasks(demo_db)) == 2
    assert len(tasks.list_tasks(demo_db, include_closed=True)) == 3


def test_undated_tasks_sort_last(demo_db):
    tasks.add_task(demo_db, "Someday")
    assert tasks.list_tasks(demo_db)[-1]["title"] == "Someday"


# ---- draft queue ----

def test_list_drafts_default_pending(demo_db):
    rows = drafts.list_drafts(demo_db)
    assert [r["org"] for r in rows] == ["Copper Kettle Cafe"]
    assert drafts.list_drafts(demo_db, "sent") == []
    assert len(drafts.list_drafts(demo_db, None)) == 1


def test_get_draft_joins_contact(demo_db):
    d = drafts.get_draft(demo_db, 1)
    assert d["org"] == "Copper Kettle Cafe" and d["contact"] == "Marcus Oyelaran"


def test_missing_draft(demo_db):
    for fn in (drafts.get_draft, drafts.approve, drafts.reject, drafts.mark_sent):
        with pytest.raises(PipelineError, match="no draft #99"):
            fn(demo_db, 99)


def test_approve_with_edit_keeps_original_body(demo_db):
    original = demo_db.execute("SELECT body FROM drafts WHERE id = 1").fetchone()[0]
    drafts.approve(demo_db, 1, edit="  My own wording.  ")
    row = demo_db.execute("SELECT status, body, final_body, decided_at FROM drafts WHERE id = 1").fetchone()
    assert row["status"] == "approved" and row["decided_at"]
    assert row["final_body"] == "My own wording."
    assert row["body"] == original  # agent's text preserved for the record


def test_approve_rejects_blank_edit(demo_db):
    with pytest.raises(PipelineError, match="cannot be empty"):
        drafts.approve(demo_db, 1, edit="   ")


def test_reject_flow_and_no_second_decision(demo_db):
    drafts.reject(demo_db, 1)
    assert demo_db.execute("SELECT status FROM drafts WHERE id = 1").fetchone()[0] == "rejected"
    with pytest.raises(PipelineError, match="only pending"):
        drafts.approve(demo_db, 1)
    with pytest.raises(PipelineError, match="only pending"):
        drafts.reject(demo_db, 1)


def test_cannot_send_unapproved(demo_db):
    with pytest.raises(PipelineError, match="approve it before"):
        drafts.mark_sent(demo_db, 1)


def test_sent_logs_interaction_linked_to_draft(demo_db):
    drafts.approve(demo_db, 1)
    iid = drafts.mark_sent(demo_db, 1)
    row = demo_db.execute("SELECT * FROM interactions WHERE id = ?", (iid,)).fetchone()
    assert (row["draft_id"], row["direction"], row["channel"]) == (1, "outbound", "email")
    assert "Sent draft #1" in row["summary"]
    assert demo_db.execute("SELECT status, sent_at FROM drafts WHERE id = 1").fetchone()["sent_at"]


def test_cannot_send_twice(demo_db):
    drafts.approve(demo_db, 1)
    drafts.mark_sent(demo_db, 1)
    with pytest.raises(PipelineError, match="is sent"):
        drafts.mark_sent(demo_db, 1)


def test_full_lifecycle_audit_trail(demo_db):
    drafts.approve(demo_db, 1)
    drafts.mark_sent(demo_db, 1)
    actions = [r[0] for r in demo_db.execute(
        "SELECT action FROM audit_log WHERE entity_type = 'draft' ORDER BY id")]
    assert actions == ["draft_approved", "draft_sent"]


def test_sending_resets_the_followup_clock(demo_db):
    from core import pipeline
    drafts.approve(demo_db, 1)
    drafts.mark_sent(demo_db, 1)
    assert "Copper Kettle Cafe" not in {r["name"] for r in pipeline.followups(demo_db)}
