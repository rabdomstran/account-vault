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
# the act resolver below. The JS list there mirrors this set.
NATIVE_FILL_TYPES = frozenset({"date", "datetime-local", "time", "month", "week", "color", "range"})

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


def _deadline(method: str) -> int:
    return SCREENSHOT_RESPONSE_TIMEOUT if method == "Page.captureScreenshot" else CDP_RESPONSE_TIMEOUT


# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8")
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class CoveredTarget(StalePage):
    """The target's centre is covered; refused before any input (#245).

    Distinct from a plain stale page: nothing was dispatched, the node is
    recorded in the snapshot cache, and the next observe omits it until
    its centre is hittable again -- so re-observing is always safe.
    """


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

    def __init__(self, url: str) -> None:
        opt_in = _guard_attach()  # #16: refuse/warn before any daemon work
        _, ensure_daemon = _harness()
        ensure_daemon()
        self.browser_kind = _verify_daemon_kind(opt_in)
        self.after_input = None
        self._offer_stale = False
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
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot: bool = True) -> dict[str, Any]:
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))("""
                    + json.dumps(action)
                    + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        # On a dedicated/cloud daemon the owned tab may be activated
        # before capture (a hidden tab emits no compositor frame and
        # the capture stalls -- upstream PR #43); on an opted-in local
        # (everyday) browser it must never steal the foreground (#16).
        activate = screenshot and getattr(self, "browser_kind", None) in {"cdp", "cloud"}
        for attempt in range(10):
            try:
                result = browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot, "activate": activate}
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
            else:
                # This observation's offered set is current (covered
                # nodes omitted, restored nodes back); trust it again.
                self._offer_stale = False
                return result
        raise StalePage("Page did not settle")

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
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            # A select or a native fill may already have mutated the page
            # when evaluation dies, so surface both as RuntimeError
            # (inspect before retrying), never the retryable StalePage.
            if operation == "act":
                action = request["action"]
                if action["kind"] == "select":
                    raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
                if action["kind"] == "fill" and action.get("input_type") in NATIVE_FILL_TYPES:
                    raise RuntimeError("Native input execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            # `text` lives on the request, not the action; the native fill
            # path inside the evaluation needs it (#227). It rides along
            # for every kind and is ignored by the others.
            payload = {**action, "text": request.get("text")}
            target = evaluate(
                """(action => {
              const cache=window.__jevFast, e=cache?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              cache.hits ??= ((el,px,py)=>el.contains(document.elementFromPoint(px,py)));
              if (!cache.hits(e,x,y)) {
                // DEVIATION: remember the covered node so the snapshot
                // omits it until hittable again (upstream PR #245).
                // Proven before any input -- including the SELECT
                // mutation below -- so re-observing cannot double-act.
                (cache.covered ??= new Set()).add(action.node);
                return {covered:true};
              }
              if (action.kind==='select') {
                if (e.tagName!=='SELECT') return null;
                // DEVIATION: upstream checks that SOME enabled option has the
                // value, then assigns e.value — whose setter selects the
                // first option with that value, disabled ones included, so
                // duplicate-valued options pick the wrong row (upstream
                // issue #216). Select the exact observed option_index
                // instead (falling back to value+label for old snapshots).
                let idx = Number.isInteger(action.option_index) ? action.option_index
                  : [...e.options].findIndex(o=>o.value===action.value &&
                      !o.disabled && !o.closest('optgroup[disabled]'));
                const chosen = e.options[idx];
                if (!chosen || chosen.disabled || chosen.closest('optgroup[disabled]')) return null;
                e.selectedIndex = idx;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              if (action.kind==='fill' && ['date','datetime-local','time','month','week','color','range']
                  .includes((e.type||'').toLowerCase())) {
                // DEVIATION: Input.insertText does not commit into a
                // native date/time/color/range control, so the generic
                // click-and-type path silently submits these fields
                // empty (or, for range, at its centre-click value) --
                // upstream issue #227. Commit through the native
                // HTMLInputElement value setter instead (so React's
                // value tracker sees a real change), then fire the
                // events an edit would. The browser stays the format
                // authority: an invalid date/time string sanitises to
                // empty. Color/range sanitise to a default instead of
                // empty, so those two are shape-checked up front. A
                // rejected value restores the previous one and reports
                // distinctly -- never as a stale target the tick loop
                // would retry with the same text forever. Still after
                // the covered check above: no mutation before that.
                if (typeof action.text!=='string' || !action.text.trim())
                  return {rejected:'no value supplied for '+e.type+' input'};
                if (e.type==='color' && !/^#[0-9a-f]{6}$/i.test(action.text))
                  return {rejected:'not a valid color value: '+action.text};
                if (e.type==='range' && !Number.isFinite(Number(action.text)))
                  return {rejected:'not a valid range value: '+action.text};
                e.focus();
                const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set;
                const assign=v=>{ if (setter) setter.call(e,v); else e.value=v; };
                const before=e.value;
                assign(action.text);
                if (!e.value) { assign(before); return {rejected:'not a valid '+e.type+' value: '+action.text}; }
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
                return {x,y,filled:true,value:e.value};
              }
              return {x,y};
            })("""
                + json.dumps(payload)
                + ")"
            )
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
