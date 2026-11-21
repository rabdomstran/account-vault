"""Regression tests for known-broken upstream behaviour (browser-use/jev-ultrafast).

Each test names the upstream issue it pins. These are the deliberate
deviations marked DEVIATION in src/account_vault/. No paid APIs, no browser.
"""

import time
from copy import deepcopy
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
        "actions": actions
        if actions is not None
        else [
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
        "no_progress": [],
        "cycle_suppressed": [],
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
    settings = Settings(
        systemone_base_url="https://gateway.test",
        systemone_path="/v1/evaluate",
        systemone_api_key="gw-key",
        text_model_api_key="t",
    )
    with pytest.raises(model.ModelError):
        model.choose(page(), "goal", [], settings=settings)
    assert seen == {"url": "https://gateway.test/v1/evaluate", "key": "gw-key"}


# --- upstream issue #203: {"text": null} is "missing", not malformed ------


def test_text_null_is_missing_value_not_malformed(monkeypatch):
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": '{"text":null}'}}]}))
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
    post = Mock(
        side_effect=[
            {"choices": [{"message": {"content": "not json"}}]},
            {"choices": [{"message": {"content": '```json\n{"text":"Zurich"}\n```'}}]},
        ]
    )
    monkeypatch.setattr(model, "post_json", post)
    value, helper = model.field_text({"goal": "g"}, settings=SETTINGS, attempts=3)
    assert value == "Zurich"
    assert helper["attempts"] == 2


# --- upstream issues #191/#198/#202: element identity in helper + cache ---


def test_field_context_distinguishes_same_labeled_fields():
    p = page()
    shipping = {
        "label": "Street Address",
        "role": "textbox",
        "value": "",
        "node": 41,
        "dom_id": "ship-street",
        "scope": "Shipping address",
    }
    billing = {
        "label": "Street Address",
        "role": "textbox",
        "value": "",
        "node": 77,
        "dom_id": "bill-street",
        "scope": "Billing address",
    }
    a = model.field_context("g", shipping, p, [])
    b = model.field_context("g", billing, p, [])
    assert a != b
    assert a["field"]["node"] == 41 and b["field"]["scope"] == "Billing address"


def test_field_context_tells_the_helper_the_native_value_format():
    # Upstream issue #227: native controls commit only their canonical
    # shape and the executor now rejects anything else. The helper must
    # see the required shape, or a goal saying "March 1" produces a
    # value the fill can only refuse.
    p = page()
    date = {"label": "Check-in", "role": "textbox", "value": "", "node": 9, "input_type": "date"}
    field = model.field_context("Check in on March 1, 2027", date, p, [])["field"]
    assert field["input_type"] == "date"
    assert field["required_format"].startswith("YYYY-MM-DD")

    text = {"label": "City", "role": "textbox", "value": "", "node": 10, "input_type": "text"}
    assert "required_format" not in model.field_context("g", text, p, [])["field"]


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
        "https://example.test/",
        "goal",
        browser=browser,
        decider=decider,
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
    return loop.Agent(FIXTURE, "goal", decider=decider, text_provider=model.MappingTextProvider(FILLS))


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
    answers = [decision("e999", node=999, operation="CLICK"), decision("BLOCKED", node=None, operation="BLOCKED")]
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


def test_reasoning_field_matches_the_deepseek_host_exactly():
    # CodeQL alert #1 (py/incomplete-url-substring-sanitization): the
    # provider special-case must key on the URL hostname, so lookalike
    # URLs that merely contain the substring don't take the DeepSeek
    # branch (and the real host always does).
    effort = Settings(text_model_reasoning="low", text_model_base_url="https://api.deepseek.com/v1")
    assert model._reasoning_field(effort) == {"thinking": {"type": "disabled"}}
    for url in ("https://evil.test/api.deepseek.com/", "https://api.deepseek.com.evil.test/v1"):
        lookalike = Settings(text_model_reasoning="low", text_model_base_url=url)
        assert model._reasoning_field(lookalike) == {"reasoning": {"effort": "low"}}, url
    assert model._reasoning_field(Settings(text_model_reasoning="omit")) == {}
    assert model._reasoning_field(Settings(text_model_reasoning="none")) == {"reasoning": {"enabled": False}}


# --- upstream issue #247 + PR #209/#67: drop-then-block, not block-only ---
# #247's comment thread proposes dropping a recurring (url, control) pair
# from the next offered set when it recurs twice and giving up cleanly at
# three; PR #209 drops a no-op action from the next decision (fingerprint-
# scoped) and caps episode-wide no-ops; #67's fork stops offering a dead
# target after 2 failed tries. The first cut here only blocked on the same
# (label, landing URL) 3x in the last 12 actions: no recovery turn, and
# hub-and-spoke browsing (same "back" label landing on the hub from
# different item pages) counted as a cycle. The tests below pin the
# source-aware, drop-first behaviour; each fails against block-only.


def _nav_page(url, label):
    p = page(
        [
            {"id": "e1", "kind": "click", "label": label, "role": "link", "value": "", "node": 10},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ]
    )
    p["url"] = url
    p["fingerprint"] = fingerprint(p)
    return p


def _go(runner, source, landing):
    runner.state["page"] = source
    runner.state["browser"].observe.return_value = landing
    runner.state["decision"] = decision("e1", node=10, operation="CLICK")
    runner.command("act", {"fingerprint": source["fingerprint"]})


def test_noop_action_is_dropped_from_next_offer_until_page_moves():
    p = page()
    runner = make_runner(p)
    runner.state["decision"] = decision("e3", node=20, operation="CLICK")
    runner.command("act", {"fingerprint": p["fingerprint"]})
    assert runner.state["history"][-1]["page_changed"] is False
    assert runner.state["no_progress"] == ["e3"]
    offered = []

    def choose(state, goal, history):
        offered.append([a["id"] for a in state["actions"]])
        return decision("wait", node=None, operation="WAIT")

    runner.decider.choose.side_effect = choose
    runner.state["status"] = "ready"
    runner.command("predict", {})
    assert offered == [["e1", "wait"]], "the no-op control leaves the next offered set (#209)"
    moved = page()
    moved["text"] = "Results"
    moved["fingerprint"] = fingerprint(moved)
    runner.state["browser"].fresh.return_value = False
    runner.state["browser"].observe.return_value = moved
    runner.command("predict", {})
    assert offered[-1] == ["e1", "e3", "wait"], "a moved page reoffers the control"
    assert runner.state["no_progress"] == []


def test_scattered_noops_hit_episode_cap_without_three_consecutive():
    cap = 12  # MAX_ACTIONS // 5, as in PR #209's MAX_NO_PROGRESS
    runner = make_runner(page())
    calls = {"n": 0}

    def observe(**_kwargs):
        calls["n"] += 1
        current = runner.state["page"]
        if calls["n"] % 2 == 1:
            return current  # no-op round
        moved = deepcopy(current)
        moved["text"] = f"after {calls['n']}"
        moved["fingerprint"] = fingerprint(moved)
        return moved

    runner.state["browser"].observe.side_effect = observe
    total = cap * 2 - 1  # no-ops land on odd rounds; the cap-th is the last
    for round_no in range(1, total + 1):
        current = deepcopy(runner.state["page"])
        for action in current["actions"]:
            if action["id"] == "e3":
                action["label"] = f"Go {round_no}"  # distinct transition: cycle counting cannot fire
        current["fingerprint"] = fingerprint(current)
        runner.state["page"] = current
        runner.state["decision"] = decision("e3", node=20, operation="CLICK")
        runner.command("act", {"fingerprint": current["fingerprint"]})
        if round_no == total - 2:
            assert runner.state["status"] == "ready", "11 scattered no-ops are below the cap"
    history = runner.state["history"]
    assert runner.state["status"] == "blocked"
    assert sum(1 for h in history if h["page_changed"] is False) == cap
    flags = [h["page_changed"] for h in history]
    assert all(flags[i : i + 3] != [False, False, False] for i in range(len(flags) - 2))


def test_same_landing_from_different_sources_is_not_a_cycle():
    # Hub-and-spoke product browsing: "Back to hub" lands on /hub from three
    # different item pages. Keying on (label, landing URL) alone blocks this
    # legitimate run at the third return; the transition key must include
    # where the control was chosen (#247 comment: (url, chosen control)).
    hub = _nav_page("https://example.test/hub", "View item")
    runner = make_runner(hub)
    for i in (1, 2, 3):
        item = _nav_page(f"https://example.test/item{i}", "Back to hub")
        _go(runner, hub, item)
        _go(runner, item, hub)
    assert runner.state["status"] == "ready"
    assert len(runner.state["history"]) == 6
    assert all(h["page_changed"] for h in runner.state["history"])


def test_same_transition_is_suppressed_at_two_and_blocks_at_three():
    a_page = _nav_page("https://example.test/a", "To B")
    b_page = _nav_page("https://example.test/b", "To A")
    runner = make_runner(a_page)
    _go(runner, a_page, b_page)
    _go(runner, b_page, a_page)
    _go(runner, a_page, b_page)
    assert {"from_url": "https://example.test/a", "kind": "click", "label": "To B"} in runner.state["cycle_suppressed"]
    assert runner.state["status"] == "ready", "second identical transition suppresses, not blocks"
    _go(runner, b_page, a_page)
    _go(runner, a_page, b_page)  # direct act bypasses the offer: the block backstop must still fire
    assert runner.state["status"] == "blocked"
    assert "cycle" in runner.state["blocked_reason"].lower()
    assert len(runner.state["history"]) == 5


def test_cycle_guard_disabled_at_zero():
    a_page = _nav_page("https://example.test/a", "To B")
    b_page = _nav_page("https://example.test/b", "To A")
    runner = make_runner(a_page)
    runner.max_cycle_repeats = 0
    for _ in range(3):
        _go(runner, a_page, b_page)
        _go(runner, b_page, a_page)
    assert runner.state["status"] == "ready"
    assert runner.state["cycle_suppressed"] == []


def test_advancing_scrolls_are_progress_not_a_cycle():
    # Upstream PR #212: repeated same-direction scrolls that move the
    # viewport are how long pages are traversed; neither the cycle count
    # nor the no-op budgets may treat them as going nowhere.
    scrolled = page([{"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}])
    runner = make_runner(scrolled)
    for step in range(1, 6):
        moved = deepcopy(runner.state["page"])
        moved["scroll"] = {"y": step * 560}
        moved["fingerprint"] = fingerprint(moved)
        runner.state["browser"].observe.return_value = moved
        runner.state["decision"] = decision("scroll_down", node=None, operation="SCROLL_DOWN")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready"
    assert len(runner.state["history"]) == 5
    assert all(h["page_changed"] for h in runner.state["history"])


def test_refilling_a_field_with_new_values_is_not_a_cycle():
    # Same page, same field, three different values: (label, landing URL)
    # repeats, but each transition carries different text, so none repeats.
    p = page()
    runner = make_runner(p)
    values = iter(["Zurich", "Bern", "Lugano"])
    runner.text_provider = lambda context: (next(values), {"model": "test", "latency_ms": 0})
    for step in range(3):
        moved = deepcopy(runner.state["page"])
        moved["text"] = f"filled {step}"
        moved["fingerprint"] = fingerprint(moved)
        runner.state["browser"].observe.return_value = moved
        runner.state["decision"] = decision("e1", node=10, operation="TYPE_TEXT")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready"
    assert [h["text"] for h in runner.state["history"]] == ["Zurich", "Bern", "Lugano"]


def test_covered_learning_does_not_spend_stale_strikes():
    # Upstream PR #245 x the local stale-streak stop: a covered rejection
    # executes nothing, so decision-layer suppressions (which learn only
    # from history) never fire for it -- the snapshot learns instead, and
    # each re-observe offers one fewer unhittable control. Counting those
    # learning steps as stale strikes would block the run at three before
    # a fourth, hittable alternative is ever offered (consent banners
    # cover whole tables at once; #137 measured 33/33 on BBC News).
    from account_vault.browser import CoveredTarget

    runner = make_runner(page())
    runner.decider.choose.return_value = decision("e3", node=20, operation="CLICK")
    runner.state["browser"].act.side_effect = CoveredTarget("Target is covered. Observe again.")
    rounds = {"n": 0}

    def observe(**_kwargs):
        rounds["n"] += 1
        moved = page()
        moved["text"] = f"offer {rounds['n']}"  # the covered node left the offer
        moved["fingerprint"] = fingerprint(moved)
        return moved

    runner.state["browser"].observe.side_effect = observe
    runner.state["status"] = "ready"
    for _ in range(3):
        runner.command("tick")
    assert runner.state["status"] == "ready", "three covered learnings must not block the run"
    assert runner.state["stale_streak"] == 0
    assert runner.state["history"] == []  # still nothing executed
    # A plain stale rejection (no new knowledge) still counts as before.
    runner.state["browser"].act.side_effect = StalePage("target vanished")
    runner.command("tick")
    assert runner.state["stale_streak"] == 1
