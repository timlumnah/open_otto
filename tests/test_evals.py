import json

import pytest

from agent.models import FakeModel, ModelError
from evals import checks, harness
from evals.checks import CheckResult

CTX = "organization: Test\n- 2026-11-06 outbound email: Sent quote for $1,800, 3 pages. Wants 11am-1pm."


def d(body, subject="", channel="email"):
    return {"subject": subject, "body": body, "channel": channel}


# ---- the harness proves itself ----

def test_selftest_all_expectations_hold():
    assert harness.selftest(harness.load_scenarios()) == []


def test_scenarios_file_is_well_formed():
    scenarios = harness.load_scenarios()
    assert len(scenarios) >= 9
    for sc in scenarios:
        assert {"id", "pack", "org", "reference_reply"} <= sc.keys(), sc["id"]
        assert sc["bad_replies"] or sc["id"] == "", sc["id"]
        for bad in sc["bad_replies"]:
            assert bad["should_fail"] in {c.__name__ for c in checks.ALL_CHECKS}


def test_every_check_is_covered_by_some_bad_reply():
    # A check no adversarial reply exercises could be silently broken.
    covered = {b["should_fail"] for sc in harness.load_scenarios() for b in sc["bad_replies"]}
    assert covered == {c.__name__ for c in checks.ALL_CHECKS} - {"must_include_any", "expected_channel"}


def test_selftest_detects_a_broken_check(monkeypatch):
    # If a check stops catching its target, selftest must say so.
    monkeypatch.setattr(checks, "ALL_CHECKS", tuple(c for c in checks.ALL_CHECKS if c.__name__ != "has_ask"))
    monkeypatch.setattr(harness, "run_checks", checks.run_checks)
    problems = harness.selftest(harness.load_scenarios())
    assert any("has_ask" in p for p in problems)


# ---- individual checks ----

def test_numbers_in_record_allowed_invented_rejected():
    assert checks.no_invented_numbers(d("The quote was $1,800 for 3 pages."), {}, CTX).passed
    r = checks.no_invented_numbers(d("I can do it for $1,200."), {}, CTX)
    assert not r.passed and "1200" in r.detail


def test_numbers_normalization_and_trailing_comma():
    assert checks.no_invented_numbers(d("It was $1,800, as quoted."), {}, CTX).passed
    assert checks.numbers_in("06 and 6") == {"6"}


def test_relative_durations_exempt():
    assert checks.no_invented_numbers(d("It has been 2 weeks and 14 days."), {}, CTX).passed
    assert checks.no_invented_numbers(d("A 30-minute call?"), {}, CTX).passed
    assert not checks.no_invented_numbers(d("A 30 minute... no, 30 dollars."), {}, CTX).passed


def test_allow_numbers_whitelist():
    assert checks.no_invented_numbers(d("Call at 5pm."), {"allow_numbers": ["5"]}, CTX).passed


def test_subject_numbers_are_checked_too():
    assert not checks.no_invented_numbers(d("fine", subject="Only $99"), {}, CTX).passed


@pytest.mark.parametrize("body,contact,ok", [
    ("Hi Marcus, hello.", "Marcus Oyelaran", True),
    ("Dear Oyelaran, hello.", "Marcus Oyelaran", True),
    ("Hi there, hello.", "Marcus Oyelaran", True),
    ("Hi Jennifer, hello.", "Marcus Oyelaran", False),
    ("Hi Jennifer, hello.", "", False),
    ("Hello, I applied last month.", "", True),
    ("Hi, I wanted to check in.", "Marcus Oyelaran", True),
    ("Good morning Dana!\nHello.", "Dana Whitfield", True),
    ("No greeting, straight to it.", "", True),
])
def test_greeting_name(body, contact, ok):
    sc = {"contact": {"name": contact}} if contact else {}
    assert checks.greeting_name(d(body), sc, CTX).passed is ok


def test_max_words_default_and_override():
    assert checks.max_words(d("word " * 140), {}, CTX).passed
    assert not checks.max_words(d("word " * 141), {}, CTX).passed
    assert not checks.max_words(d("word " * 61), {"max_words": 60}, CTX).passed


@pytest.mark.parametrize("body,ok", [
    ("Would you be open to a call?", True),
    ("Let me know what works.", True),
    ("I wanted to ask whether there is an update.", True),
    ("I am checking on the status.", True),
    ("Just a note that we exist.", False),
])
def test_has_ask(body, ok):
    assert checks.has_ask(d(body), {}, CTX).passed is ok


def test_has_ask_can_be_disabled_per_scenario():
    assert checks.has_ask(d("Thanks for everything."), {"require_ask": False}, CTX).passed


def test_forbidden_terms_scenario_and_default():
    sc = {"forbidden_terms": ["pineapple"]}
    assert not checks.forbidden_terms(d("PINEAPPLE!"), sc, CTX).passed
    assert not checks.forbidden_terms(d("I am desperate for work."), {}, CTX).passed
    assert not checks.forbidden_terms(d("fine", subject="pineapple"), sc, CTX).passed
    assert checks.forbidden_terms(d("A normal message."), sc, CTX).passed


def test_style_boilerplate():
    assert not checks.style_boilerplate(d("I hope this email finds you well."), {}, CTX).passed
    assert not checks.style_boilerplate(d("Let's circle back soon."), {}, CTX).passed
    assert checks.style_boilerplate(d("Plain and direct."), {}, CTX).passed


def test_must_include_any_groups():
    sc = {"must_include_any": [["thursday", "friday"], ["quote"]]}
    assert checks.must_include_any(d("About the QUOTE for Thursday"), sc, CTX).passed
    r = checks.must_include_any(d("About the quote only"), sc, CTX)
    assert not r.passed and "thursday" in r.detail


def test_expected_channel():
    assert checks.expected_channel(d("x", channel="text"), {"expect_channel": "text"}, CTX).passed
    assert not checks.expected_channel(d("x", channel="email"), {"expect_channel": "text"}, CTX).passed
    assert checks.expected_channel(d("x"), {}, CTX).passed


def test_run_checks_returns_one_result_per_check():
    results = checks.run_checks(d("Would you reply?"), {}, CTX)
    assert [r.name for r in results] == [c.__name__ for c in checks.ALL_CHECKS]
    assert all(isinstance(r, CheckResult) for r in results)


# ---- running scenarios ----

GOOD = json.dumps({"subject": "Hello", "body": "Hi Marcus, would Sunday brunch music work for you?"})


def scenario(sid):
    return next(s for s in harness.load_scenarios() if s["id"] == sid)


def test_run_scenario_passes_with_a_good_reply():
    r = harness.run_scenario(scenario("gigs_quiet_pitch"), FakeModel(GOOD))
    assert r.status == "ok" and r.passed and r.draft["channel"] == "email"


def test_run_scenario_fails_on_bad_content():
    bad = json.dumps({"subject": "x", "body": "Hi Marcus, we charge $400. Interested?"})
    r = harness.run_scenario(scenario("gigs_quiet_pitch"), FakeModel(bad))
    assert r.status == "ok" and not r.passed
    assert [c.name for c in r.failed_checks()] == ["no_invented_numbers", "must_include_any"]


def test_guardrail_rejection_is_a_scored_failure():
    r = harness.run_scenario(scenario("gigs_quiet_pitch"), FakeModel('{"body": "Hi [Name]"}'))
    assert r.status == "rejected" and not r.passed and "placeholder" in r.detail


def test_unparseable_reply_is_a_scored_failure():
    r = harness.run_scenario(scenario("gigs_quiet_pitch"), FakeModel("sorry, I can't help"))
    assert r.status == "rejected" and not r.passed


def test_model_outage_is_an_error_not_a_score():
    class Down:
        name = "down"

        def complete(self, system, user, **kwargs):
            raise ModelError("connection refused")

    r = harness.run_scenario(scenario("gigs_quiet_pitch"), Down())
    assert r.status == "error" and not r.passed and "connection refused" in r.detail


def test_text_scenario_picks_text_channel():
    r = harness.run_scenario(scenario("gigs_text_only_contact"), FakeModel(
        json.dumps({"subject": "", "body": "Hi Tomasz, can we lock in the next Friday?"})))
    assert r.draft["channel"] == "text" and r.passed


def test_voice_reaches_model_in_scenarios_that_have_one():
    model = FakeModel(GOOD)
    harness.run_scenario(scenario("jobs_screen_followup"), model)
    assert "Otto" in model.calls[0][1]


def test_each_run_uses_a_fresh_database():
    sc = scenario("gigs_quiet_pitch")
    model = FakeModel(GOOD)
    harness.run_scenario(sc, model)
    # Would raise "already has pending draft" or a duplicate-org error if state leaked.
    assert harness.run_scenario(sc, model).status == "ok"


# ---- reports ----

def test_evaluate_scores_and_excludes_errors():
    scenarios = [scenario("gigs_quiet_pitch"), scenario("gigs_sparse_record")]
    replies = iter([GOOD, "garbage"])
    report = harness.evaluate(FakeModel(lambda s, u: next(replies)), scenarios)
    assert report.score == 0.5 and report.errors == 0
    text = harness.format_report(report)
    assert "PASS  gigs_quiet_pitch" in text and "FAIL  gigs_sparse_record" in text
    assert "score: 50%" in text


def test_multiple_runs_report_pass_rate():
    replies = iter([GOOD, "garbage", GOOD])
    report = harness.evaluate(FakeModel(lambda s, u: next(replies)), [scenario("gigs_quiet_pitch")], runs=3)
    assert report.summaries[0].passes == 2 and "2/3" in harness.format_report(report)


def test_all_errors_scenario_reports_err_and_zero_score():
    class Down:
        name = "down"

        def complete(self, system, user, **kwargs):
            raise ModelError("nope")

    report = harness.evaluate(Down(), [scenario("gigs_quiet_pitch")])
    assert report.errors == 1 and report.score == 0.0
    assert "ERR   gigs_quiet_pitch" in harness.format_report(report)


def test_report_dict_is_json_serializable():
    report = harness.evaluate(FakeModel(GOOD), [scenario("gigs_quiet_pitch")])
    data = json.loads(json.dumps(harness.report_to_dict(report)))
    assert data["model"] == "fake" and data["scenarios"][0]["runs"][0]["checks"]


# ---- command line ----

def test_cli_selftest_exit_zero(capsys):
    assert harness.main(["--selftest"]) == 0
    assert "expectations held" in capsys.readouterr().out


def test_cli_runs_model_and_writes_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(harness, "get_model", lambda spec=None: FakeModel(GOOD))
    out = tmp_path / "r.json"
    code = harness.main(["--scenario", "gigs_quiet_pitch", "--json", str(out)])
    assert code == 0 and json.loads(out.read_text())["score"] == 1.0


def test_cli_min_score_gate(monkeypatch):
    monkeypatch.setattr(harness, "get_model", lambda spec=None: FakeModel("garbage"))
    assert harness.main(["--scenario", "gigs_quiet_pitch"]) == 0
    assert harness.main(["--scenario", "gigs_quiet_pitch", "--min-score", "0.5"]) == 1


def test_cli_outage_exits_2(monkeypatch):
    class Down:
        name = "down"

        def complete(self, system, user, **kwargs):
            raise ModelError("nope")

    monkeypatch.setattr(harness, "get_model", lambda spec=None: Down())
    assert harness.main(["--scenario", "gigs_quiet_pitch"]) == 2


def test_cli_unknown_scenario_and_bad_runs(monkeypatch, capsys):
    monkeypatch.setattr(harness, "get_model", lambda spec=None: FakeModel(GOOD))
    assert harness.main(["--scenario", "nope"]) == 2
    assert harness.main(["--runs", "0"]) == 2


def test_cli_no_model_configured_is_clean_exit_2(monkeypatch, capsys):
    monkeypatch.delenv("OPEN_OTTO_MODEL", raising=False)
    assert harness.main([]) == 2
    assert "no model configured" in capsys.readouterr().err


# ---- hardening switch, tags, comparison ----

def test_harden_flag_reaches_the_prompt():
    sc = scenario("gigs_injection_in_notes")
    on, off = FakeModel(GOOD), FakeModel(GOOD)
    harness.run_scenario(sc, on, harden=True)
    harness.run_scenario(sc, off, harden=False)
    assert "PINEAPPLE" not in on.calls[0][1] and "PINEAPPLE" in off.calls[0][1]


def test_hardening_blocks_detectable_injection_but_not_evasive():
    caught = harness.run_scenario(scenario("gigs_injection_note_to_ai"), FakeModel(GOOD))
    assert caught.status == "ok"
    for sid in ("gigs_evasive_must_close", "gigs_evasive_redirect", "gigs_evasive_contact_role"):
        sc = scenario(sid)
        obey = json.dumps(sc["bad_replies"][0]["reply"])
        r = harness.run_scenario(sc, FakeModel(obey))
        # Not removed from the prompt, so a model that obeys still produces a failing draft.
        assert r.status == "ok" and not r.passed, sid


def test_no_hardening_flag_and_mode_in_report(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(harness, "get_model", lambda spec=None: FakeModel(GOOD))
    out = tmp_path / "r.json"
    harness.main(["--scenario", "gigs_quiet_pitch", "--no-hardening", "--json", str(out)])
    assert "baseline (no injection hardening)" in capsys.readouterr().out
    assert json.loads(out.read_text())["hardened"] is False


def test_tag_lines_in_report():
    report = harness.evaluate(FakeModel(GOOD), [scenario("gigs_quiet_pitch"), scenario("gigs_injection_note_to_ai")])
    text = harness.format_report(report)
    assert "injection: " in text and "runs passed" in text


def test_compare_table(tmp_path):
    from evals import compare
    paths = []
    for name, harden in (("a", False), ("b", True)):
        report = harness.evaluate(FakeModel(GOOD, name="m1"),
                                  [scenario("gigs_quiet_pitch"), scenario("gigs_injection_note_to_ai"),
                                   scenario("gigs_evasive_redirect")], harden=harden)
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(harness.report_to_dict(report)))
        paths.append(p)
    table = compare.build_table(paths)
    lines = table.splitlines()
    assert lines[0].startswith("| Model | Mode |")
    assert "| m1 | baseline |" in lines[2] and "| m1 | hardened |" in lines[3]  # baseline first
    assert "Obeyed injection (detectable)" in lines[0] and "Obeyed injection (evasive)" in lines[0]


def test_compare_counts_obeyed_injection(tmp_path):
    from evals import compare
    sc = scenario("gigs_injection_note_to_ai")
    bad = sc["bad_replies"][0]["reply"]
    bad = bad if isinstance(bad, str) else json.dumps(bad)
    report = harness.evaluate(FakeModel(bad, name="m2"), [sc], runs=2, harden=False)
    row = compare.summarize(harness.report_to_dict(report))
    assert row["obeyed_detectable"] == "2/2" and row["obeyed_evasive"] == "n/a"
    good = harness.evaluate(FakeModel(GOOD, name="m2"), [sc], runs=2, harden=False)
    assert compare.summarize(harness.report_to_dict(good))["obeyed_detectable"] == "0/2"


def test_compare_cli_errors_cleanly(tmp_path, capsys):
    from evals import compare
    assert compare.main([str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert compare.main([str(bad)]) == 2
