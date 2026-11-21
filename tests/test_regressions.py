"""Regression tests for known-broken upstream behaviour (browser-use/jev-ultrafast).

Each test names the upstream issue it pins. These are the deliberate
deviations marked DEVIATION in src/account_vault/. No paid APIs, no browser.
"""

import time
from unittest.mock import Mock

import pytest

from account_vault import agent as loop
from account_vault import model
from account_vault.browser import StalePage, fingerprint
from account_vault.settings import Settings, load_settings

SETTINGS = Settings(
    systemone_base_url="https://decide.test",
    systemone_api_key="test",
    text_model_api_key="test",
)


def page(actions=None):
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": actions if actions is not None else [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def decision(action="e1", node=10, confidence=1.0, operation="TYPE_TEXT"):
    return {
        "choice": action,
        "node": node,
        "operation": operation,
        "target": "1",
        "confidence": confidence,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


def make_runner(p):
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    a.settings = SETTINGS
    a.decider = Mock(validate=Mock(return_value=None))
    a.text_provider = lambda context: loop.field_text(context)
    a.min_confidence = 0.0
    a.max_empty_observes = 5
    a.max_stale_decisions = 3
    a.max_cycle_repeats = 3
    a._low_confidence_reobserves = 0
    a.record_dir = None
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": None,
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
        "elapsed_ms": 0,
        "plan": ["Find a book"],
        "plan_index": 0,
    }
    a.browser = a.state["browser"]
    return a


class FakeResponse:
    def __init__(self, payload=None, status_code=200, json_error=False):
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("No JSON object could be decoded")
        return self._payload


def stub_client(monkeypatch, response):
    client = Mock()
    client.post.return_value = response
    monkeypatch.setattr(model, "_client", lambda: client)
    return client


# --- upstream issue #158: HTTP 200 with an error body is not success ------

def test_post_json_rejects_error_body_in_http_200(monkeypatch):
    stub_client(monkeypatch, FakeResponse({"id": "gen-1", "error": {"message": "Upstream idle timeout", "code": 504}}))
    with pytest.raises(model.ModelError, match="no action executed"):
        model.post_json("https://decide.test/v1/systemone", "k", {})


# --- upstream issue #87: invalid JSON must not leak a decoder error -------

def test_post_json_rejects_non_json_body(monkeypatch):
    stub_client(monkeypatch, FakeResponse(json_error=True))
    with pytest.raises(model.ModelError, match="invalid JSON"):
        model.post_json("https://decide.test/v1/systemone", "k", {})


# --- upstream issue #133: off-envelope 200 must not raise raw KeyError ----

def test_choose_rejects_off_envelope_response(monkeypatch):
    monkeypatch.setattr(model, "post_json", Mock(return_value={"unexpected": True}))
    with pytest.raises(model.ModelError, match="envelope"):
        model.choose(page(), "goal", [], settings=SETTINGS)


def test_choose_uses_configured_endpoint_not_hardcoded_host(monkeypatch):
    # Upstream issues #241/#25: gateway users get 401 from the hardcoded host.
    seen = {}

    def post(url, key, body):
        seen["url"], seen["key"] = url, key
        raise model.ModelError("stop here")

    monkeypatch.setattr(model, "post_json", post)
    settings = Settings(systemone_base_url="https://gateway.test", systemone_path="/v1/evaluate",
                        systemone_api_key="gw-key", text_model_api_key="t")
    with pytest.raises(model.ModelError):
        model.choose(page(), "goal", [], settings=settings)
    assert seen == {"url": "https://gateway.test/v1/evaluate", "key": "gw-key"}


# --- upstream issue #203: {"text": null} is "missing", not malformed ------

def test_text_null_is_missing_value_not_malformed(monkeypatch):
    monkeypatch.setattr(model, "post_json",
                        Mock(return_value={"choices": [{"message": {"content": '{"text":null}'}}]}))
    with pytest.raises(model.MissingValueError, match="nothing typed"):
        model.field_text({"goal": "g"}, settings=SETTINGS, attempts=3)


def test_missing_value_blocks_instead_of_crashing():
    p = page()
    runner = make_runner(p)
    runner.text_provider = Mock(side_effect=model.MissingValueError("missing; nothing typed."))
    runner.state["decision"] = decision()
    snapshot = runner.command("act", {"fingerprint": p["fingerprint"]})
    assert snapshot["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()


# --- upstream issue #157: retry transient text-helper failures ------------

def test_text_helper_retries_and_strips_markdown_fence(monkeypatch):
    post = Mock(side_effect=[
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": '```json\n{"text":"Zurich"}\n```'}}]},
    ])
    monkeypatch.setattr(model, "post_json", post)
    value, helper = model.field_text({"goal": "g"}, settings=SETTINGS, attempts=3)
    assert value == "Zurich"
    assert helper["attempts"] == 2


# --- upstream issues #191/#198/#202: element identity in helper + cache ---

def test_field_context_distinguishes_same_labeled_fields():
    p = page()
    shipping = {"label": "Street Address", "role": "textbox", "value": "", "node": 41,
                "dom_id": "ship-street", "scope": "Shipping address"}
    billing = {"label": "Street Address", "role": "textbox", "value": "", "node": 77,
               "dom_id": "bill-street", "scope": "Billing address"}
    a = model.field_context("g", shipping, p, [])
    b = model.field_context("g", billing, p, [])
    assert a != b
    assert a["field"]["node"] == 41 and b["field"]["scope"] == "Billing address"


def test_cached_text_is_not_reused_for_a_different_node(monkeypatch):
    actions = [
        {"id": "e1", "kind": "fill", "label": "Street Address", "role": "textbox", "value": "", "node": 41},
        {"id": "e2", "kind": "fill", "label": "Street Address", "role": "textbox", "value": "", "node": 77},
        {"id": "wait", "kind": "wait", "label": "Wait"},
    ]
    p = page(actions)
    runner = make_runner(p)
    helper = Mock(return_value=("1 Main St", {"model": "test", "latency_ms": 1}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("changed"), None]
    runner.state["decision"] = decision("e1", node=41)
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": p["fingerprint"]})
    # Same label, different element: the cached value must not be reused.
    runner.state["decision"] = decision("e2", node=77)
    runner.command("act", {"fingerprint": p["fingerprint"]})
    assert helper.call_count == 2


# --- upstream issue #132: positional id alone must not select a control --

def test_stale_id_with_wrong_node_never_acts():
    p = page()
    runner = make_runner(p)
    # Decision was made for node 999, but id e3 now belongs to node 20.
    runner.state["decision"] = decision("e3", node=999, operation="CLICK")
    with pytest.raises(StalePage, match="changed identity"):
        runner.command("act", {"fingerprint": p["fingerprint"]})
    runner.state["browser"].act.assert_not_called()


# --- upstream issue #94: page_changed=None must not disable the stop -----

def test_failed_observation_still_counts_toward_no_progress_stop():
    p = page()
    runner = make_runner(p)
    # 1st action: post-action observe fails -> history entry keeps None.
    runner.state["browser"].observe.side_effect = [StalePage("navigating"), p, p]
    runner.state["decision"] = decision("e3", node=20, operation="CLICK")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": p["fingerprint"]})
    assert runner.state["history"][-1]["page_changed"] is None
    # Two more no-change actions: [None, False, False] must stop the run.
    for _ in range(2):
        runner.state["decision"] = decision("e3", node=20, operation="CLICK")
        runner.command("act", {"fingerprint": p["fingerprint"]})
    assert runner.state["status"] == "blocked"


# --- upstream issue #1: empty first observation retries, not BLOCKED -----

def test_empty_observation_is_retried_before_deciding():
    empty = page([{"id": "wait", "kind": "wait", "label": "Wait"}])
    full = page()
    browser = Mock()
    browser.observe.side_effect = [empty, empty, full]
    browser.fresh.return_value = True
    decider = Mock()
    decider.validate.return_value = None
    agent = loop.Agent(
        "https://example.test/", "goal",
        browser=browser, decider=decider,
        text_provider=Mock(return_value=("x", {"model": "t", "latency_ms": 0})),
        settings=SETTINGS,
    )
    assert browser.observe.call_count == 3
    assert agent.state["page"] is full


# --- upstream issue #120: confidence gate for ordinary actions -----------

def test_low_confidence_decision_is_not_executed():
    p = page()
    runner = make_runner(p)
    runner.min_confidence = 0.9
    runner.decider.choose.return_value = decision(confidence=0.4)
    runner.state["status"] = "ready"
    runner.command("predict", {})
    assert runner.state["decision"] is None
    assert runner.state["status"] == "ready"  # re-observe once
    runner.command("predict", {})
    assert runner.state["status"] == "blocked"  # still unsure -> stop
    runner.state["browser"].act.assert_not_called()


# --- upstream issue #55: both budgets are named ---------------------------

def test_model_call_budget_error_names_the_limit():
    from account_vault.questions import MAX_DECISIONS

    p = page()
    runner = make_runner(p)
    runner.state["status"] = "ready"
    runner.state["decisions"] = [{}] * MAX_DECISIONS
    with pytest.raises(ValueError, match=f"{MAX_DECISIONS}-request"):
        runner.command("predict", {})


# --- upstream issue #217: dotenv parsing strips quotes/comments -----------

def test_dotenv_values_are_parsed_not_raw(tmp_path, monkeypatch):
    monkeypatch.delenv("SYSTEMONE_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('export SYSTEMONE_API_KEY="tsk-abc123"  # hosted key\n', encoding="utf-8")
    settings = load_settings(env)
    assert settings.systemone_api_key == "tsk-abc123"


# --- upstream issues #94/#55/#132 at loop level: a decider that keeps ------
# --- choosing a missing element must BLOCK, not burn the whole budget ------
# Upstream's only stuck-run guard counts executed actions in history. A
# decision whose target is missing (bad index, vanished element, identity
# change) raises StalePage in act and is never recorded, so the guard can
# never fire: tick re-observes and re-asks until the 120-call budget (#55)
# raises an uncaught ValueError out of run(). Measured with a probe before
# the fix: 120 decider calls, 0 history, status "ready", then ValueError.
# That is #94's "spend the entire budget proving nothing happened" through
# the one door #94's own fix (counting page_changed=None) cannot reach.

from pathlib import Path  # noqa: E402

FIXTURE = str(Path(__file__).resolve().parents[1] / "examples" / "hotel_search.html")
FILLS = {"destination": "Zurich", "check-in": "2027-03-01", "notes": "Late arrival"}


class StubDecider:
    def __init__(self, choose):
        self._choose = choose
        self.calls = 0

    def validate(self):
        return None

    def choose(self, state, goal, history):
        self.calls += 1
        return self._choose(state, goal, history)


def static_agent(decider):
    return loop.Agent(FIXTURE, "goal", decider=decider,
                      text_provider=model.MappingTextProvider(FILLS))


def test_repeated_missing_target_blocks_without_spending_budget():
    decider = StubDecider(lambda s, g, h: decision("e999", node=999, operation="CLICK"))
    with static_agent(decider) as agent:
        states = list(agent.run())  # must terminate, not raise at the budget
        assert agent.state["status"] == "blocked"
        assert decider.calls == 3  # three-strikes stale stop, not 120 calls
        assert agent.state["history"] == []  # nothing ever executed
        destination = next(a for a in agent.browser.observe()["actions"] if a["label"] == "Destination")
        assert destination["value"] == ""  # page never mutated
    assert states[-1]["status"] == "blocked"


def test_single_stale_decision_recovers():
    answers = [decision("e999", node=999, operation="CLICK"),
               decision("BLOCKED", node=None, operation="BLOCKED")]
    decider = StubDecider(lambda s, g, h: answers.pop(0))
    with static_agent(decider) as agent:
        first = agent.command("tick")
        assert first["status"] == "ready"  # one stale decision: re-observe
        list(agent.run())
        assert agent.state["status"] == "blocked"  # via BLOCKED, not the streak
        assert decider.calls == 2


def test_good_step_then_vanished_target_blocks():
    calls = {"n": 0}

    def choose(state, goal, history):
        calls["n"] += 1
        if calls["n"] == 1:
            fill = next(a for a in state["actions"] if a["kind"] == "fill")
            return decision(fill["id"], node=fill["node"], operation="TYPE_TEXT")
        return decision("e999", node=999, operation="CLICK")

    with static_agent(StubDecider(choose)) as agent:
        list(agent.run())
        assert agent.state["status"] == "blocked"
        assert len(agent.state["history"]) == 1  # the good step still counts
        assert calls["n"] == 4  # 1 executed + 3 stale strikes


def test_malformed_decision_fails_before_any_mutation():
    # Upstream issue #133's principle: no raw KeyError escapes the loop.
    # Worse, probabilities is indexed while recording history — after the
    # browser already mutated — so an unchecked malformed decision used to
    # execute first and crash second.
    def choose(state, goal, history):
        fill = next(a for a in state["actions"] if a["kind"] == "fill")
        broken = decision(fill["id"], node=fill["node"], operation="TYPE_TEXT")
        broken["probabilities"] = {}
        return broken

    with static_agent(StubDecider(choose)) as agent:
        with pytest.raises(ValueError, match="no action executed"):
            agent.command("tick")
        assert agent.state["history"] == []
        destination = next(a for a in agent.browser.observe()["actions"] if a["label"] == "Destination")
        assert destination["value"] == ""  # nothing typed before the failure


def test_run_ends_blocked_at_decision_budget_without_raising():
    # Command-level predict still raises (pinned above, #55), but run() —
    # the drive-to-completion API — must land on the terminal "blocked"
    # state instead of leaking the budget error as a traceback.
    from account_vault.questions import MAX_DECISIONS

    p = page()
    runner = make_runner(p)
    runner.state["status"] = "ready"
    runner.state["decisions"] = [{}] * (MAX_DECISIONS - 1)
    runner.decider.choose.return_value = decision("wait", node=None, operation="WAIT")
    states = list(runner.run())
    assert runner.state["status"] == "blocked"
    assert len(runner.state["decisions"]) == MAX_DECISIONS
    assert states[-1]["status"] == "blocked"
