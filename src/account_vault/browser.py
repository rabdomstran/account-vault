"""Perceive/act for live pages over CDP; one session, no per-step subprocess.

Ported from jev_ultrafast/browser.py (MIT, browser-use/jev-ultrafast).
Kept intact: atomic single-evaluate observation, WeakMap node identity,
semantic freshness (marker / per-node guards), geometry + occlusion
re-checked immediately before input, and no retry of any browser mutation.
DEVIATION comments mark deliberate differences.
"""

import hashlib
import json
import os
import sys
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

# DEVIATION: upstream imports browser_harness at module top. It is imported
# lazily inside the CDP paths here so `import account_vault`, the offline
# static-file backend, and unit tests never require a running Chrome/daemon
# (same import-time-crash concern as upstream issue #16).
_cdp: Callable[..., Any] | None = None
_ensure_daemon: Callable[..., Any] | None = None


def _harness() -> tuple[Callable[..., Any], Callable[..., Any]]:
    global _cdp, _ensure_daemon
    if _cdp is None or _ensure_daemon is None:
        from browser_harness.admin import ensure_daemon
        from browser_harness.helpers import cdp

        _cdp, _ensure_daemon = cdp, ensure_daemon
    assert _cdp is not None and _ensure_daemon is not None  # set just above
    return _cdp, _ensure_daemon


def cdp(method: str, **params: Any) -> Any:
    """Indirection kept so tests can monkeypatch account_vault.browser.cdp."""
    real_cdp, _ = _harness()
    return real_cdp(method, **params)


# DEVIATION: never attach to the user's everyday browser by default
# (upstream issue #16, part a). Unpinned, browser-harness discovers a
# local Chrome from the profile directories' DevToolsActivePort files
# (falling back to probing 9222/9223) and may even launch it -- i.e. the
# daily profile with its logged-in tabs -- and upstream calls
# ensure_daemon() straight into that. For an account vault that default
# is the hazard itself, so construction requires either a pinned CDP
# endpoint (a dedicated automation Chrome the caller launched) or an
# explicit real-browser opt-in, and the live daemon's self-reported
# browser kind is checked afterwards: ensure_daemon() reuses any
# healthy daemon under BU_NAME, so a pin set after a local daemon
# started would otherwise be silently ignored (the PR #230 trap).
_DEDICATED_HOWTO = (
    "Launch a dedicated Chrome with its own profile, e.g.:\n"
    '  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" '
    "--remote-debugging-port=9333 --user-data-dir=/tmp/account-vault-chrome about:blank\n"
    "then pin the harness to it under a fresh daemon name:\n"
    "  BU_CDP_URL=http://127.0.0.1:9333 BU_NAME=account-vault <your command>\n"
    "(BU_CDP_WS=<webSocketDebuggerUrl from http://127.0.0.1:9333/json/version> also pins.)\n"
    "To knowingly drive your everyday browser instead, set ACCOUNT_VAULT_ALLOW_REAL_BROWSER=1."
)

_daemon_browser_kind: Callable[..., Any] | None = None


def _kind_of_daemon() -> str | None:
    """'cloud' | 'cdp' | 'local' from the live harness daemon, else None."""
    global _daemon_browser_kind
    if _daemon_browser_kind is None:
        from browser_harness.admin import daemon_browser_kind

        _daemon_browser_kind = daemon_browser_kind
    kind = _daemon_browser_kind()
    return kind if kind in {"cloud", "cdp", "local"} else None


def _real_browser_opt_in() -> bool:
    return os.environ.get("ACCOUNT_VAULT_ALLOW_REAL_BROWSER", "").strip().lower() in {"1", "true", "yes", "on"}


def _pinned_cdp_endpoint() -> str:
    return (os.environ.get("BU_CDP_WS") or os.environ.get("BU_CDP_URL") or "").strip()


def _guard_attach() -> bool:
    """Refuse an unpinned, unacknowledged attach before any daemon work.

    Returns the real-browser opt-in so the post-attach kind check can
    apply the same allowance. Runs before ensure_daemon(): no daemon is
    spawned, no Chrome is launched, nothing attaches on refusal.
    """
    opt_in = _real_browser_opt_in()
    pinned = _pinned_cdp_endpoint()
    if not pinned and not opt_in:
        raise RuntimeError(
            "Refusing to attach: no dedicated CDP endpoint is pinned (BU_CDP_URL/BU_CDP_WS unset), "
            "so browser-harness would discover -- or launch -- your everyday Chrome and drive its "
            "logged-in profile. " + _DEDICATED_HOWTO
        )
    if not pinned:
        warnings.warn(
            "ACCOUNT_VAULT_ALLOW_REAL_BROWSER is set with no pinned CDP endpoint: this run will "
            "attach to your everyday Chrome profile, including its logged-in tabs.",
            UserWarning,
            stacklevel=3,
        )
    return opt_in


def _verify_daemon_kind(opt_in: bool) -> str | None:
    """Check what the (possibly reused) daemon is actually attached to."""
    kind = _kind_of_daemon()
    pinned = _pinned_cdp_endpoint()
    if kind == "local" and pinned and opt_in:
        warnings.warn(
            "A pinned CDP endpoint is set, but the already-running harness daemon is attached to "
            "a local (everyday) Chrome and was reused, so the pin did not take effect. "
            "Use a fresh BU_NAME to honor the pin.",
            UserWarning,
            stacklevel=3,
        )
        return kind
    if kind == "local" and not opt_in:
        raise RuntimeError(
            "Refusing to drive this browser: the running harness daemon is attached to a local "
            "(everyday) Chrome, not the pinned CDP endpoint -- ensure_daemon() reuses a healthy "
            "daemon under BU_NAME, so the pin was ignored. Stop that daemon or use a fresh "
            "BU_NAME. " + _DEDICATED_HOWTO
        )
    if kind is None and not opt_in:
        raise RuntimeError(
            "Refusing to drive this browser: the harness daemon did not report what it is "
            "attached to (it may predate browser-kind reporting), so a dedicated profile cannot "
            "be verified. Restart it under a fresh BU_NAME. " + _DEDICATED_HOWTO
        )
    return kind


# DEVIATION: native value controls Input.insertText cannot commit. Typing
# into a segmented date/time control, a color picker or a range slider
# changes nothing (or, for range, the preceding centre click sets a
# midpoint), yet the run reports success and submits the field empty or
# wrong (upstream issue #227, fill half; enumeration landed earlier).
# Fills for these types commit through the native value setter inside
# the act resolver (act.js). Its JS list is generated from this set.
NATIVE_FILL_TYPES = frozenset({"date", "datetime-local", "time", "month", "week", "color", "range"})

# Perception tables shared verbatim by snapshot.js (live) and static_dom
# (offline twin). They used to be spelled once per implementation; the
# JS copies are now generated from these constants at load (tokens in
# snapshot.js, like act.js's __NATIVE_FILL_TYPES__), and static_dom
# imports them, so the two engines cannot drift on what a role or an
# excluded input type is. See docs/static-live-parity.md for what still
# (necessarily) differs between the backends.
ELEMENT_ROLES = (
    "button",
    "link",
    "checkbox",
    "radio",
    "switch",
    "tab",
    "menuitem",
    "menuitemradio",
    "option",
    "gridcell",
    "combobox",
    "textbox",
    "searchbox",
    "spinbutton",
    "slider",
)
SAFE_EXCLUDE = frozenset({"password", "file", "hidden"})
EDITABLE_ROLES = ("textbox", "searchbox", "spinbutton", "slider")

# DEVIATION: explicit CDP IPC deadlines (upstream issue #14, PRs #97/#19,
# and branch codex/bu-bench-eval-v2 commits a2676ee/6cf62ca, diffed
# before adopting). browser-harness defaults every call to 5s, which
# slow navigations/evaluations legitimately exceed; session calls get
# 30s -- a deadline, not a delay, and never a reason to retry: a timed-
# out mutation has an unknown result and propagates for inspection.
# Screenshots keep the original short 5s: capture on a throttled
# background tab can stall indefinitely, and the observation degrades
# to no-screenshot below instead of hanging or dying with the run.
CDP_RESPONSE_TIMEOUT = 30
SCREENSHOT_RESPONSE_TIMEOUT = 5

# DEVIATION: await wheel-scroll commit before observing (interaction of
# PR #212's proven-progress rule with PR #209's no-op drop, found by the
# CI live gate). A dispatched mouseWheel commits its scroll offset to
# the main thread asynchronously; on an idle headless/background tab
# that commit can lag the post-input settle (2 rAF / 50ms, built for
# autocomplete popups) by hundreds of ms. Observe then reads scrollY
# unchanged, history records a no-op, #209 drops scroll_down from the
# next offer, and the run blocks mid-page with the target still below
# (Linux headless Chrome 157: scrolls read 0->560 then 560->560, while a
# live poll seconds later showed 1120). After a scroll act, poll
# scrollY until it moves in the scrolled direction or this deadline
# passes; a genuine boundary no-op pays the deadline once, then the
# existing no-op machinery proceeds exactly as before.
SCROLL_SETTLE_TIMEOUT = 3.0
SCROLL_SETTLE_POLL = 0.05

# DEVIATION: provable-boundary early exit (docs/latency-results.md,
# measured boundary observe 3,076 ms). Each poll also reads the max
# scroll offset, atomically with scrollY. At the directional limit
# no scroll/scrollend event will ever arrive, so waiting buys no
# information: two consecutive at-limit polls (snapshot.js's own
# 2 px offer gate, so offer-time and wait-time agree) end the wait
# early. This proves only "the root viewport cannot move further
# that way", never "this wheel was processed" (CDP dispatch
# resolves on ACK before DOM dispatch), so every unprovable state
# — movement still possible, growing page, pinch-zoom (scrollY is
# the layout offset while the wheel may move only the visual
# viewport) — keeps the full deadline exactly as before.
SCROLL_POLL = (
    "[window.scrollY, document.documentElement.scrollHeight - window.innerHeight, "
    "window.visualViewport ? window.visualViewport.scale : 1, "
    "...(() => { const c = window.__jevWheelChain; if (!c) return [0, 0]; "
    "let d = 0, u = 0; c.els.forEach((e, i) => { if (e.isConnected) { "
    "const m = e.scrollTop - c.tops[i]; if (m > d) d = m; if (-m > u) u = -m; } }); "
    "return [d, u]; })()]"
)

# DEVIATION: nested-scroller swallow detection (the boundary exit's
# remaining case, docs/jev-ultrafast-port.md). Chromium hit-tests a
# wheel to the innermost scroller under the point and latches the
# gesture to it; the delta chains to ancestors only once that
# scroller is at its limit and overscroll-behavior allows bubbling
# (crbug 41378182). No surveyed automation project waits on any of
# this -- Playwright's and Puppeteer's mouse.wheel are the same
# fire-and-forget CDP dispatch used below, and Stagehand (issue
# #276) cannot even offer nested scrollers -- and Chromium exposes
# no "wheel consumed by X" signal: the dispatch ACK precedes DOM
# handling, exactly as for the root. What is provable is narrower:
# if a scroller under the fixed wheel point moves in the scrolled
# direction, the wheel was processed (by it), so the commit wait
# can end just as root movement ends it. The probe below records
# the scrollable ancestor chain under the point (and its offsets)
# immediately before dispatch; SCROLL_POLL's last two values are
# the chain's largest down/up movement since. The mirror case --
# a nested scroller already at its own limit with
# overscroll-behavior: contain/none, a non-passive wheel
# preventDefault, or a scroller inside an iframe/shadow tree the
# probe cannot see -- moves nothing anywhere and is
# indistinguishable from a merely late root commit, so it keeps
# the full deadline deliberately: a false early exit there would
# reintroduce the #212/#209 mid-page drop the wait exists to
# prevent.
WHEEL_X = 550
WHEEL_Y = 650
SCROLL_CHAIN_PROBE = (
    f"(() => {{ try {{ const chain = []; let n = document.elementFromPoint({WHEEL_X}, {WHEEL_Y}); "
    "while (n && n !== document.documentElement && n !== document.body) { "
    "if (n instanceof Element) { const s = getComputedStyle(n); "
    "if ((s.overflowY === 'auto' || s.overflowY === 'scroll' || s.overflowY === 'overlay') "
    "&& n.scrollHeight > n.clientHeight + 1) chain.push(n); } "
    "n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null; } "
    "window.__jevWheelChain = {els: chain, tops: chain.map((e) => e.scrollTop)}; "
    "return chain.length; } catch { window.__jevWheelChain = null; return 0; } })()"
)


def _deadline(method: str) -> int:
    return SCREENSHOT_RESPONSE_TIMEOUT if method == "Page.captureScreenshot" else CDP_RESPONSE_TIMEOUT


def _js_list(values: Any) -> str:
    return "[" + ",".join(f"'{v}'" for v in values) + "]"


# In-page programs live beside snapshot.js as .js files, loaded once:
# READ_STATE observes, AFTER_INPUT_SETTLE waits out the post-input settle,
# ACT_RESOLVER re-resolves a target's geometry/occlusion before input.
# Shared perception tables ride into the JS as __TOKENS__ replaced here
# from the Python constants above, so no JS list can drift from them.
_PACKAGE_DIR = Path(__file__).parent
_READ_STATE_TOKENS = {
    "__ELEMENT_ROLES__": _js_list(ELEMENT_ROLES),
    "__SAFE_EXCLUDE__": _js_list(sorted(SAFE_EXCLUDE)),
    "__EDITABLE_ROLES__": _js_list(EDITABLE_ROLES),
}
READ_STATE = (_PACKAGE_DIR / "snapshot.js").read_text(encoding="utf-8")
for _token, _list in _READ_STATE_TOKENS.items():
    assert READ_STATE.count(_token) == 1, _token  # each table spelled once in the JS
    READ_STATE = READ_STATE.replace(_token, _list)
AFTER_INPUT_SETTLE = (_PACKAGE_DIR / "settle.js").read_text(encoding="utf-8").rstrip("\n")
ACT_RESOLVER = (
    (_PACKAGE_DIR / "act.js")
    .read_text(encoding="utf-8")
    .rstrip("\n")
    .replace("__NATIVE_FILL_TYPES__", _js_list(sorted(NATIVE_FILL_TYPES)))
)
assert "__NATIVE_FILL_TYPES__" not in ACT_RESOLVER  # token replaced above
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class CoveredTarget(StalePage):
    """The target's centre is covered; refused before any input (#245).

    Distinct from a plain stale page: nothing was dispatched, the node is
    recorded in the snapshot cache, and the next observe omits it until
    its centre is hittable again -- so re-observing is always safe.
    """


def _evaluated_value(response: dict[str, Any], interrupted: str | None = None) -> Any:
    """Unwrap a Runtime.evaluate response. An exception during a select or
    native fill may follow a real mutation, so those callers pass an
    `interrupted` message and get a RuntimeError (inspect, never retry);
    otherwise the document simply changed under the evaluation."""
    if response.get("exceptionDetails"):
        if interrupted is not None:
            raise RuntimeError(interrupted)
        raise StalePage("Document changed during evaluation")
    return response.get("result", {}).get("value")


class CdpBrowser:
    after_input: dict[str, Any] | None
    # What the harness daemon reports it is attached to (#16 guard):
    # "cdp" (pinned endpoint), "cloud", or "local" (everyday Chrome,
    # reachable only via the explicit opt-in).
    browser_kind: str | None
    # True after a covered rejection until the next observe: the cached
    # page's offered set is known wrong (it still lists the covered node)
    # even though its occlusion-blind marker still matches.
    _offer_stale: bool
    # Viewport Y captured by act() for a scroll, consumed by the next
    # observe()'s commit wait (see SCROLL_SETTLE_TIMEOUT). None when the
    # last act was not a scroll or its page carried no scroll position.
    _scroll_before_y: float | None

    def __init__(self, url: str) -> None:
        opt_in = _guard_attach()  # #16: refuse/warn before any daemon work
        _, ensure_daemon = _harness()
        ensure_daemon()
        self.browser_kind = _verify_daemon_kind(opt_in)
        self.after_input = None
        self._offer_stale = False
        self._scroll_before_y = None
        self.target: str | None = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)

    def call(self, method: str, **params: Any) -> Any:
        return cdp(method, session_id=self.session, _response_timeout=_deadline(method), **params)

    def evaluate(self, expression: str) -> Any:
        return _evaluated_value(self.call("Runtime.evaluate", expression=expression, returnByValue=True))

    def observe(self, screenshot: bool = True) -> dict[str, Any]:
        acted: dict[str, Any] | None = None
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            acted = action
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression=AFTER_INPUT_SETTLE + "(" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        before_y = getattr(self, "_scroll_before_y", None)
        self._scroll_before_y = None
        if acted is not None and acted.get("kind") == "scroll" and before_y is not None:
            self._await_scroll_commit(before_y, acted.get("delta") or 0)
        # On a dedicated/cloud daemon the owned tab may be activated
        # before capture (a hidden tab emits no compositor frame and
        # the capture stalls -- upstream PR #43); on an opted-in local
        # (everyday) browser it must never steal the foreground (#16).
        activate = screenshot and getattr(self, "browser_kind", None) in {"cdp", "cloud"}
        last_error: StalePage | None = None
        for attempt in range(10):
            try:
                result = browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot, "activate": activate}
                )
            except StalePage as error:
                last_error = error
                if attempt < 9:
                    time.sleep(0.02)
            else:
                # This observation's offered set is current (covered
                # nodes omitted, restored nodes back); trust it again.
                self._offer_stale = False
                return result
        assert last_error is not None  # the tenth failure lands here
        raise last_error

    def _await_scroll_commit(self, before_y: float, delta: float) -> None:
        """Poll until a dispatched wheel scroll commits (see the
        SCROLL_SETTLE_TIMEOUT deviation). Movement in the scrolled
        direction is the exit -- the root viewport's, or the nested
        chain under the wheel point (see SCROLL_CHAIN_PROBE): either
        proves the wheel was processed. A still viewport at the
        deadline is a genuine no-op and the caller observes it as
        one. Two consecutive polls proving the viewport is already
        at its directional limit also exit (see SCROLL_POLL): no
        commit can follow, so the remaining deadline buys no
        information."""
        if not delta:
            return
        deadline = time.monotonic() + SCROLL_SETTLE_TIMEOUT
        at_limit_polls = 0
        while time.monotonic() < deadline:
            try:
                reading = self.evaluate(SCROLL_POLL)
            except (RuntimeError, StalePage):
                return  # navigating/interrupted; the snapshot retry owns it
            numbers = (
                [reading]
                if isinstance(reading, (int, float)) and not isinstance(reading, bool)
                else reading
                if isinstance(reading, (list, tuple))
                else []
            )
            values = [v if isinstance(v, (int, float)) and not isinstance(v, bool) else None for v in numbers[:5]]
            y = values[0] if values else None
            max_y = values[1] if len(values) > 1 else None
            scale = values[2] if len(values) > 2 else None
            chain_down = values[3] if len(values) > 3 else None
            chain_up = values[4] if len(values) > 4 else None
            if y is not None:
                if (delta > 0 and y > before_y) or (delta < 0 and y < before_y):
                    return
                # A nested scroller under the wheel point moved in the
                # scrolled direction: it latched and consumed the
                # wheel, so the root will not follow from this
                # dispatch and the deadline buys nothing. The
                # snapshot still records the root as unmoved, so the
                # no-op machinery proceeds exactly as after a full
                # wait -- only the 3 s is saved.
                if (delta > 0 and chain_down) or (delta < 0 and chain_up):
                    return
                at_limit = (
                    max_y is not None and scale == 1 and ((delta > 0 and y >= max_y - 2) or (delta < 0 and y <= 0))
                )
                at_limit_polls = at_limit_polls + 1 if at_limit else 0
                if at_limit_polls >= 2:
                    return
            time.sleep(SCROLL_SETTLE_POLL)

    def fresh(self, page: dict[str, Any], action: dict[str, Any] | None = None) -> bool:
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return bool(current == [page["page_key"], page["guards"].get(str(node))])
        # DEVIATION: after a covered rejection the cached page still offers
        # a control the snapshot will now omit, but its marker deliberately
        # ignores occlusion and would still match. Without this, manual
        # predict/act callers (the inspector) reoffer the covered target
        # forever -- the hole flagged in upstream PR #245's review. Force
        # the re-observe every path already takes on a stale marker.
        if getattr(self, "_offer_stale", False):
            return False
        return bool(self.evaluate(MARKER) == page["marker"])

    def act(self, action: dict[str, Any], page: dict[str, Any], text: str | None = None) -> Any:
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        try:
            result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        except CoveredTarget:
            self._offer_stale = True  # cached page offers a now-hidden node
            raise
        self.after_input = action if action["kind"] != "wait" else None
        scroll = page.get("scroll")
        before_y = scroll.get("y") if isinstance(scroll, dict) else None
        self._scroll_before_y = (
            float(before_y)
            if action["kind"] == "scroll" and isinstance(before_y, (int, float)) and not isinstance(before_y, bool)
            else None
        )
        return result

    def close(self) -> None:
        if getattr(self, "target", None):
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


# Upstream name kept: Browser is the CDP backend.
Browser = CdpBrowser


def fingerprint(state: dict[str, Any]) -> str:
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def browser_operation(request: dict[str, Any]) -> dict[str, Any]:
    operation = request["operation"]
    session = request["session"]

    def call(method: str, **params: Any) -> Any:
        return cdp(method, session_id=session, _response_timeout=_deadline(method), **params)

    def evaluate(expression: str) -> Any:
        # A select or a native fill may already have mutated the page when
        # evaluation dies, so those surface as RuntimeError (inspect before
        # retrying), never the retryable StalePage.
        interrupted = None
        if operation == "act":
            action = request["action"]
            if action["kind"] == "select":
                interrupted = "Dropdown execution was interrupted; inspect before retrying."
            elif action["kind"] == "fill" and action.get("input_type") in NATIVE_FILL_TYPES:
                interrupted = "Native input execution was interrupted; inspect before retrying."
        return _evaluated_value(
            call("Runtime.evaluate", expression=expression, returnByValue=True), interrupted=interrupted
        )

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            # Record the scrollable chain under the wheel point so the
            # commit wait can tell a nested swallow from a late root
            # commit (see SCROLL_CHAIN_PROBE). Best-effort: the probe
            # is read-only and a page changing under it must never
            # block the dispatch it only observes.
            try:
                evaluate(SCROLL_CHAIN_PROBE)
            except (RuntimeError, StalePage):
                pass
            call("Input.dispatchMouseEvent", type="mouseWheel", x=WHEEL_X, y=WHEEL_Y, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            # `text` lives on the request, not the action; the native fill
            # path inside the evaluation needs it (#227). It rides along
            # for every kind and is ignored by the others.
            payload = {**action, "text": request.get("text")}
            target = evaluate(ACT_RESOLVER + "(" + json.dumps(payload) + ")")
            if target == {"covered": True}:
                raise CoveredTarget("Target is covered. Observe again.")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed. Observe again.")
            # Native fill verdicts (#227): a rejection proved the value
            # unusable with the field left untouched, so fail loudly like
            # the select path instead of letting the run submit empty;
            # a commit already happened in-page, so skip mouse/typing.
            if target.get("rejected"):
                raise RuntimeError(f"Native input fill rejected: {target['rejected']}")
            if target.get("filled"):
                return {"executed": action["id"]}
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info: dict[str, Any] = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        if request.get("activate"):
            # Best effort (#43): a hidden tab never produces a frame to
            # capture. Failure here must not pre-empt the capture's own
            # verdict below.
            try:
                call("Page.bringToFront")
            except (RuntimeError, TimeoutError):
                pass
        try:
            info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
        except TimeoutError:
            # DEVIATION (#14, PR #19): screenshots never drive the loop,
            # so a stalled capture degrades this observation to
            # no-screenshot (explicit None, distinct from "not
            # requested") instead of discarding the completed
            # structured read and pausing the run. Other CDP failures
            # still propagate.
            info["screenshot"] = None
    return info
