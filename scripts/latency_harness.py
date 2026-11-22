"""Per-stage latency harness for the perceive -> decide -> act loop.

Times every stage of one loop step separately so the port's "ultrafast"
claim stops being inherited from upstream (browser-use/jev-ultrafast
docs/performance.md: 7.073 s Google Flights, 178 ms median decide) and
the TinyFish field report cited in docs/jev-ultrafast-issue-analysis.md
section 6 (142 runs: decide 102 ms = 9% of a 1.25 s step; acting +
waiting for the page to settle = 64%).

Stages (disjoint; they sum to the tick wall time, see aggregate_step):

- snapshot  executing snapshot.js (CDP: the Runtime.evaluate of
  READ_STATE, including the full re-execution hidden inside every
  MARKER freshness check; static: StaticBrowser._build()).
- settle    the post-input browser wait inside the next observe():
  the capped 2-rAF promise (50 ms, 200 ms for combobox fills) plus
  the wheel-scroll commit poll. Zero by construction offline.
- observe   the rest of observe(): CDP/IPC framing, fingerprint
  hashing, retries, screenshot when requested (the loop default is
  no screenshot, and that is what is measured here).
- decide    decider.choose() wall time. With the offline doubles this
  is local CPU only; the wire-shape micro-benchmark below isolates
  the request-building part of a real decide call so the missing
  network/model time can be reasoned about separately.
- act       browser.act() dispatch: the pre-input freshness guard,
  the in-page target resolver, and the Input.* calls.
- text      the TYPE_TEXT helper call (MappingTextProvider offline).
- other     residual: Agent bookkeeping and freshness-check framing.
  Reported, never hidden; a healthy run keeps it small.

No product code is modified. Timing wraps the injected browser /
decider / text provider, taps the single account_vault.browser.cdp
choke point (the same seam the fake-CDP tests monkeypatch), and wraps
StaticBrowser._build on the instance. Deciders are the offline
doubles throughout: what is measured is this port's machinery, not
hosted-model quality or latency. Results land in
docs/latency-measurement.json; docs/latency-results.md reads them.

    uv run python scripts/latency_harness.py --quick          # static smoke
    uv run python scripts/latency_harness.py                  # static, full N
    ACCOUNT_VAULT_LIVE=1 uv run python scripts/latency_harness.py --cdp
"""

import argparse
import json
import math
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("snapshot", "settle", "observe", "decide", "act", "text", "other")


# --------------------------------------------------------------------------
# Statistics (pure; unit-tested in tests/test_latency_harness.py)
# --------------------------------------------------------------------------


def percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile of an already-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = math.floor(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] + frac * (sorted_values[high] - sorted_values[low])


def summarize(values_ms: list[float]) -> dict[str, float | int]:
    """Distribution summary in ms: n, mean, stdev, min, p50/p90/p95/p99, max."""
    if not values_ms:
        return {
            "n": 0,
            "mean": 0.0,
            "stdev": 0.0,
            "min": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
        }
    ordered = sorted(values_ms)
    mean = sum(ordered) / len(ordered)
    variance = sum((v - mean) ** 2 for v in ordered) / len(ordered)
    return {
        "n": len(ordered),
        "mean": mean,
        "stdev": math.sqrt(variance),
        "min": ordered[0],
        "p50": percentile(ordered, 50),
        "p90": percentile(ordered, 90),
        "p95": percentile(ordered, 95),
        "p99": percentile(ordered, 99),
        "max": ordered[-1],
    }


def classify_cdp(method: str, params: dict[str, Any], *, read_state: str, marker: str) -> str:
    """Bucket one CDP call into a leaf stage from its method/expression.

    Order matters: the settle promise and the act resolver both start
    with "(action =>", and MARKER wraps the whole READ_STATE script,
    so exact matches are tested before prefixes.
    """
    if method == "Runtime.evaluate":
        expression = params.get("expression") or ""
        if expression == read_state:
            return "snapshot"
        if expression == marker:
            return "snapshot"  # freshness check = full snapshot re-run
        if expression == "window.scrollY" or expression.startswith("[window.scrollY,"):
            return "settle"  # scroll-commit poll inside observe()
        if expression.startswith("(action => new Promise"):
            return "settle"  # post-input 2-rAF wait inside observe()
        if "c.guard(" in expression or "c.pageKey()" in expression:
            return "fresh_guard"
        if expression.startswith("(action => {"):
            return "act_resolve"
        if expression == "document.readyState":
            return "setup"
        return "eval_other"
    if method.startswith("Input."):
        return "act_input"
    if method == "Page.captureScreenshot":
        return "screenshot"
    if method in {"Target.createTarget", "Target.attachToTarget", "Target.closeTarget", "Page.navigate"}:
        return "setup"
    if method.startswith("Emulation."):
        return "setup"
    return "cdp_other"


# --------------------------------------------------------------------------
# Recording: leaf spans + walls, one bucket per phase/step
# --------------------------------------------------------------------------


@dataclass
class Recorder:
    """Single-threaded event sink. The timed proxies set `context`
    (observe/fresh/act) around each delegated call so leaf spans from
    the CDP tap or _build wrapper know which wall contains them."""

    bucket: str = "setup"
    context: str = "setup"
    events: list[dict[str, Any]] = field(default_factory=list)

    def add(self, stage: str, ns: int, **meta: Any) -> None:
        self.events.append({"bucket": self.bucket, "context": self.context, "stage": stage, "ns": ns, **meta})

    def bucket_events(self, bucket: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["bucket"] == bucket]


def _sum_ns(events: list[dict[str, Any]], stage: str, context: str | None = None) -> int:
    return sum(e["ns"] for e in events if e["stage"] == stage and (context is None or e["context"] == context))


def aggregate_step(events: list[dict[str, Any]], tick_wall_ns: int) -> dict[str, float]:
    """Fold one tick's events into the disjoint stage totals (ms).

    Walls (observe/act/fresh/decide/text) never overlap inside a tick;
    snapshot/settle leaves sit inside the observe/act/fresh walls and
    are subtracted back out there, so each nanosecond lands once and
    `other` is the honest residual.
    """
    snapshot = _sum_ns(events, "snapshot")
    settle = _sum_ns(events, "settle")
    decide = _sum_ns(events, "wall_decide")
    text = _sum_ns(events, "wall_text")
    act = _sum_ns(events, "wall_act") - _sum_ns(events, "snapshot", "act")
    observe = (
        _sum_ns(events, "wall_observe") - _sum_ns(events, "snapshot", "observe") - _sum_ns(events, "settle", "observe")
    )
    accounted = snapshot + settle + decide + text + act + observe
    other = tick_wall_ns - accounted
    raw = {"snapshot": snapshot, "settle": settle, "observe": observe, "decide": decide, "act": act, "text": text}
    stages_ms = {name: ns / 1e6 for name, ns in raw.items()}
    stages_ms["other"] = other / 1e6
    stages_ms["step"] = tick_wall_ns / 1e6
    return stages_ms


def stage_shares(step_records: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate share per stage: sum(stage) / sum(step), TinyFish-style."""
    total = sum(r["stages_ms"]["step"] for r in step_records)
    if total <= 0:
        return {name: 0.0 for name in STAGES}
    return {name: sum(r["stages_ms"][name] for r in step_records) / total for name in STAGES}


# --------------------------------------------------------------------------
# Timed proxies around the real browser / decider / text provider
# --------------------------------------------------------------------------


class TimedBrowser:
    """BrowserLike proxy recording observe/fresh/act walls + context."""

    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self._inner = inner
        self._rec = recorder
        build = getattr(inner, "_build", None)
        if callable(build):  # StaticBrowser: _build() is its snapshot.js

            def timed_build(*args: Any, **kwargs: Any) -> Any:
                started = time.perf_counter_ns()
                try:
                    return build(*args, **kwargs)
                finally:
                    recorder.add("snapshot", time.perf_counter_ns() - started)

            inner._build = timed_build  # instance attribute; product code untouched

    def _timed(self, name: str, call: Any, *args: Any, **kwargs: Any) -> Any:
        previous = self._rec.context
        self._rec.context = name
        started = time.perf_counter_ns()
        try:
            return call(*args, **kwargs)
        finally:
            self._rec.context = previous
            self._rec.add(f"wall_{name}", time.perf_counter_ns() - started)

    def observe(self, screenshot: bool = False) -> Any:
        return self._timed("observe", self._inner.observe, screenshot=screenshot)

    def fresh(self, page: Any, action: Any = None) -> Any:
        return self._timed("fresh", self._inner.fresh, page, action)

    def act(self, action: Any, page: Any, text: str | None = None) -> Any:
        return self._timed("act", self._inner.act, action, page, text=text)

    def close(self) -> None:
        self._inner.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TimedDecider:
    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self._inner = inner
        self._rec = recorder

    def choose(self, state: Any, goal: str, history: Any) -> Any:
        started = time.perf_counter_ns()
        try:
            return self._inner.choose(state, goal, history)
        finally:
            self._rec.add("wall_decide", time.perf_counter_ns() - started)


def timed_text_provider(inner: Any, recorder: Recorder) -> Any:
    def provider(context: Any) -> Any:
        started = time.perf_counter_ns()
        try:
            return inner(context)
        finally:
            recorder.add("wall_text", time.perf_counter_ns() - started)

    return provider


class CdpTap:
    """Times every call through account_vault.browser.cdp (one seam)."""

    def __init__(self, recorder: Recorder) -> None:
        self._rec = recorder
        self._module: Any = None
        self._real: Any = None

    def install(self) -> None:
        import account_vault.browser as browser_module

        self._module = browser_module
        self._real = browser_module.cdp
        read_state, marker, real, rec = browser_module.READ_STATE, browser_module.MARKER, self._real, self._rec

        def tapped(method: str, **params: Any) -> Any:
            started = time.perf_counter_ns()
            try:
                return real(method, **params)
            finally:
                stage = classify_cdp(method, params, read_state=read_state, marker=marker)
                rec.add(stage, time.perf_counter_ns() - started, method=method)

        browser_module.cdp = tapped

    def restore(self) -> None:
        if self._module is not None and self._real is not None:
            self._module.cdp = self._real


# --------------------------------------------------------------------------
# Driving runs: fresh Agent per iteration, stage record per tick
# --------------------------------------------------------------------------


def drive_run(make_browser: Any, make_agent_parts: Any, url: str, goal: str, max_ticks: int = 30) -> dict[str, Any]:
    """One full loop run to done/blocked with per-tick stage records."""
    from account_vault.agent import Agent

    recorder = Recorder()
    tap = CdpTap(recorder)
    tap.install()
    try:
        setup_started = time.perf_counter_ns()
        recorder.bucket = "setup"
        browser = TimedBrowser(make_browser(url), recorder)
        decider, provider = make_agent_parts()
        agent = Agent(
            url,
            goal,
            browser=browser,
            decider=TimedDecider(decider, recorder),
            text_provider=timed_text_provider(provider, recorder),
        )
        setup_ms = (time.perf_counter_ns() - setup_started) / 1e6
        steps: list[dict[str, Any]] = []
        try:
            for index in range(max_ticks):
                if agent.state["status"] in {"done", "blocked"}:
                    break
                history_before = len(agent.state["history"])
                bucket = f"step{index}"
                recorder.bucket = bucket
                recorder.context = "tick"
                started = time.perf_counter_ns()
                agent.command("tick")
                wall_ns = time.perf_counter_ns() - started
                events = recorder.bucket_events(bucket)
                history = agent.state["history"]
                executed = len(history) > history_before
                last = history[-1] if executed else {}
                steps.append(
                    {
                        "tick": index,
                        "kind": last.get("kind")
                        or ("terminal" if agent.state["status"] in {"done", "blocked"} else "noact"),
                        "operation": last.get("operation"),
                        "action": last.get("action"),
                        "stages_ms": aggregate_step(events, wall_ns),
                        "snapshot_evals": sum(1 for e in events if e["stage"] == "snapshot"),
                        "cdp_calls": sum(1 for e in events if "method" in e),
                        "elements": len(agent.state["page"].get("actions", [])),
                    }
                )
            final_page = agent.state["page"]
            return {
                "status": agent.state["status"],
                "blocked_reason": agent.state.get("blocked_reason"),
                "setup_ms": setup_ms,
                "decisions": len(agent.state["decisions"]),
                "actions": len(agent.state["history"]),
                "final_title": final_page.get("title"),
                "final_text_has_free_cancellation": "Free cancellation included" in final_page.get("text", ""),
                "steps": steps,
                "total_step_ms": sum(s["stages_ms"]["step"] for s in steps),
            }
        finally:
            agent.close()
    finally:
        tap.restore()


# --------------------------------------------------------------------------
# Configs: the local fixtures + catalog page, offline doubles throughout
# --------------------------------------------------------------------------

HOTEL = ROOT / "examples" / "hotel_search.html"
CATALOG = ROOT / "src" / "account_vault" / "static" / "catalog.html"
FIXTURE = ROOT / "src" / "account_vault" / "static" / "fixture.html"
TRAVEL_GOAL = "Find a Design stay in Lisbon with Free cancellation and open Casa Flora."


def _hotel_parts() -> Any:
    from account_vault.__main__ import DEFAULT_FILLS, verify
    from account_vault.model import HeuristicDecider, MappingTextProvider

    decider = HeuristicDecider(
        fill_labels=tuple(DEFAULT_FILLS),
        select_labels=("promo",),
        check_labels=("free cancellation",),
        click_labels=("Search",),
        done_when=lambda page: verify(page)["passed"],
    )
    return decider, MappingTextProvider(DEFAULT_FILLS)


def _catalog_parts(cdp: bool) -> Any:
    from account_vault.model import HeuristicDecider, MappingTextProvider

    fills = {"destination": "Lisbon", "arrive": "2027-03-01", "gift note": "Quiet room please"}

    def done_when(page: dict[str, Any]) -> bool:
        if "Free cancellation included" in page["text"]:
            return True
        if not cdp:
            return False
        # catalog.html reveals its detail region via data-reveals, a
        # StaticBrowser-only convention; in a real browser the click
        # is inert, so completion evidence is the applied form state
        # itself (still independent of the decider's DONE).
        actions = page["actions"]
        destination = next((a for a in actions if a["label"].startswith("Destination")), {})
        category = next((a for a in actions if "Category" in a["label"] or "category" in a["label"]), {})
        return bool(
            str(destination.get("value", "")).strip() == "Lisbon"
            and str(category.get("current_value", category.get("value", ""))).strip() == "Design"
        )

    decider = HeuristicDecider(
        fill_labels=("destination", "arrive", "gift note"),
        select_labels=("design",),
        check_labels=("free cancellation",),
        click_labels=("Casa Flora", "Find stays"),
        done_when=done_when,
    )
    return decider, MappingTextProvider(fills)


def _fixture_parts() -> Any:
    from account_vault.model import HeuristicDecider, MappingTextProvider

    decider = HeuristicDecider(
        fill_labels=("destination",),
        select_labels=("design",),
        check_labels=("free cancellation",),
        click_labels=("Casa Flora", "Find stays"),
        done_when=lambda page: "Free cancellation included" in page["text"],
    )
    return decider, MappingTextProvider({"destination": "Lisbon"})


# --- settle-path scenarios (scroll / wait / combobox) -----------------------
# The travel configs above never scroll, wait, or fill a combobox, so
# the three settle variants built for those paths (2-rAF cap 50 ms,
# combobox cap 200 ms, wheel-commit poll up to SCROLL_SETTLE_TIMEOUT)
# went unmeasured in the first results. These inline pages + rule
# deciders exercise exactly one variant each, CDP-only: the static
# backend has no timers, its scroll act is a stub, and its settle is
# zero by construction.

SCROLL_HTML = """<!doctype html><title>Deep page</title>
<style>body{margin:0;font:18px sans-serif}.spacer{height:900px}
#deep{width:200px;height:44px}#status{margin:20px}</style>
<p>Top of a tall page</p><div class="spacer"></div><div class="spacer"></div>
<div class="spacer"></div>
<button id="deep" onclick="window.__clicks.push('deep');
document.getElementById('status').textContent='Deep done'">Deep target</button>
<p id="status">pending</p><script>window.__clicks=[]</script>"""

WAIT_HTML = """<!doctype html><title>Async reveal</title>
<p id="status">loading</p>
<button id="late" style="display:none"
 onclick="document.getElementById('status').textContent='Ready done'">Loaded action</button>
<script>setTimeout(() => {
  document.getElementById('late').style.display = 'block';
  document.getElementById('status').textContent = 'loaded';
}, 700);</script>"""


def combobox_html(delay_ms: int) -> str:
    return f"""<!doctype html><title>Combobox settle</title>
<label>City <input id="city" role="combobox" aria-expanded="false"
 aria-controls="sb" autocomplete="off"></label>
<ul id="sb" role="listbox"></ul><p id="status">pending</p>
<script>
const i = document.getElementById('city'), sb = document.getElementById('sb'),
      st = document.getElementById('status');
i.addEventListener('input', () => {{
  sb.innerHTML = ''; i.setAttribute('aria-expanded', 'false');
  const token = String(Math.random()); i.dataset.token = token;
  setTimeout(() => {{
    if (!i.value || i.dataset.token !== token) return;
    const li = document.createElement('li');
    li.setAttribute('role', 'option'); li.id = 'opt1';
    li.textContent = 'Lisbon, Portugal';
    li.onclick = () => {{ i.value = 'Lisbon, Portugal'; sb.innerHTML = '';
      st.textContent = 'Selected: Lisbon, Portugal'; }};
    sb.appendChild(li); i.setAttribute('aria-expanded', 'true');
  }}, {delay_ms});
}});
</script>"""


def _data_url(html: str) -> str:
    from urllib.parse import quote

    return "data:text/html," + quote(html)


def _scroll_parts() -> Any:
    from account_vault.model import HeuristicDecider, MappingTextProvider, action_space

    class ScrollDecider(HeuristicDecider):
        def choose(self, state: Any, goal: str, history: Any) -> Any:
            _, targets, controls = action_space(state["actions"])
            if "Deep done" in state["text"]:
                return self._decision("DONE", None, targets, controls, state, goal, history)
            for action in targets.get("CLICK", {}).values():
                if action["label"] == "Deep target":
                    return self._decision("CLICK", action, targets, controls, state, goal, history)
            if "SCROLL_DOWN" in controls:
                return self._decision("SCROLL_DOWN", None, targets, controls, state, goal, history)
            return self._decision("BLOCKED", None, targets, controls, state, goal, history)

    return ScrollDecider(), MappingTextProvider({})


def _wait_parts() -> Any:
    from account_vault.model import HeuristicDecider, MappingTextProvider, action_space

    class WaitDecider(HeuristicDecider):
        def choose(self, state: Any, goal: str, history: Any) -> Any:
            _, targets, controls = action_space(state["actions"])
            if "Ready done" in state["text"]:
                return self._decision("DONE", None, targets, controls, state, goal, history)
            for action in targets.get("CLICK", {}).values():
                if action["label"] == "Loaded action":
                    return self._decision("CLICK", action, targets, controls, state, goal, history)
            return self._decision("WAIT", None, targets, controls, state, goal, history)

    return WaitDecider(), MappingTextProvider({})


def _combobox_parts() -> Any:
    from account_vault.model import HeuristicDecider, MappingTextProvider, action_space

    class ComboboxDecider(HeuristicDecider):
        def choose(self, state: Any, goal: str, history: Any) -> Any:
            _, targets, controls = action_space(state["actions"])
            if "Selected: Lisbon, Portugal" in state["text"]:
                return self._decision("DONE", None, targets, controls, state, goal, history)
            for action in targets.get("CLICK", {}).values():
                if "Lisbon, Portugal" in action["label"]:
                    return self._decision("CLICK", action, targets, controls, state, goal, history)
            for action in targets.get("TYPE_TEXT", {}).values():
                if not str(action.get("value", "")).strip():
                    return self._decision("TYPE_TEXT", action, targets, controls, state, goal, history)
            return self._decision("WAIT", None, targets, controls, state, goal, history)

    return ComboboxDecider(), MappingTextProvider({"city": "Lis"})


def _static_browser(url: str) -> Any:
    from account_vault.static_dom import StaticBrowser

    return StaticBrowser(url)


def _cdp_browser(url: str) -> Any:
    from account_vault.browser import CdpBrowser

    return CdpBrowser(url)


def configs(include_cdp: bool) -> list[dict[str, Any]]:
    from account_vault.__main__ import DEFAULT_GOAL

    base = [
        {
            "name": "static-hotel",
            "backend": "static",
            "page": "examples/hotel_search.html",
            "url": str(HOTEL),
            "goal": DEFAULT_GOAL,
            "make_browser": _static_browser,
            "make_parts": _hotel_parts,
        },
        {
            "name": "static-catalog",
            "backend": "static",
            "page": "src/account_vault/static/catalog.html",
            "url": str(CATALOG),
            "goal": TRAVEL_GOAL,
            "make_browser": _static_browser,
            "make_parts": lambda: _catalog_parts(cdp=False),
        },
    ]
    if include_cdp:
        base += [
            {
                "name": "cdp-catalog",
                "backend": "cdp",
                "page": "src/account_vault/static/catalog.html",
                "url": CATALOG.as_uri(),
                "goal": TRAVEL_GOAL,
                "make_browser": _cdp_browser,
                "make_parts": lambda: _catalog_parts(cdp=True),
            },
            {
                "name": "cdp-fixture",
                "backend": "cdp",
                "page": "src/account_vault/static/fixture.html?scenario=travel",
                "url": FIXTURE.as_uri() + "?scenario=travel",
                "goal": TRAVEL_GOAL,
                "make_browser": _cdp_browser,
                "make_parts": _fixture_parts,
            },
            {
                "name": "cdp-scroll",
                "backend": "cdp",
                "page": "inline tall page (3x900px spacers, button below fold)",
                "url": _data_url(SCROLL_HTML),
                "goal": "Click the Deep target button",
                "make_browser": _cdp_browser,
                "make_parts": _scroll_parts,
            },
            {
                "name": "cdp-wait",
                "backend": "cdp",
                "page": "inline async reveal (button appears after 700 ms)",
                "url": _data_url(WAIT_HTML),
                "goal": "Click Loaded action when it appears",
                "make_browser": _cdp_browser,
                "make_parts": _wait_parts,
            },
            {
                "name": "cdp-combobox",
                "backend": "cdp",
                "page": "inline combobox (suggestion renders 150 ms after input)",
                "url": _data_url(combobox_html(150)),
                "goal": "Choose Lisbon, Portugal in the City field",
                "make_browser": _cdp_browser,
                "make_parts": _combobox_parts,
            },
        ]
    return base


def run_config(config: dict[str, Any], runs: int, warmup: int) -> dict[str, Any]:
    for _ in range(warmup):
        drive_run(config["make_browser"], config["make_parts"], config["url"], config["goal"])
    records = [
        drive_run(config["make_browser"], config["make_parts"], config["url"], config["goal"]) for _ in range(runs)
    ]
    steps = [s for r in records for s in r["steps"]]
    # Terminal ticks (the DONE/BLOCKED decision) carry no act; shares
    # about acting/settling are computed over executed-action steps,
    # with all-step shares kept alongside so nothing is cherry-picked.
    acted = [s for s in steps if s["kind"] not in {"terminal", "noact"}]
    by_kind: dict[str, Any] = {}
    for kind in sorted({s["kind"] for s in steps}):
        subset = [s for s in steps if s["kind"] == kind]
        by_kind[kind] = {
            "n": len(subset),
            "step_ms": summarize([s["stages_ms"]["step"] for s in subset]),
            "shares": stage_shares(subset),
        }
    return {
        "name": config["name"],
        "backend": config["backend"],
        "page": config["page"],
        "goal": config["goal"],
        "runs": records,
        "summary": {
            "runs": len(records),
            "statuses": {
                s: sum(1 for r in records if r["status"] == s) for s in sorted({r["status"] for r in records})
            },
            "run_total_step_ms": summarize([r["total_step_ms"] for r in records]),
            "setup_ms": summarize([r["setup_ms"] for r in records]),
            "steps": len(steps),
            "acted_steps": len(acted),
            "stages_ms": {name: summarize([s["stages_ms"][name] for s in acted]) for name in (*STAGES, "step")},
            "shares_acted_steps": stage_shares(acted),
            "shares_all_steps": stage_shares(steps),
            "act_plus_settle_share_acted": stage_shares(acted)["act"] + stage_shares(acted)["settle"],
            "snapshot_evals_per_acted_step": summarize([float(s["snapshot_evals"]) for s in acted]),
            "by_kind": by_kind,
        },
    }


# --------------------------------------------------------------------------
# Micro-benchmarks: isolate one mechanism per distribution
# --------------------------------------------------------------------------


def _repeat_ms(call: Any, n: int) -> list[float]:
    samples = []
    for _ in range(n):
        started = time.perf_counter_ns()
        call()
        samples.append((time.perf_counter_ns() - started) / 1e6)
    return samples


def micro_static(n: int) -> dict[str, Any]:
    from account_vault.browser import fingerprint
    from account_vault.model import action_space, build_choose_request
    from account_vault.static_dom import StaticBrowser

    out: dict[str, Any] = {}
    for label, path in (("hotel", HOTEL), ("catalog", CATALOG)):
        browser = StaticBrowser(path)
        page = browser.observe()
        out[label] = {
            "elements": len(action_space(page["actions"])[0]),
            "actions": len(page["actions"]),
            "text_chars": len(page["text"]),
            "observe_build_ms": summarize(_repeat_ms(lambda: browser.observe(), n)),
            "fresh_ms": summarize(_repeat_ms(lambda: browser.fresh(page), n)),
            "fingerprint_ms": summarize(_repeat_ms(lambda: fingerprint(page), n)),
        }
        bodies = []

        def build() -> None:
            body, *_ = build_choose_request(page, TRAVEL_GOAL, [])
            bodies.append(len(json.dumps(body)))

        out[label]["build_choose_request_ms"] = summarize(_repeat_ms(build, n))
        out[label]["request_json_chars"] = bodies[0] if bodies else 0
    return out


def micro_cdp(n: int) -> dict[str, Any]:
    from account_vault.model import action_space

    recorder = Recorder()
    tap = CdpTap(recorder)
    tap.install()
    try:
        out: dict[str, Any] = {}
        for label, url in (("catalog", CATALOG.as_uri()), ("fixture", FIXTURE.as_uri() + "?scenario=travel")):
            browser = _cdp_browser(url)
            timed = TimedBrowser(browser, recorder)
            try:
                recorder.bucket = f"micro-{label}"
                page = timed.observe(screenshot=False)
                observe_ms = _repeat_ms(lambda: timed.observe(screenshot=False), n)
                fresh_ms = _repeat_ms(lambda: timed.fresh(page), n)
                events = recorder.bucket_events(f"micro-{label}")
                snapshot_ms = [e["ns"] / 1e6 for e in events if e["stage"] == "snapshot"]
                out[label] = {
                    "elements": len(action_space(page["actions"])[0]),
                    "actions": len(page["actions"]),
                    "text_chars": len(page["text"]),
                    "observe_ms": summarize(observe_ms),
                    "fresh_ms": summarize(fresh_ms),
                    "snapshot_eval_ms": summarize(snapshot_ms),
                }
            finally:
                browser.close()
        return out
    finally:
        tap.restore()


def _bucket_ms(recorder: Recorder, bucket: str, stage: str) -> float:
    return sum(e["ns"] for e in recorder.bucket_events(bucket) if e["stage"] == stage) / 1e6


def micro_cdp_mechanisms(quick: bool = False) -> dict[str, Any]:
    """Isolate each settle variant the travel tasks never reach.

    Drives TimedBrowser directly (no Agent): scroll act+observe pairs
    (wheel-commit poll), a scroll dispatched at the page boundary
    (the poll's full 3 s deadline), WAIT acts (fixed sleep, no
    settle after), and combobox fills whose suggestions render
    before / inside / after the 200 ms settle cap.
    """
    scroll_n, boundary_n, wait_n, combo_n = (6, 2, 5, 5) if quick else (24, 5, 20, 15)
    recorder = Recorder()
    tap = CdpTap(recorder)
    tap.install()
    try:
        out: dict[str, Any] = {}

        browser = _cdp_browser(_data_url(SCROLL_HTML))
        timed = TimedBrowser(browser, recorder)
        try:
            recorder.bucket = "mech-scroll-setup"
            page = timed.observe(screenshot=False)
            act_ms, settle_ms, observe_ms = [], [], []
            direction = "scroll_down"
            for i in range(scroll_n):
                action = next((a for a in page["actions"] if a.get("id") == direction), None)
                if action is None:
                    direction = "scroll_up" if direction == "scroll_down" else "scroll_down"
                    action = next(a for a in page["actions"] if a.get("id") == direction)
                bucket = f"mech-scroll-{i}"
                recorder.bucket = bucket
                timed.act(action, page)
                page = timed.observe(screenshot=False)
                act_ms.append(_bucket_ms(recorder, bucket, "wall_act"))
                settle_ms.append(_bucket_ms(recorder, bucket, "settle"))
                observe_ms.append(_bucket_ms(recorder, bucket, "wall_observe"))
            out["scroll"] = {
                "act_ms": summarize(act_ms),
                "settle_ms": summarize(settle_ms),
                "observe_wall_ms": summarize(observe_ms),
            }
            # Boundary: drive to the bottom, then dispatch wheels that
            # cannot move the viewport; observe pays the commit deadline.
            recorder.bucket = "mech-scroll-tobottom"
            while any(a.get("id") == "scroll_down" for a in page["actions"]):
                down = next(a for a in page["actions"] if a.get("id") == "scroll_down")
                timed.act(down, page)
                page = timed.observe(screenshot=False)
            boundary_observe = []
            stuck = {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}
            for i in range(boundary_n):
                bucket = f"mech-boundary-{i}"
                recorder.bucket = bucket
                timed.act(stuck, page)
                page = timed.observe(screenshot=False)
                boundary_observe.append(_bucket_ms(recorder, bucket, "wall_observe"))
            out["scroll_boundary"] = {"observe_wall_ms": summarize(boundary_observe)}

            wait_act, wait_settle = [], []
            wait = next(a for a in page["actions"] if a.get("id") == "wait")
            for i in range(wait_n):
                bucket = f"mech-wait-{i}"
                recorder.bucket = bucket
                timed.act(wait, page)
                page = timed.observe(screenshot=False)
                wait_act.append(_bucket_ms(recorder, bucket, "wall_act"))
                wait_settle.append(_bucket_ms(recorder, bucket, "settle"))
            out["wait"] = {"act_ms": summarize(wait_act), "settle_after_ms": summarize(wait_settle)}
        finally:
            browser.close()

        combo: dict[str, Any] = {}
        for delay in (0, 120, 300):
            browser = _cdp_browser(_data_url(combobox_html(delay)))
            timed = TimedBrowser(browser, recorder)
            try:
                settles, observes, offered = [], [], 0
                for i in range(combo_n):
                    recorder.bucket = f"mech-combo-{delay}-reset"
                    timed.evaluate(
                        "(()=>{const i=document.getElementById('city');i.value='';i.dataset.token='';"
                        "document.getElementById('sb').innerHTML=''})()"
                    )
                    bucket = f"mech-combo-{delay}-{i}"
                    recorder.bucket = bucket
                    page = timed.observe(screenshot=False)
                    fill = next(a for a in page["actions"] if a["kind"] == "fill")
                    timed.act(fill, page, text="Lis")
                    page = timed.observe(screenshot=False)
                    settles.append(_bucket_ms(recorder, bucket, "settle"))
                    observes.append(_bucket_ms(recorder, bucket, "wall_observe"))
                    offered += int(any("Lisbon, Portugal" in a["label"] for a in page["actions"]))
                combo[f"delay_{delay}ms"] = {
                    "settle_ms": summarize(settles),
                    "observe_wall_ms": summarize(observes),
                    "suggestion_offered": f"{offered}/{combo_n}",
                }
            finally:
                browser.close()
        out["combobox"] = combo
        return out
    finally:
        tap.restore()


# --------------------------------------------------------------------------
# Environment, reporting, CLI
# --------------------------------------------------------------------------


def environment() -> dict[str, Any]:
    def probe(*argv: str) -> str:
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    chrome = probe("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version") or probe(
        "google-chrome", "--version"
    )
    return {
        "date": time.strftime("%Y-%m-%d"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": probe("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
        "python": sys.version.split()[0],
        "chrome": chrome,
        "git_sha": probe("git", "rev-parse", "--short", "HEAD"),
        "decider": "HeuristicDecider (offline double)",
        "text_provider": "MappingTextProvider (offline double)",
        "screenshots": "off (loop default)",
    }


def _fmt(summary: dict[str, Any]) -> str:
    return f"p50 {summary['p50']:.2f}  p95 {summary['p95']:.2f}  max {summary['max']:.2f}  (n={summary['n']})"


def print_report(results: dict[str, Any]) -> None:
    env = results["environment"]
    chrome = env["chrome"] or "no Chrome"
    print(f"latency harness — {env['date']}  {env['cpu']}  Python {env['python']}  {chrome}  @{env['git_sha']}")
    for config in results["configs"]:
        s = config["summary"]
        print(f"\n== {config['name']}  ({config['page']})  statuses={s['statuses']}  acted steps={s['acted_steps']}")
        print(f"   run total (steps only): {_fmt(s['run_total_step_ms'])} ms | setup: {_fmt(s['setup_ms'])} ms")
        for name in (*STAGES, "step"):
            share = s["shares_acted_steps"].get(name, 1.0)
            print(f"   {name:<9} {_fmt(s['stages_ms'][name]):<52} share {share:6.1%}")
        print(f"   act+settle share (TinyFish claims 64%): {s['act_plus_settle_share_acted']:.1%}")
        print(f"   snapshot evals/acted step: {_fmt(s['snapshot_evals_per_acted_step'])}")
    print("\n== micro (static)")
    for label, m in results["micro"]["static"].items():
        print(
            f"   {label}: elements={m['elements']} observe {_fmt(m['observe_build_ms'])} ms | "
            f"fresh {_fmt(m['fresh_ms'])} ms | build_request {_fmt(m['build_choose_request_ms'])} ms | "
            f"request {m['request_json_chars']} chars"
        )
    mech = results["micro"].get("cdp_mechanisms", {})
    if mech:
        print("== micro (CDP settle mechanisms)")
        print(
            f"   scroll: act {_fmt(mech['scroll']['act_ms'])} ms | settle {_fmt(mech['scroll']['settle_ms'])} ms | "
            f"observe {_fmt(mech['scroll']['observe_wall_ms'])} ms"
        )
        print(f"   scroll at boundary: observe {_fmt(mech['scroll_boundary']['observe_wall_ms'])} ms")
        print(
            f"   wait: act {_fmt(mech['wait']['act_ms'])} ms | settle after {_fmt(mech['wait']['settle_after_ms'])} ms"
        )
        for label, m in mech["combobox"].items():
            print(f"   combobox {label}: settle {_fmt(m['settle_ms'])} ms | offered {m['suggestion_offered']}")
    for label, m in results["micro"].get("cdp", {}).items():
        print(
            f"   cdp/{label}: elements={m['elements']} observe {_fmt(m['observe_ms'])} ms | "
            f"fresh {_fmt(m['fresh_ms'])} ms | snapshot eval {_fmt(m['snapshot_eval_ms'])} ms"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-stage latency harness for the agent loop.")
    parser.add_argument(
        "--cdp", action="store_true", help="also run CDP configs (needs dedicated Chrome env, see test_live_cdp)"
    )
    parser.add_argument("--runs", type=int, default=30, help="recorded full-loop runs per static config")
    parser.add_argument("--cdp-runs", type=int, default=15, help="recorded full-loop runs per CDP config")
    parser.add_argument("--warmup", type=int, default=2, help="unrecorded warmup runs per config")
    parser.add_argument("--micro-n", type=int, default=100, help="iterations per micro-benchmark")
    parser.add_argument("--quick", action="store_true", help="tiny N smoke run (3 runs, micro n=10)")
    parser.add_argument("--out", default=str(ROOT / "docs" / "latency-measurement.json"))
    args = parser.parse_args(argv)
    if args.quick:
        args.runs, args.cdp_runs, args.micro_n, args.warmup = 3, 2, 10, 1

    results: dict[str, Any] = {"environment": environment(), "configs": [], "micro": {}}
    for config in configs(include_cdp=args.cdp):
        runs = args.cdp_runs if config["backend"] == "cdp" else args.runs
        print(f"running {config['name']} x{runs} (+{args.warmup} warmup) ...", flush=True)
        results["configs"].append(run_config(config, runs, args.warmup))
    results["micro"]["static"] = micro_static(args.micro_n)
    if args.cdp:
        print("running CDP micro-benchmarks ...", flush=True)
        results["micro"]["cdp"] = micro_cdp(args.micro_n)
        print("running CDP settle-mechanism micro-benchmarks ...", flush=True)
        results["micro"]["cdp_mechanisms"] = micro_cdp_mechanisms(quick=args.quick)

    serializable = json.loads(json.dumps(results, default=str))
    Path(args.out).write_text(json.dumps(serializable, indent=1), encoding="utf-8")
    print_report(serializable)
    print(f"\nwrote {args.out}")
    undone = [c["name"] for c in serializable["configs"] if set(c["summary"]["statuses"]) != {"done"}]
    if undone:
        print(f"WARNING: configs not uniformly done: {undone}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
