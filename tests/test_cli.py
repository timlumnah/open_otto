import pytest

from cli.main import main


@pytest.fixture
def dbfile(tmp_path):
    return str(tmp_path / "test.db")


def run(dbfile, *argv):
    return main(["--db", dbfile, *argv])


def test_commands_before_init_give_a_clear_error(dbfile, capsys):
    assert run(dbfile, "list") == 2
    assert "Run: python -m cli init" in capsys.readouterr().err


def test_init_then_init_again_is_safe(dbfile, capsys):
    assert run(dbfile, "init") == 0
    assert run(dbfile, "init") == 0
    assert "already up to date" in capsys.readouterr().out


def test_init_demo_then_followups(dbfile, capsys):
    assert run(dbfile, "init", "--demo") == 0
    capsys.readouterr()
    assert run(dbfile, "followups") == 0
    out = capsys.readouterr().out
    assert "Lantern Hill Winery" in out and "22d" in out


def test_demo_refused_on_populated_database(dbfile, capsys):
    run(dbfile, "init", "--demo")
    assert run(dbfile, "init", "--demo") == 2
    assert "already has orgs" in capsys.readouterr().err


def test_import_log_stage_flow(dbfile, tmp_path, capsys):
    run(dbfile, "init")
    csv_path = tmp_path / "v.csv"
    csv_path.write_text("name,city,contact_name\nTest Hall,Riverton,Sam\n")
    assert run(dbfile, "import", "gigs", str(csv_path)) == 0
    assert run(dbfile, "log", "Test Hall", "email", "outbound", "Sent pitch") == 0
    assert run(dbfile, "stage", "Test Hall", "pitched") == 0
    capsys.readouterr()
    run(dbfile, "list", "--stage", "pitched")
    assert "Test Hall" in capsys.readouterr().out


def test_import_with_bad_rows_exits_nonzero_but_keeps_good(dbfile, tmp_path, capsys):
    run(dbfile, "init")
    p = tmp_path / "v.csv"
    p.write_text("name,stage\nGood,prospect\nBad,warp\n")
    assert run(dbfile, "import", "gigs", str(p)) == 1
    captured = capsys.readouterr()
    assert "created 1" in captured.out and "line 3" in captured.err


def test_unknown_org_is_exit_2_not_a_traceback(dbfile, capsys):
    run(dbfile, "init")
    assert run(dbfile, "stage", "Nowhere", "booked") == 2
    assert "no org matches" in capsys.readouterr().err


def test_env_var_selects_database(dbfile, monkeypatch, capsys):
    monkeypatch.setenv("OPEN_OTTO_DB", dbfile)
    assert main(["init", "--demo"]) == 0
    assert main(["list"]) == 0
    assert "Blue Heron" in capsys.readouterr().out


# ---- tasks, draft queue, agent ----

import json

from agent.models import FakeModel

GOOD = json.dumps({"subject": "Checking in", "body": "Hi, just checking in on the Thursday dates."})


@pytest.fixture
def demo(dbfile):
    run(dbfile, "init", "--demo")
    return dbfile


def test_task_cli_flow(demo, capsys):
    capsys.readouterr()
    assert run(demo, "task", "add", "Call Dana", "--org", "heron", "--due", "+1d") == 0
    assert run(demo, "task", "list") == 0
    out = capsys.readouterr().out
    assert "Call Dana [The Blue Heron Room]" in out and "OVERDUE" in out  # the seeded Greg task
    assert run(demo, "task", "done", "1") == 0
    assert run(demo, "task", "done", "1") == 2


def test_task_bad_due_is_exit_2(demo, capsys):
    assert run(demo, "task", "add", "x", "--due", "tomorrow") == 2
    assert "bad due date" in capsys.readouterr().err


def test_draft_queue_cli_flow(demo, capsys):
    capsys.readouterr()
    assert run(demo, "drafts") == 0
    assert "Copper Kettle Cafe" in capsys.readouterr().out
    assert run(demo, "show", "1") == 0
    assert "Marcus Oyelaran <marcus@example.com>" in capsys.readouterr().out
    assert run(demo, "sent", "1") == 2  # not approved yet
    assert run(demo, "approve", "1", "--edit", "My words.") == 0
    assert run(demo, "show", "1") == 0
    out = capsys.readouterr().out
    assert "My words." in out and "edited by you" in out
    assert run(demo, "sent", "1") == 0
    assert run(demo, "drafts", "--all") == 0
    assert "sent" in capsys.readouterr().out


def test_reject_cli(demo, capsys):
    assert run(demo, "reject", "1") == 0
    assert run(demo, "reject", "1") == 2


def test_draft_command_queues_pending_draft(demo, monkeypatch, capsys):
    monkeypatch.setattr("cli.main.get_model", lambda spec=None: FakeModel(GOOD, name="fake"))
    capsys.readouterr()
    assert run(demo, "draft", "Maple Street") == 0
    assert "queued as pending" in capsys.readouterr().out
    assert run(demo, "draft", "Maple Street") == 2  # already pending
    assert "already has pending draft" in capsys.readouterr().err


def test_draft_followups_command(demo, monkeypatch, capsys):
    monkeypatch.setattr("cli.main.get_model", lambda spec=None: FakeModel(GOOD))
    capsys.readouterr()
    assert run(demo, "draft-followups") == 0
    assert "Lantern Hill Winery: draft #" in capsys.readouterr().out
    assert run(demo, "draft-followups") == 0
    assert "no follow-ups due" in capsys.readouterr().out  # now covered by a pending draft


def test_draft_followups_failure_exits_nonzero(demo, monkeypatch, capsys):
    monkeypatch.setattr("cli.main.get_model", lambda spec=None: FakeModel("not json"))
    assert run(demo, "draft-followups") == 1
    assert "FAILED" in capsys.readouterr().out


def test_draft_without_model_configured_is_a_clean_error(demo, monkeypatch, capsys):
    monkeypatch.delenv("OPEN_OTTO_MODEL", raising=False)
    assert run(demo, "draft", "Maple Street") == 2
    assert "no model configured" in capsys.readouterr().err


def test_voice_file_reaches_the_model(demo, monkeypatch, tmp_path):
    voice = tmp_path / "voice.md"
    voice.write_text("I am a jazz guitarist from Riverton.")
    model = FakeModel(GOOD)
    monkeypatch.setattr("cli.main.get_model", lambda spec=None: model)
    assert run(demo, "draft", "Maple Street", "--voice", str(voice)) == 0
    assert "jazz guitarist from Riverton" in model.calls[0][1]


def test_missing_voice_file_is_exit_2(demo, monkeypatch, capsys):
    monkeypatch.setattr("cli.main.get_model", lambda spec=None: FakeModel(GOOD))
    assert run(demo, "draft", "Maple Street", "--voice", "/nope/voice.md") == 2
    assert "voice file not found" in capsys.readouterr().err
