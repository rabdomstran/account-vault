"""The `python -m account_vault` CLI: offline loop, flags, exit behavior.

The default-drive test runs the real perceive -> decide -> act loop
against examples/hotel_search.html through the actual module entry
point (runpy as __main__), so argument defaults, the HeuristicDecider
wiring, printing, and the independent verify() gate are all exercised
exactly as a user runs them. The systemone branch is covered with a
fake Agent (it would otherwise need a live model endpoint).
"""

import runpy
import sys
import warnings

import pytest

import account_vault.__main__ as cli
from account_vault.__main__ import main, verify


def passing_page():
    return {
        "text": "4 stays near Zurich match your search",
        "actions": [
            {"label": "Destination", "current_value": "Zurich"},
            {"label": "Check-in", "current_value": "2027-03-01"},
            {"label": "Guests", "current_value": "1 guest (promo)"},
            {"label": "Free cancellation", "checked": "true"},
            {"label": "Notes", "current_value": "Late arrival"},
        ],
    }


def test_verify_fails_every_check_on_an_empty_page():
    result = verify({"actions": [], "text": ""})
    assert result["passed"] is False
    assert not any(result["checks"].values())


def test_verify_uses_values_not_the_deciders_word():
    page = passing_page()
    assert verify(page)["passed"] is True
    page["actions"][2]["current_value"] = "1 guest"  # duplicate-value trap
    result = verify(page)
    assert result["checks"]["guests_promo"] is False and result["passed"] is False


def test_module_entry_point_drives_the_default_example_to_done(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["account-vault"])
    with warnings.catch_warnings():
        # cli is already imported by this module; runpy warns about
        # re-executing it as __main__, which is exactly the intent here.
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("account_vault", run_name="__main__")
    out = capsys.readouterr().out
    assert "OK: loop reached DONE and independent verification passed." in out
    assert '"passed": true' in out


def test_main_honours_repeated_fill_flags(capsys):
    main(["--fill", "Destination=Bern", "--fill", "Notes=Early arrival"])
    out = capsys.readouterr().out
    assert '"passed": true' in out
    assert "OK: loop reached DONE" in out


def test_main_exits_nonzero_when_verification_cannot_pass(tmp_path, capsys):
    empty = tmp_path / "empty.html"
    empty.write_text("<html><head><title>Empty</title></head><body><p>Nothing</p></body></html>")
    with pytest.raises(SystemExit, match="did not satisfy the independent checks"):
        main(["--html", str(empty)])
    out = capsys.readouterr().out
    assert '"passed": false' in out


class FakeAgent:
    """Stands in for Agent(settings=...) on the systemone branch."""

    instances = []

    def __init__(self, html, goal, settings=None):
        self.html, self.goal, self.settings = html, goal, settings
        FakeAgent.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def snapshot(self):
        return {"page": {"url": self.html}, "elements": [1, 2, 3]}

    def run(self):
        yield {
            "history": [{"operation": "CLICK", "action": "Search", "text": "Zurich"}],
            "elapsed_ms": 7,
            "status": "done",
            "page": passing_page(),
            "decisions": [{}],
            "text_calls": [{}],
            "blocked_reason": None,
        }


def test_systemone_branch_builds_agent_from_settings(monkeypatch, capsys):
    sentinel = object()
    monkeypatch.setattr(cli, "load_settings", lambda: sentinel)
    monkeypatch.setattr(cli, "Agent", FakeAgent)
    FakeAgent.instances.clear()

    main(["--decider", "systemone", "--goal", "find stays"])
    (agent,) = FakeAgent.instances
    assert agent.settings is sentinel and agent.goal == "find stays"
    assert "OK: loop reached DONE" in capsys.readouterr().out


def test_systemone_branch_reports_a_blocked_run(monkeypatch, capsys):
    class BlockedAgent(FakeAgent):
        def run(self):
            state = next(super().run())
            state.update(status="blocked", blocked_reason="no progress", page={"actions": [], "text": ""})
            yield state

    monkeypatch.setattr(cli, "load_settings", object)
    monkeypatch.setattr(cli, "Agent", BlockedAgent)
    with pytest.raises(SystemExit, match="did not satisfy"):
        main(["--decider", "systemone"])
    assert "reason=no progress" in capsys.readouterr().out
