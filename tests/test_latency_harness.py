"""Offline tests for scripts/latency_harness.py (no Chrome, no network).

The script is loaded by path because scripts/ is not an installed
package (same pattern as tests/test_analyse_jev_issues.py). What is
pinned here is the measurement arithmetic — percentiles, CDP-call
classification, and the disjoint stage accounting — plus one real
static-backend drive so the proxies cannot silently stop timing.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "latency_harness.py"
_SPEC = importlib.util.spec_from_file_location("latency_harness", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harness)


def test_percentile_endpoints_and_interpolation():
    assert harness.percentile([], 50) == 0.0
    assert harness.percentile([7.0], 95) == 7.0
    assert harness.percentile([0.0, 10.0], 50) == 5.0
    assert harness.percentile([0.0, 10.0], 0) == 0.0
    assert harness.percentile([0.0, 10.0], 100) == 10.0


def test_summarize_shape():
    summary = harness.summarize([3.0, 1.0, 2.0])
    assert summary["n"] == 3
    assert summary["min"] == 1.0 and summary["max"] == 3.0
    assert summary["p50"] == 2.0
    assert harness.summarize([])["n"] == 0


def test_classify_cdp_distinguishes_settle_from_act_resolve():
    from account_vault.browser import MARKER, READ_STATE

    classify = harness.classify_cdp
    assert classify("Runtime.evaluate", {"expression": READ_STATE}, read_state=READ_STATE, marker=MARKER) == "snapshot"
    # A freshness check re-runs the whole snapshot script (MARKER wraps it).
    assert classify("Runtime.evaluate", {"expression": MARKER}, read_state=READ_STATE, marker=MARKER) == "snapshot"
    settle = "(action => new Promise(resolve => { requestAnimationFrame(ready); }))({})"
    assert classify("Runtime.evaluate", {"expression": settle}, read_state=READ_STATE, marker=MARKER) == "settle"
    assert (
        classify("Runtime.evaluate", {"expression": "window.scrollY"}, read_state=READ_STATE, marker=MARKER) == "settle"
    )
    resolve = "(action => { const cache=window.__jevFast; return null; })({})"
    assert classify("Runtime.evaluate", {"expression": resolve}, read_state=READ_STATE, marker=MARKER) == "act_resolve"
    guard = "(() => { const c=window.__jevFast; return c ? [c.pageKey(),c.guard(c.nodes.get(3))] : null; })()"
    assert classify("Runtime.evaluate", {"expression": guard}, read_state=READ_STATE, marker=MARKER) == "fresh_guard"
    assert classify("Input.dispatchMouseEvent", {}, read_state=READ_STATE, marker=MARKER) == "act_input"
    assert classify("Page.captureScreenshot", {}, read_state=READ_STATE, marker=MARKER) == "screenshot"
    assert classify("Page.navigate", {}, read_state=READ_STATE, marker=MARKER) == "setup"


def test_aggregate_step_counts_each_nanosecond_once():
    ms = 1_000_000
    events = [
        {"bucket": "s", "context": "observe", "stage": "snapshot", "ns": 3 * ms},
        {"bucket": "s", "context": "observe", "stage": "settle", "ns": 2 * ms},
        {"bucket": "s", "context": "tick", "stage": "wall_observe", "ns": 6 * ms},
        {"bucket": "s", "context": "fresh", "stage": "snapshot", "ns": 3 * ms},
        {"bucket": "s", "context": "act", "stage": "snapshot", "ns": 3 * ms},
        {"bucket": "s", "context": "tick", "stage": "wall_act", "ns": 5 * ms},
        {"bucket": "s", "context": "tick", "stage": "wall_decide", "ns": 4 * ms},
        {"bucket": "s", "context": "tick", "stage": "wall_text", "ns": 1 * ms},
    ]
    stages = harness.aggregate_step(events, tick_wall_ns=20 * ms)
    assert stages["snapshot"] == 9.0  # observe + fresh + in-act executions
    assert stages["settle"] == 2.0
    assert stages["observe"] == 1.0  # 6 wall - 3 snapshot - 2 settle
    assert stages["act"] == 2.0  # 5 wall - 3 in-act snapshot
    assert stages["decide"] == 4.0 and stages["text"] == 1.0
    assert stages["other"] == 1.0 and stages["step"] == 20.0
    assert sum(stages[s] for s in harness.STAGES) == stages["step"]


def test_stage_shares_sum_to_one():
    records = [
        {
            "stages_ms": {
                "snapshot": 6.0,
                "settle": 2.0,
                "observe": 0.0,
                "decide": 1.0,
                "act": 1.0,
                "text": 0.0,
                "other": 0.0,
                "step": 10.0,
            }
        },
        {
            "stages_ms": {
                "snapshot": 2.0,
                "settle": 6.0,
                "observe": 0.0,
                "decide": 1.0,
                "act": 1.0,
                "text": 0.0,
                "other": 0.0,
                "step": 10.0,
            }
        },
    ]
    shares = harness.stage_shares(records)
    assert abs(sum(shares.values()) - 1.0) < 1e-9
    assert shares["act"] + shares["settle"] == 0.5


def test_drive_run_static_hotel_reaches_done_with_timed_steps():
    config = harness.configs(include_cdp=False)[0]
    assert config["name"] == "static-hotel"
    record = harness.drive_run(config["make_browser"], config["make_parts"], config["url"], config["goal"])
    assert record["status"] == "done"
    acted = [s for s in record["steps"] if s["kind"] not in {"terminal", "noact"}]
    assert len(acted) >= 5
    for step in acted:
        stages = step["stages_ms"]
        assert stages["step"] > 0
        assert stages["settle"] == 0.0  # no browser offline, by construction
        # One build per acted step (the post-act observe); freshness is
        # served from that build's cache (cheap-fresh DEVIATION).
        assert step["snapshot_evals"] == 1
        total = sum(stages[s] for s in harness.STAGES)
        assert abs(total - stages["step"]) < 1e-6
        assert stages["other"] > -0.05  # residual stays sane (timer granularity)


def test_micro_static_reports_request_size():
    micro = harness.micro_static(3)
    for label in ("hotel", "catalog"):
        assert micro[label]["elements"] >= 5
        assert micro[label]["request_json_chars"] > 1000
        assert micro[label]["observe_build_ms"]["n"] == 3
