"""CdpBrowser / browser_operation against a scripted fake CDP transport.

browser.py keeps `cdp()` as an indirection "so tests can monkeypatch
account_vault.browser.cdp" — these tests use that seam (via the cached
_harness globals) to exercise the live-CDP paths without Chrome: target
setup, readiness waiting, freshness guards, stale retries, post-input
settling, and every Input.* dispatch in the act executor, including its
no-retry error semantics. The transport itself (a real daemon + Chrome)
stays covered by the opt-in tests/test_live_cdp.py.
"""

import sys
import types
from unittest.mock import Mock

import pytest

import account_vault.browser as browser
from account_vault.browser import CdpBrowser, CoveredTarget, StalePage, browser_operation

INFO = {
    "url": "https://example.test/",
    "title": "Example",
    "text": "hello",
    "actions": [],
    "scroll": 0,
    "marker": "M1",
    "page_key": "K1",
    "guards": {},
}


class FakeCdp:
    """Records calls; Runtime.evaluate answers from a script queue."""

    def __init__(self):
        self.calls = []
        self.script = []  # values, exceptions, or {"__response__": raw}

    def __call__(self, method, **params):
        self.calls.append((method, params))
        if method == "Target.createTarget":
            return {"targetId": "T1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Page.captureScreenshot":
            return {"data": "JPEGDATA"}
        if method == "Runtime.evaluate":
            if self.script:
                item = self.script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, dict) and "__response__" in item:
                    return item["__response__"]
                return {"result": {"value": dict(item) if isinstance(item, dict) else item}}
            return {"result": {"value": dict(INFO)}}
        return {}

    def methods(self):
        return [method for method, _ in self.calls]

    def evaluate_params(self):
        return [params for method, params in self.calls if method == "Runtime.evaluate"]


@pytest.fixture
def fake_cdp(monkeypatch):
    fake = FakeCdp()
    ensure_daemon = Mock(name="ensure_daemon")
    monkeypatch.setattr(browser, "_cdp", fake)
    monkeypatch.setattr(browser, "_ensure_daemon", ensure_daemon)
    fake.ensure_daemon = ensure_daemon
    # The #16 attach guard: construction needs a pinned endpoint (or the
    # real-browser opt-in) and a daemon that reports a dedicated kind.
    # Default the fake world to the pinned, dedicated case; the guard
    # tests below override per case.
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9333")
    monkeypatch.delenv("ACCOUNT_VAULT_ALLOW_REAL_BROWSER", raising=False)
    fake.browser_kind = Mock(name="daemon_browser_kind", return_value="cdp")
    monkeypatch.setattr(browser, "_daemon_browser_kind", fake.browser_kind)
    return fake


def bare_browser():
    instance = CdpBrowser.__new__(CdpBrowser)
    instance.session = "S1"
    instance.target = None
    return instance


def test_harness_lazy_import_is_cached(monkeypatch):
    sentinel_cdp, sentinel_ensure = Mock(name="cdp"), Mock(name="ensure_daemon")
    package = types.ModuleType("browser_harness")
    admin = types.ModuleType("browser_harness.admin")
    admin.ensure_daemon = sentinel_ensure
    helpers = types.ModuleType("browser_harness.helpers")
    helpers.cdp = sentinel_cdp
    package.admin, package.helpers = admin, helpers
    monkeypatch.setitem(sys.modules, "browser_harness", package)
    monkeypatch.setitem(sys.modules, "browser_harness.admin", admin)
    monkeypatch.setitem(sys.modules, "browser_harness.helpers", helpers)
    monkeypatch.setattr(browser, "_cdp", None)
    monkeypatch.setattr(browser, "_ensure_daemon", None)

    assert browser._harness() == (sentinel_cdp, sentinel_ensure)
    # Second call serves the cache without re-importing.
    monkeypatch.delitem(sys.modules, "browser_harness.helpers")
    assert browser._harness() == (sentinel_cdp, sentinel_ensure)


def test_init_creates_target_configures_and_waits_for_ready(fake_cdp):
    fake_cdp.script = ["loading", "interactive", "complete"]
    instance = CdpBrowser("https://example.test/page")

    fake_cdp.ensure_daemon.assert_called_once_with()
    assert instance.target == "T1" and instance.session == "S1"
    opening = fake_cdp.calls[:5]
    assert opening[0] == ("Target.createTarget", {"url": "about:blank", "background": True})
    assert opening[1][0] == "Target.attachToTarget"
    assert opening[1][1]["targetId"] == "T1" and opening[1][1]["flatten"] is True
    assert opening[2][0] == "Emulation.setDeviceMetricsOverride"
    assert opening[2][1]["width"] == 1120 and opening[2][1]["height"] == 780
    assert opening[2][1]["session_id"] == "S1"
    assert opening[3] == (
        "Emulation.setFocusEmulationEnabled",
        {"session_id": "S1", "_response_timeout": 30, "enabled": True},
    )
    assert opening[4] == (
        "Page.navigate",
        {"session_id": "S1", "_response_timeout": 30, "url": "https://example.test/page"},
    )
    # Polled readyState until complete: 3 evaluations.
    assert [p["expression"] for p in fake_cdp.evaluate_params()] == ["document.readyState"] * 3


def test_init_gives_up_waiting_after_the_deadline(fake_cdp, monkeypatch):
    ticks = iter([0.0, 100.0])  # deadline = 0+15; first loop check is past it

    def monotonic():
        return next(ticks, 1000.0)

    monkeypatch.setattr(browser.time, "monotonic", monotonic)
    instance = CdpBrowser("https://example.test/slow")
    assert instance.session == "S1"  # construction completes regardless
    assert fake_cdp.evaluate_params() == []  # never even polled


# --- attach guard (upstream issue #16a): never the everyday browser -------
# Unpinned, browser-harness discovers the daily Chrome from its profile
# dirs (or probes 9222/9223, or launches it). Construction must refuse
# that default before any daemon work, accept a pinned dedicated
# endpoint, and still catch the stale-daemon case where ensure_daemon()
# reuses a daemon that attached locally before the pin was set (#230).


def _unpin(monkeypatch):
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    monkeypatch.delenv("BU_CDP_WS", raising=False)


def test_init_refuses_unpinned_attach_before_any_daemon_work(fake_cdp, monkeypatch):
    _unpin(monkeypatch)
    with pytest.raises(RuntimeError, match="Refusing to attach"):
        CdpBrowser("https://example.test/")
    fake_cdp.ensure_daemon.assert_not_called()  # nothing spawned, launched or attached
    assert fake_cdp.calls == []


def test_init_refusal_names_the_dedicated_recipe_and_the_opt_in(fake_cdp, monkeypatch):
    _unpin(monkeypatch)
    with pytest.raises(RuntimeError) as caught:
        CdpBrowser("https://example.test/")
    message = str(caught.value)
    assert "BU_CDP_URL=http://127.0.0.1:9333" in message
    assert "--user-data-dir=" in message
    assert "ACCOUNT_VAULT_ALLOW_REAL_BROWSER=1" in message


def test_init_pinned_endpoint_constructs_and_records_kind(fake_cdp):
    fake_cdp.script = ["complete"]
    instance = CdpBrowser("https://example.test/")
    assert instance.browser_kind == "cdp"
    fake_cdp.ensure_daemon.assert_called_once_with()
    fake_cdp.browser_kind.assert_called_once_with()


def test_init_rejects_a_stale_local_daemon_despite_the_pin(fake_cdp, monkeypatch):
    # The pin is set, but a daemon under BU_NAME already attached to the
    # everyday Chrome and ensure_daemon() reused it: driving on would
    # drive the real profile while believing it is dedicated.
    fake_cdp.browser_kind.return_value = "local"
    with pytest.raises(RuntimeError, match="attached to a local"):
        CdpBrowser("https://example.test/")
    assert "Target.createTarget" not in fake_cdp.methods()  # refused before opening any tab


def test_init_fails_closed_when_daemon_kind_is_unverifiable(fake_cdp):
    fake_cdp.browser_kind.return_value = None  # older daemon, no kind report
    with pytest.raises(RuntimeError, match="cannot be verified"):
        CdpBrowser("https://example.test/")
    assert "Target.createTarget" not in fake_cdp.methods()


def test_init_real_browser_opt_in_warns_and_attaches(fake_cdp, monkeypatch):
    _unpin(monkeypatch)
    monkeypatch.setenv("ACCOUNT_VAULT_ALLOW_REAL_BROWSER", "1")
    fake_cdp.browser_kind.return_value = "local"
    fake_cdp.script = ["complete"]
    with pytest.warns(UserWarning, match="everyday Chrome profile"):
        instance = CdpBrowser("https://example.test/")
    assert instance.browser_kind == "local"
    fake_cdp.ensure_daemon.assert_called_once_with()


def test_init_opt_in_warns_when_a_stale_daemon_ignores_the_pin(fake_cdp, monkeypatch):
    monkeypatch.setenv("ACCOUNT_VAULT_ALLOW_REAL_BROWSER", "true")
    fake_cdp.browser_kind.return_value = "local"  # pin set, daemon still local
    fake_cdp.script = ["complete"]
    with pytest.warns(UserWarning, match="pin did not take effect"):
        instance = CdpBrowser("https://example.test/")
    assert instance.browser_kind == "local"


def test_call_scopes_every_method_to_the_session(fake_cdp):
    bare_browser().call("Page.reload", ignoreCache=True)
    assert fake_cdp.calls == [("Page.reload", {"session_id": "S1", "_response_timeout": 30, "ignoreCache": True})]


def test_evaluate_returns_value_and_maps_exceptions_to_stale(fake_cdp):
    instance = bare_browser()
    fake_cdp.script = ["complete"]
    assert instance.evaluate("document.readyState") == "complete"

    fake_cdp.script = [{"__response__": {"exceptionDetails": {"text": "gone"}}}]
    with pytest.raises(StalePage, match="Document changed during evaluation"):
        instance.evaluate("1 + 1")

    fake_cdp.script = [{"__response__": {}}]  # no result at all
    assert instance.evaluate("undefined") is None


def test_close_closes_the_target_once(fake_cdp):
    instance = bare_browser()
    instance.target = "T9"
    instance.close()
    instance.close()  # second call must not re-close
    assert fake_cdp.calls == [("Target.closeTarget", {"targetId": "T9"})]
    assert instance.target is None


# --- observe(): retries, screenshots, post-input settling -------------------


def test_observe_retries_stale_reads_then_succeeds(monkeypatch):
    operation = Mock(side_effect=[StalePage("nav"), StalePage("nav"), dict(INFO)])
    monkeypatch.setattr(browser, "browser_operation", operation)
    result = bare_browser().observe(screenshot=False)
    assert result["marker"] == "M1"
    assert operation.call_count == 3
    request = operation.call_args.args[0]
    # No screenshot requested, so no tab activation either (#43/#16).
    assert request == {"operation": "observe", "session": "S1", "screenshot": False, "activate": False}


def test_observe_raises_after_ten_stale_reads(monkeypatch):
    operation = Mock(side_effect=StalePage("never settles"))
    monkeypatch.setattr(browser, "browser_operation", operation)
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)
    with pytest.raises(StalePage, match="never settles"):
        bare_browser().observe()
    assert operation.call_count == 10


def test_observe_settles_after_input_before_reading(fake_cdp):
    instance = bare_browser()
    instance.after_input = {"kind": "fill", "node": 3}
    fake_cdp.script = [None]  # the settle promise resolves
    result = instance.observe(screenshot=False)

    assert instance.after_input is None
    settle, read = fake_cdp.evaluate_params()
    assert settle["awaitPromise"] is True
    assert '"node": 3' in settle["expression"] and '"kind": "fill"' in settle["expression"]
    assert read["expression"] == browser.READ_STATE
    assert result["fingerprint"] == browser.fingerprint(INFO)


def test_observe_ignores_a_settle_interrupted_by_navigation(fake_cdp):
    instance = bare_browser()
    instance.after_input = {"kind": "click", "node": 1}
    fake_cdp.script = [RuntimeError("context destroyed")]
    result = instance.observe(screenshot=False)  # must not raise
    assert result["marker"] == "M1"


# --- scroll-commit settle (#212/#209 interaction, found by the live gate) ---
# A dispatched mouseWheel commits asynchronously; on an idle headless
# tab the commit lags the post-input settle, so observe read scrollY
# unchanged, history recorded a no-op, and #209's drop removed
# scroll_down for the rest of the page -- the deep-scroll live test
# blocked mid-page on Linux while passing on macOS (a later poll showed
# the wheel had in fact landed). Observe after a scroll now polls
# scrollY until it moves, or the settle deadline passes.


def test_observe_after_scroll_waits_for_the_wheel_to_commit(fake_cdp, monkeypatch):
    instance = bare_browser()
    instance.after_input = {"kind": "scroll", "delta": 560}
    instance._scroll_before_y = 0.0
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)
    # Settle promise, two uncommitted reads, the committed offset; the
    # snapshot read then falls through to the fake's default INFO.
    fake_cdp.script = [None, 0, 0, 560]
    result = instance.observe(screenshot=False)

    expressions = [p["expression"] for p in fake_cdp.evaluate_params()]
    assert expressions[1:-1] == [browser.SCROLL_POLL] * 3
    assert expressions[-1] == browser.READ_STATE
    assert result["marker"] == "M1"
    assert instance._scroll_before_y is None  # consumed, not sticky


def test_observe_after_a_dead_scroll_still_observes_at_the_deadline(fake_cdp, monkeypatch):
    instance = bare_browser()
    instance.after_input = {"kind": "scroll", "delta": 560}
    instance._scroll_before_y = 0.0
    monkeypatch.setattr(browser, "SCROLL_SETTLE_TIMEOUT", 0.12)
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)
    # Deadline base + two in-deadline checks, then past the deadline:
    # exactly two polls (both unmoved -- a genuine boundary no-op), and
    # the snapshot proceeds so the no-op machinery sees it as before.
    monkeypatch.setattr(browser.time, "monotonic", Mock(side_effect=[0.0, 0.0, 0.0] + [1.0] * 10))
    fake_cdp.script = [None, 0, 0]
    result = instance.observe(screenshot=False)

    expressions = [p["expression"] for p in fake_cdp.evaluate_params()]
    assert expressions[1:-1] == [browser.SCROLL_POLL] * 2
    assert expressions[-1] == browser.READ_STATE
    assert result["marker"] == "M1"


def test_observe_after_boundary_scroll_exits_on_provable_limit(fake_cdp, monkeypatch):
    # Provable-boundary early exit (docs/latency-results.md): at the
    # directional limit no scroll event will ever arrive, so two
    # consecutive at-limit polls end the wait instead of the 3s deadline.
    instance = bare_browser()
    instance.after_input = {"kind": "scroll", "delta": 560}
    instance._scroll_before_y = 2800.0
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)
    fake_cdp.script = [None, [2800, 2800, 1], [2800, 2800, 1]]  # [y, maxY, scale]
    result = instance.observe(screenshot=False)

    expressions = [p["expression"] for p in fake_cdp.evaluate_params()]
    assert expressions[1:-1] == [browser.SCROLL_POLL] * 2
    assert expressions[-1] == browser.READ_STATE
    assert result["marker"] == "M1"


def test_observe_boundary_exit_is_direction_aware_and_needs_two_stable_polls(fake_cdp, monkeypatch):
    instance = bare_browser()
    instance.after_input = {"kind": "scroll", "delta": -560}
    instance._scroll_before_y = 0.0
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)
    # At the top while scrolling up: exit after the second stable poll.
    fake_cdp.script = [None, [0, 2800, 1], [0, 2800, 1]]
    instance.observe(screenshot=False)
    expressions = [p["expression"] for p in fake_cdp.evaluate_params()]
    assert expressions[1:-1] == [browser.SCROLL_POLL] * 2

    # A growing page revokes the proof between polls: no early exit,
    # the wait ends only on the later movement.
    fake_cdp.calls.clear()
    instance.after_input = {"kind": "scroll", "delta": 560}
    instance._scroll_before_y = 2800.0
    fake_cdp.script = [None, [2800, 2800, 1], [2800, 3600, 1], [3000, 3600, 1]]
    instance.observe(screenshot=False)
    expressions = [p["expression"] for p in fake_cdp.evaluate_params()]
    assert expressions[1:-1] == [browser.SCROLL_POLL] * 3


def test_observe_boundary_exit_falls_through_under_pinch_zoom(fake_cdp, monkeypatch):
    # Under pinch-zoom window.scrollY is the layout offset; the wheel
    # may move only the visual viewport, so at-limit proves nothing.
    instance = bare_browser()
    instance.after_input = {"kind": "scroll", "delta": 560}
    instance._scroll_before_y = 2800.0
    monkeypatch.setattr(browser, "SCROLL_SETTLE_TIMEOUT", 0.12)
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)
    monkeypatch.setattr(browser.time, "monotonic", Mock(side_effect=[0.0, 0.0, 0.0] + [1.0] * 10))
    fake_cdp.script = [None, [2800, 2800, 2], [2800, 2800, 2]]
    instance.observe(screenshot=False)
    expressions = [p["expression"] for p in fake_cdp.evaluate_params()]
    assert expressions[1:-1] == [browser.SCROLL_POLL] * 2  # polled to the deadline, no early exit


def test_act_scroll_records_the_pre_scroll_viewport_for_observe(monkeypatch):
    operation = Mock(return_value={"executed": "scroll_down"})
    monkeypatch.setattr(browser, "browser_operation", operation)
    instance = bare_browser()
    monkeypatch.setattr(instance, "fresh", Mock(return_value=True))

    action = {"kind": "scroll", "id": "scroll_down", "delta": 560}
    instance.act(action, page={"scroll": {"y": 560, "height": 2862}})
    assert instance.after_input is action
    assert instance._scroll_before_y == 560.0

    instance.act({"kind": "click", "node": 1, "id": "e1"}, page={"scroll": {"y": 560}})
    assert instance._scroll_before_y is None  # only scrolls arm the wait


def test_observe_screenshot_is_captured_only_when_asked(fake_cdp):
    with_shot = browser_operation({"operation": "observe", "session": "S1", "screenshot": True})
    assert with_shot["screenshot"] == "JPEGDATA"
    capture = next(p for m, p in fake_cdp.calls if m == "Page.captureScreenshot")
    assert capture["format"] == "jpeg" and capture["quality"] == 72

    fake_cdp.calls.clear()
    without = browser_operation({"operation": "observe", "session": "S1", "screenshot": False})
    assert "screenshot" not in without
    assert "Page.captureScreenshot" not in fake_cdp.methods()


def test_observe_of_a_navigating_document_is_stale(fake_cdp):
    fake_cdp.script = [None]  # snapshot.js returns null mid-navigation
    with pytest.raises(StalePage, match="Document is navigating"):
        browser_operation({"operation": "observe", "session": "S1", "screenshot": False})


# --- CDP deadlines + screenshot degradation (#14, PRs #97/#19/#43) ----------
# browser-harness defaults every IPC call to 5s. Slow navigations and
# evaluations legitimately exceed that, while a screenshot on a throttled
# background tab can stall indefinitely -- and used to take the whole
# (already-completed) structured observation down with it. Deadlines are
# explicit now (30s session-wide, 5s for the optional capture, the shape
# from branch codex/bu-bench-eval-v2 a2676ee/6cf62ca), capture failure by
# timeout degrades to screenshot=None, and no timeout is ever retried.


class TimingOutCapture(FakeCdp):
    """FakeCdp whose capture raises the way the harness IPC layer does."""

    def __call__(self, method, **params):
        if method == "Page.captureScreenshot":
            self.calls.append((method, params))
            raise TimeoutError("Page.captureScreenshot timed out after 5s waiting for the daemon")
        return super().__call__(method, **params)


@pytest.fixture
def timing_out_cdp(fake_cdp, monkeypatch):
    fake = TimingOutCapture()
    fake.ensure_daemon = fake_cdp.ensure_daemon
    fake.browser_kind = fake_cdp.browser_kind
    monkeypatch.setattr(browser, "_cdp", fake)
    return fake


def test_screenshot_timeout_preserves_the_structured_observation(timing_out_cdp):
    result = browser_operation({"operation": "observe", "session": "S1", "screenshot": True})
    assert result["marker"] == "M1" and result["actions"] == INFO["actions"]
    assert result["screenshot"] is None  # explicit: requested but unavailable
    capture = next(p for m, p in timing_out_cdp.calls if m == "Page.captureScreenshot")
    assert capture["_response_timeout"] == 5  # the optional capture keeps its short deadline


def test_screenshot_failure_other_than_timeout_still_propagates(fake_cdp, monkeypatch):
    original = fake_cdp.__call__

    def broken(method, **params):
        if method == "Page.captureScreenshot":
            raise RuntimeError("daemon exploded")
        return original(method, **params)

    monkeypatch.setattr(browser, "_cdp", broken)
    with pytest.raises(RuntimeError, match="daemon exploded"):
        browser_operation({"operation": "observe", "session": "S1", "screenshot": True})


def test_session_calls_carry_the_thirty_second_deadline(fake_cdp):
    fake_cdp.script = ["complete"]
    CdpBrowser("https://example.test/")  # navigate + evaluates go through call()
    browser_operation({"operation": "observe", "session": "S1", "screenshot": True})
    deadlines = {m: p["_response_timeout"] for m, p in fake_cdp.calls if "session_id" in p}
    assert deadlines["Page.navigate"] == 30
    assert deadlines["Runtime.evaluate"] == 30
    assert deadlines["Page.captureScreenshot"] == 5


def test_timed_out_mutation_is_never_retried(fake_cdp, monkeypatch):
    # A deadline is not a retry hint: the input may already have landed.
    navigations = Mock(side_effect=TimeoutError("Page.navigate timed out after 30s waiting for the daemon"))
    monkeypatch.setattr(browser, "_cdp", navigations)
    with pytest.raises(TimeoutError, match="timed out"):
        bare_browser().call("Page.navigate", url="https://example.test/")
    navigations.assert_called_once_with(
        "Page.navigate", session_id="S1", _response_timeout=30, url="https://example.test/"
    )


def test_capture_activates_the_tab_only_when_asked(fake_cdp):
    browser_operation({"operation": "observe", "session": "S1", "screenshot": True, "activate": True})
    assert fake_cdp.methods().index("Page.bringToFront") < fake_cdp.methods().index("Page.captureScreenshot")

    fake_cdp.calls.clear()
    browser_operation({"operation": "observe", "session": "S1", "screenshot": True})
    assert "Page.bringToFront" not in fake_cdp.methods()


def test_observe_requests_activation_only_on_a_dedicated_kind(monkeypatch):
    operation = Mock(return_value=dict(INFO))
    monkeypatch.setattr(browser, "browser_operation", operation)
    instance = bare_browser()

    instance.browser_kind = "cdp"
    instance.observe(screenshot=True)
    assert operation.call_args.args[0]["activate"] is True

    instance.browser_kind = "local"  # opted-in everyday Chrome: never steal focus
    instance.observe(screenshot=True)
    assert operation.call_args.args[0]["activate"] is False

    instance.browser_kind = "cdp"
    instance.observe(screenshot=False)  # nothing to capture, nothing to activate
    assert operation.call_args.args[0]["activate"] is False


# --- fresh(): per-node guards for click/select, marker otherwise ------------


def guard_page():
    return {"page_key": "K1", "guards": {"7": "guard-7"}, "marker": "M1"}


def test_fresh_click_compares_page_key_and_node_guard(fake_cdp):
    instance = bare_browser()
    action = {"kind": "click", "node": 7}
    fake_cdp.script = [["K1", "guard-7"]]
    assert instance.fresh(guard_page(), action) is True
    expression = fake_cdp.evaluate_params()[0]["expression"]
    assert "c.nodes.get(7)" in expression

    fake_cdp.script = [["K2", "guard-7"]]  # page moved on
    assert instance.fresh(guard_page(), action) is False
    fake_cdp.script = [["K1", "guard-8"]]  # node itself changed
    assert instance.fresh(guard_page(), action) is False


def test_fresh_rejects_non_int_nodes_without_evaluating(fake_cdp):
    instance = bare_browser()
    for node in ("7", 7.0, True, None):
        assert instance.fresh(guard_page(), {"kind": "select", "node": node}) is False
    assert fake_cdp.evaluate_params() == []


def test_fresh_falls_back_to_the_page_marker(fake_cdp):
    instance = bare_browser()
    fake_cdp.script = ["M1", "M2", "M1"]
    assert instance.fresh(guard_page(), {"kind": "fill", "node": 7}) is True
    assert instance.fresh(guard_page(), {"kind": "fill", "node": 7}) is False
    assert instance.fresh(guard_page()) is True  # no action at all


# --- CdpBrowser.act(): freshness gate, wait, after_input bookkeeping --------


def test_act_rejects_stale_decisions_before_any_input(monkeypatch):
    operation = Mock(name="browser_operation")
    monkeypatch.setattr(browser, "browser_operation", operation)
    instance = bare_browser()
    monkeypatch.setattr(instance, "fresh", Mock(return_value=False))
    with pytest.raises(StalePage, match="Observe again"):
        instance.act({"kind": "click", "node": 1, "id": "a0"}, page={})
    operation.assert_not_called()


def test_act_wait_sleeps_and_records_no_after_input(monkeypatch):
    operation = Mock(return_value={"executed": "a1"})
    monkeypatch.setattr(browser, "browser_operation", operation)
    sleeps = []
    monkeypatch.setattr(browser.time, "sleep", sleeps.append)
    instance = bare_browser()
    monkeypatch.setattr(instance, "fresh", Mock(return_value=True))

    action = {"kind": "wait", "id": "a1"}
    assert instance.act(action, page={}) == {"executed": "a1"}
    assert sleeps == [0.1]
    assert instance.after_input is None


def test_act_click_records_after_input_for_the_next_observe(monkeypatch):
    operation = Mock(return_value={"executed": "a2"})
    monkeypatch.setattr(browser, "browser_operation", operation)
    instance = bare_browser()
    monkeypatch.setattr(instance, "fresh", Mock(return_value=True))

    action = {"kind": "click", "node": 4, "id": "a2"}
    assert instance.act(action, page={}, text=None) == {"executed": "a2"}
    assert instance.after_input is action
    request = operation.call_args.args[0]
    assert request == {"operation": "act", "session": "S1", "action": action, "text": None}


# --- browser_operation act executor: real Input.* dispatch -------------------


def act_request(action, text=None):
    return {"operation": "act", "session": "S1", "action": action, "text": text}


def test_scroll_dispatches_a_mouse_wheel_and_nothing_else(fake_cdp):
    result = browser_operation(act_request({"kind": "scroll", "delta": 600, "id": "a3"}))
    assert result == {"executed": "a3"}
    assert fake_cdp.calls == [
        (
            "Input.dispatchMouseEvent",
            {
                "session_id": "S1",
                "_response_timeout": 30,
                "type": "mouseWheel",
                "x": 550,
                "y": 650,
                "deltaX": 0,
                "deltaY": 600,
            },
        )
    ]


def test_wait_performs_no_browser_calls(fake_cdp):
    assert browser_operation(act_request({"kind": "wait", "id": "a4"})) == {"executed": "a4"}
    assert fake_cdp.calls == []


def test_act_rejects_a_non_int_node_before_evaluating(fake_cdp):
    with pytest.raises(ValueError, match="Invalid observed node"):
        browser_operation(act_request({"kind": "click", "node": "3", "id": "a5"}))
    assert fake_cdp.calls == []


def test_click_presses_and_releases_at_the_resolved_point(fake_cdp):
    fake_cdp.script = [{"x": 12.5, "y": 40.0}]
    result = browser_operation(act_request({"kind": "click", "node": 3, "id": "a6"}))
    assert result == {"executed": "a6"}
    mouse = [(m, p) for m, p in fake_cdp.calls if m == "Input.dispatchMouseEvent"]
    assert [p["type"] for _, p in mouse] == ["mousePressed", "mouseReleased"]
    assert all(p["x"] == 12.5 and p["y"] == 40.0 and p["button"] == "left" for _, p in mouse)
    assert "Input.insertText" not in fake_cdp.methods()
    # The resolver receives the observed node id, never a selector.
    assert '"node": 3' in fake_cdp.evaluate_params()[0]["expression"]


def test_fill_clicks_selects_all_and_inserts_text(fake_cdp):
    fake_cdp.script = [{"x": 1.0, "y": 2.0}]
    result = browser_operation(act_request({"kind": "fill", "node": 5, "id": "a7"}, text="Zurich"))
    assert result == {"executed": "a7"}
    modifiers = 4 if sys.platform == "darwin" else 2  # Cmd on macOS, Ctrl elsewhere
    keys = [p for m, p in fake_cdp.calls if m == "Input.dispatchKeyEvent"]
    assert [k["type"] for k in keys] == ["keyDown", "keyUp"]
    assert keys[0]["commands"] == ["selectAll"] and keys[0]["modifiers"] == modifiers
    assert keys[0]["key"] == "a" and keys[0]["code"] == "KeyA"
    inserts = [p for m, p in fake_cdp.calls if m == "Input.insertText"]
    assert inserts == [{"session_id": "S1", "_response_timeout": 30, "text": "Zurich"}]
    # Mouse click into the field precedes the keyboard work.
    assert fake_cdp.methods().index("Input.dispatchMouseEvent") < fake_cdp.methods().index("Input.insertText")


# --- native date/time/color/range fill (upstream issue #227, fill half) ---
# Enumeration landed earlier, but every fill went through Input.insertText,
# which cannot commit a native segmented control: the live repro filled a
# date field and submitted it empty with zero input/change events. These
# pin the Python half against the fake transport; the JS half (native
# setter, rejection restore) runs for real in tests/test_live_cdp.py.


def test_native_fill_commits_inside_the_evaluate_without_typing(fake_cdp):
    fake_cdp.script = [{"x": 1.0, "y": 2.0, "filled": True, "value": "2027-03-01"}]
    action = {"kind": "fill", "node": 5, "input_type": "date", "id": "a16"}
    assert browser_operation(act_request(action, text="2027-03-01")) == {"executed": "a16"}
    # The value was committed in-page: no click, no select-all, no insertText.
    assert fake_cdp.methods() == ["Runtime.evaluate"]
    expression = fake_cdp.evaluate_params()[0]["expression"]
    # The fill text rides in the payload; the resolver commits it through
    # the native setter for exactly the native types.
    assert '"text": "2027-03-01"' in expression
    assert "HTMLInputElement.prototype" in expression
    for input_type in ("date", "datetime-local", "time", "month", "week", "color", "range"):
        assert f"'{input_type}'" in expression
    # The mutation stays after the covered check, like the SELECT mutation:
    # nothing may be committed before hittability is proven (#245).
    assert expression.index("cache.covered") < expression.index("filled:true")


def test_native_fill_rejection_is_loud_runtime_not_stale(fake_cdp):
    # A rejected value leaves the field untouched (the JS restores it), so
    # it must surface like the select path -- RuntimeError naming the bad
    # value -- never as StalePage, which the tick would retry verbatim.
    fake_cdp.script = [{"rejected": "not a valid date value: March 1, 2027"}]
    action = {"kind": "fill", "node": 5, "input_type": "date", "id": "a17"}
    with pytest.raises(RuntimeError, match="Native input fill rejected: not a valid date value"):
        browser_operation(act_request(action, text="March 1, 2027"))
    assert fake_cdp.methods() == ["Runtime.evaluate"]  # refused before any Input.* dispatch


def test_evaluation_failure_during_native_fill_is_not_reported_as_stale(fake_cdp):
    # The setter may already have committed when evaluation dies; like a
    # select, that is inspect-before-retrying, not a safe-to-re-observe
    # stale page. A plain text fill mutates only via Input.* afterwards,
    # so it keeps the stale verdict.
    failure = {"__response__": {"exceptionDetails": {"text": "destroyed"}}}
    fake_cdp.script = [dict(failure)]
    native = {"kind": "fill", "node": 3, "input_type": "time", "id": "a18"}
    with pytest.raises(RuntimeError, match="Native input execution was interrupted"):
        browser_operation(act_request(native, text="09:30"))

    fake_cdp.script = [dict(failure)]
    plain = {"kind": "fill", "node": 3, "input_type": "text", "id": "a19"}
    with pytest.raises(StalePage):
        browser_operation(act_request(plain, text="Zurich"))


def test_select_acts_inside_the_evaluate_without_mouse_input(fake_cdp):
    fake_cdp.script = [{"x": 3.0, "y": 4.0}]
    action = {"kind": "select", "node": 9, "value": "1", "option_index": 2, "id": "a8"}
    assert browser_operation(act_request(action)) == {"executed": "a8"}
    assert fake_cdp.methods() == ["Runtime.evaluate"]  # selection already applied in-page
    expression = fake_cdp.evaluate_params()[0]["expression"]
    assert "option_index" in expression  # exact-option selection (upstream #216 deviation)
    assert '"option_index": 2' in expression


def test_unresolvable_target_is_stale_for_clicks_but_runtime_for_select(fake_cdp):
    fake_cdp.script = [None]
    with pytest.raises(StalePage, match="Target changed"):
        browser_operation(act_request({"kind": "click", "node": 3, "id": "a9"}))

    fake_cdp.script = [None]
    with pytest.raises(RuntimeError, match="Dropdown execution was not confirmed"):
        browser_operation(act_request({"kind": "select", "node": 3, "id": "a10"}))


def test_evaluation_failure_during_select_is_not_reported_as_stale(fake_cdp):
    # A select may already have mutated the page when evaluation dies, so
    # it must surface as RuntimeError (inspect before retrying), not the
    # retryable StalePage used for other kinds.
    failure = {"__response__": {"exceptionDetails": {"text": "destroyed"}}}
    fake_cdp.script = [dict(failure)]
    with pytest.raises(RuntimeError, match="Dropdown execution was interrupted"):
        browser_operation(act_request({"kind": "select", "node": 3, "id": "a11"}))

    fake_cdp.script = [dict(failure)]
    with pytest.raises(StalePage):
        browser_operation(act_request({"kind": "click", "node": 3, "id": "a12"}))

    fake_cdp.script = [dict(failure)]
    with pytest.raises(StalePage):
        browser_operation({"operation": "observe", "session": "S1", "screenshot": False})


# --- covered targets (upstream PR #245): proven pre-input, observe again ---
# The snapshot remembers a node whose act-time hit test failed and omits it
# from later offers until its centre is hittable again (the JS half is
# exercised for real in tests/test_live_cdp.py). These pin the Python half:
# a covered verdict from the resolver is a distinct, retryable rejection --
# never mouse input, never an unconfirmed-mutation RuntimeError -- and the
# cached page must stop reading as fresh, because its offered set is now
# known wrong even though the occlusion-blind marker is unchanged (the
# manual predict/act hole flagged in #245's review).


def test_covered_click_is_a_distinct_preinput_rejection(fake_cdp):
    fake_cdp.script = [{"covered": True}]
    with pytest.raises(CoveredTarget, match="covered"):
        browser_operation(act_request({"kind": "click", "node": 3, "id": "a13"}))
    assert issubclass(CoveredTarget, StalePage)  # tick's stale handling still applies
    assert fake_cdp.methods() == ["Runtime.evaluate"]  # refused before any input dispatch
    # The resolver records the node in the snapshot cache so the next
    # observe can omit it; it still receives only the observed node id.
    expression = fake_cdp.evaluate_params()[0]["expression"]
    assert "cache.covered" in expression and '"node": 3' in expression


def test_covered_select_is_retryable_not_an_unconfirmed_mutation(fake_cdp):
    # Covered is proven before selectedIndex is touched, so unlike an
    # interrupted dropdown it may simply re-observe (#245).
    fake_cdp.script = [{"covered": True}]
    with pytest.raises(CoveredTarget, match="covered"):
        browser_operation(act_request({"kind": "select", "node": 3, "value": "1", "id": "a14"}))
    assert fake_cdp.methods() == ["Runtime.evaluate"]


def test_covered_rejection_makes_the_cached_page_unfresh_until_observe(fake_cdp, monkeypatch):
    operation = Mock(side_effect=CoveredTarget("Target is covered. Observe again."))
    monkeypatch.setattr(browser, "browser_operation", operation)
    instance = bare_browser()
    monkeypatch.setattr(instance, "fresh", Mock(return_value=True))  # act-time per-node gate
    with pytest.raises(CoveredTarget):
        instance.act({"kind": "click", "node": 1, "id": "a15"}, page={})

    real_fresh = CdpBrowser.fresh.__get__(instance)
    assert real_fresh(guard_page()) is False  # known-wrong offer: no marker evaluation needed
    assert fake_cdp.evaluate_params() == []

    monkeypatch.setattr(browser, "browser_operation", Mock(return_value=dict(INFO)))
    instance.observe(screenshot=False)  # the re-observe clears the flag
    fake_cdp.script = ["M1"]
    assert real_fresh(guard_page()) is True
