"""Offline tests for scripts/analyse_jev_issues.py (no network, no gh).

The script is loaded by path because scripts/ is not an installed package.
Fixtures are miniature gh-shaped payloads and doc fragments, so the
clustering / cross-reference / ranking rules stay pinned without the live
tracker.
"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyse_jev_issues.py"
_SPEC = importlib.util.spec_from_file_location("analyse_jev_issues", SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
analyser = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(analyser)

PORT_FRAGMENT = """
### Fixed in this port (each with a `DEVIATION` comment + regression test)

| [#215](https://github.com/browser-use/jev-ultrafast/issues/215) | a | b |
| [#247](https://github.com/browser-use/jev-ultrafast/issues/247) | cycle | guard |

### Known upstream problems deliberately NOT fixed here (out of scope, still true)

- [#248](https://github.com/browser-use/jev-ultrafast/issues/248) accuracy ceiling.

## New in this port (no upstream equivalent)
"""

DRIFT_FRAGMENT = """
## 2. P0 — must pick up
### P0.4 No deadline — [#14](https://github.com/browser-use/jev-ultrafast/issues/14)
## 3. P1 — should pick up
### P1.3 Windows — [#51](https://github.com/browser-use/jev-ultrafast/issues/51)
## 4. P2 — evaluate or ignore
### Open issues — evaluate
- [#224](https://github.com/browser-use/jev-ultrafast/issues/224) region block.
### Open issues — ignore
- [#115](https://github.com/browser-use/jev-ultrafast/issues/115) theme switch.
### Perception boundaries — strategic evaluate
## 8. Already handled (excluded above)
[#215](https://github.com/browser-use/jev-ultrafast/issues/215) input types.
## 9. Recommended pickup order
"""


def _record(number, title, body="", comments=None):
    return analyser.normalise_issue(
        {
            "number": number,
            "title": title,
            "body": body,
            "labels": [],
            "comments": comments or [],
            "createdAt": "2026-09-17T00:00:00Z",
            "updatedAt": "2026-09-17T00:00:00Z",
            "author": {"login": "reporter"},
            "reactionGroups": [],
        }
    )


def test_classify_prefers_title_over_noisy_body():
    # Bodies mention other subsystems; the title decides the cluster.
    record = _record(
        999,
        "Element table misses clickable non-native elements, so suggestion rows are unreachable",
        body="The TYPE_TEXT loop then retypes forever (loop, benchmark).",
    )
    assert analyser.classify_issue(record) == "perceive-enumeration"


def test_classify_each_cluster_and_overrides():
    assert analyser.classify_issue(_record(191, "TYPE_TEXT cache key has no element identity")) == "text-helper"
    assert (
        analyser.classify_issue(_record(132, "Stale action ids can select a different live control")) == "execute-act"
    )
    assert (
        analyser.classify_issue(_record(224, "Model provider returned HTTP 451; no action executed"))
        == "wire-providers"
    )
    assert (
        analyser.classify_issue(_record(248, "Community benchmark: how accurate is the decision model?"))
        == "decide-accuracy"
    )
    assert analyser.classify_issue(_record(46, "Min")) == "community-noise"
    assert analyser.classify_issue(_record(999, "Something entirely new and undescribed")) == "unclustered"


def test_doc_parsers_split_sections():
    port = analyser.parse_port_doc(PORT_FRAGMENT)
    drift = analyser.parse_drift_doc(DRIFT_FRAGMENT)
    assert port == {"fixed": {215, 247}, "not_fixed": {248}}
    assert drift["p0"] == {14} and drift["p1"] == {51}
    assert drift["evaluate"] == {224} and drift["ignore"] == {115} and drift["handled"] == {215}


def test_resolve_status_code_outranks_stale_docs():
    record = _record(14, "app not functioning")
    status, _evidence, stale = analyser.resolve_status(
        record,
        analyser.parse_port_doc(PORT_FRAGMENT),
        analyser.parse_drift_doc(DRIFT_FRAGMENT),
        {14: ["src/account_vault/browser.py:152"]},
    )
    assert status == "handled" and stale is True


def test_resolve_status_open_partial_ignore_and_not_a_fix():
    port = analyser.parse_port_doc(PORT_FRAGMENT)
    drift = analyser.parse_drift_doc(DRIFT_FRAGMENT)
    open_rec = _record(51, "Windows: owned tab is backgrounded")
    assert analyser.resolve_status(open_rec, port, drift, {})[0] == "open"
    partial_rec = _record(125, "Support local resident decision backend and MCP")
    status, _ev, _stale = analyser.resolve_status(partial_rec, port, drift, {125: ["src/x.py:1"]})
    assert status == "partial"  # backend half fixed; MCP residual stays open
    cited_rec = _record(67, "Jev ultra fast Can't complete simple task")
    status, evidence, _stale = analyser.resolve_status(cited_rec, port, drift, {67: ["src/x.py:1"]})
    assert status == "open" and any("not a fix" in note for note in evidence)


def test_hit_score_rewards_people_discussion_and_repros():
    quiet = _record(1, "Library API: first observation can return an empty action space")
    loud = _record(
        67,
        "Jev ultra fast Can't complete simple task",
        body="Steps to reproduce on a live site, with a trace.",
        comments=[
            {"author": {"login": "alice"}, "body": "Same here, we've hit this too"},
            {"author": {"login": "bob"}, "body": "+1"},
        ],
    )
    quiet_score, _parts = analyser.hit_score(quiet, xrefs=0)
    loud_score, parts = analyser.hit_score(loud, xrefs=2)
    assert loud_score > quiet_score
    assert parts["people"] == 3 * 3 and parts["comments"] == 4 and parts["xrefs"] == 6


def test_cross_reference_counts_ignore_self_mentions():
    first = _record(10, "First", body="See #20 and #20 again; self #10.")
    second = _record(20, "Second", body="No references here.")
    counts = analyser.cross_reference_counts([first, second])
    assert counts[20] >= 1 and counts.get(10, 0) == 0


def test_render_report_reconciles_and_ranks():
    records = [
        _record(215, "Native input types (date, time) never reach the element table"),
        _record(51, "Windows: owned tab is backgrounded, modal menus do not paint in time"),
        _record(46, "Min"),
        _record(999, "Something entirely new and undescribed"),
    ]
    analyser.analyse(records, PORT_FRAGMENT, DRIFT_FRAGMENT, {215: ["src/account_vault/snapshot.js:68"]})
    report = analyser.render_report(records, "2026-11-22 00:00 UTC", "unit-test fixture")
    assert "## TL;DR" in report and "## 1. Work queue" in report
    assert "## 6. Outside the tracker" in report and "Appendix A" in report
    assert "#999" in report and "unclustered" in report  # flagged, never dropped
    assert report.count("issues/215)") >= 2  # cluster table + handled table


def test_code_evidence_excludes_this_test_file():
    # This file quotes issue numbers as fixtures; it must never count as
    # evidence that an upstream issue is handled.
    evidence = analyser.scan_code_evidence()
    cited = [cite for cites in evidence.values() for cite in cites]
    assert not any("test_analyse_jev_issues" in cite for cite in cited)
    assert evidence  # the real tree does cite handled issues


def test_normalise_issue_tolerates_missing_fields():
    record = analyser.normalise_issue({"number": 7, "title": "Bare"})
    assert record.number == 7 and record.comment_count == 0 and record.labels == []
    assert record.url.endswith("/issues/7")
