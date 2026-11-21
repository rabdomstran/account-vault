"""Static-HTML backend: enumeration coverage + end-to-end loop, offline."""

from pathlib import Path

import pytest

from account_vault import Agent, StaticBrowser
from account_vault.__main__ import verify
from account_vault.model import HeuristicDecider, MappingTextProvider

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "hotel_search.html"
FILLS = {"destination": "Zurich", "check-in": "2027-03-01", "notes": "Late arrival"}


def labels(page):
    return [a["label"] for a in page["actions"]]


def test_native_date_input_is_enumerated():
    # Upstream issues #215/#227: date inputs used to be invisible.
    page = StaticBrowser(FIXTURE).observe()
    checkin = next(a for a in page["actions"] if a["label"] == "Check-in")
    assert checkin["kind"] == "fill"
    assert checkin["input_type"] == "date"


def test_bare_contenteditable_is_enumerated():
    # Upstream issue #218: selector only matched contenteditable="true".
    page = StaticBrowser(FIXTURE).observe()
    notes = next(a for a in page["actions"] if a["label"] == "Notes for the host")
    assert notes["kind"] == "fill" and notes["role"] == "textbox"


def test_all_native_value_types_reach_the_table():
    markup = """<html><head><title>t</title></head><body><form>
      <label>When <input type="time" name="t"></label>
      <label>Month <input type="month" name="m"></label>
      <label>Volume <input type="range" name="r"></label>
      <label>Shade <input type="color" name="c"></label>
      <div onclick="pick()">Suggestion row</div>
    </form></body></html>"""
    page = StaticBrowser.from_html(markup).observe()
    kinds = {a["label"]: a["kind"] for a in page["actions"]}
    assert kinds["When"] == "fill"
    assert kinds["Month"] == "fill"
    assert kinds["Volume"] == "fill"
    assert kinds["Shade"] == "fill"
    # Upstream issue #23: bare clickable rows used to be unreachable.
    assert kinds["Suggestion row"] == "click"


def test_duplicate_valued_option_selects_by_index():
    # Upstream issue #216: value-based select picks the FIRST "1" option.
    browser = StaticBrowser(FIXTURE)
    page = browser.observe()
    promo = next(a for a in page["actions"] if a["label"] == "Guests → 1 guest (promo)")
    browser.act(promo, page)
    after = browser.observe()
    guests = next(a for a in after["actions"] if a["label"].startswith("Guests"))
    assert guests["current_value"] == "1 guest (promo)"


def test_password_and_hidden_inputs_stay_out_of_the_table():
    markup = """<html><head><title>t</title></head><body>
      <label>Secret <input type="password"></label>
      <input type="hidden" value="x">
      <label>Visible <input type="text"></label>
    </body></html>"""
    page = StaticBrowser.from_html(markup).observe()
    assert labels(page) == ["Visible", "Open Visible", "Wait for the page to update"]


def test_stale_static_page_is_rejected():
    browser = StaticBrowser(FIXTURE)
    page = browser.observe()
    search = next(a for a in page["actions"] if a["label"] == "Destination")
    browser.act(search, page, text="Bern")
    # The old observation no longer matches; replaying it must fail.
    with pytest.raises(Exception, match="Observe again"):
        browser.act(search, page, text="Lugano")


def test_loop_runs_end_to_end_on_static_file():
    decider = HeuristicDecider(
        fill_labels=tuple(FILLS),
        select_labels=("promo",),
        check_labels=("free cancellation",),
        click_labels=("Search",),
        done_when=lambda page: verify(page, FILLS)["passed"],
    )
    with Agent(
        str(FIXTURE),
        "Search stays near Zurich with free cancellation.",
        decider=decider,
        text_provider=MappingTextProvider(FILLS),
    ) as agent:
        states = list(agent.run())
        final = states[-1]
        assert final["status"] == "done"
        result = verify(final["page"], FILLS)
        assert result["passed"], result
        # Six executed actions + one terminal DONE decision.
        assert [h["kind"] for h in final["history"]] == ["fill", "fill", "fill", "select", "click", "click"]
        assert len(final["decisions"]) == 7
        assert len(final["text_calls"]) == 3


# --- second, messier page: static/catalog.html -----------------------------
# Duplicate labels across groups, near-duplicate stay names, duplicate
# select values, disabled/hidden/aria-hidden traps, cross-file nav.

CATALOG = Path(__file__).resolve().parents[1] / "src" / "account_vault" / "static" / "catalog.html"
CATALOG_FILLS = {"destination": "Lisbon", "arrive": "2027-03-01", "gift note": "Quiet room please"}


def test_catalog_traps_stay_out_of_the_table():
    found = labels(StaticBrowser(CATALOG).observe())
    for trap in ("Claim 50% off", "Secret deal", "Sold out", "Admin PIN"):
        assert trap not in found
    # Disabled option is not offered; enabled duplicates keep distinct rows.
    assert not any("sold out" in label.lower() for label in found)
    assert "Sort by → Price: low to high" in found
    assert "Sort by → Price: low to high (member)" in found


def test_catalog_same_text_buttons_are_distinguished_by_aria_label():
    found = labels(StaticBrowser(CATALOG).observe())
    # Every card button reads "View stay ↗"; only aria-label separates them.
    assert not any(label.startswith("View stay") for label in found)
    assert "View Casa Flora" in found and "View Casa Verde" in found


def test_loop_runs_end_to_end_on_messier_catalog():
    decider = HeuristicDecider(
        fill_labels=tuple(CATALOG_FILLS),
        select_labels=("design",),
        check_labels=("free cancellation",),
        click_labels=("Casa Flora", "Find stays"),
        done_when=lambda page: "Free cancellation included" in page["text"],
    )
    with Agent(
        str(CATALOG),
        "Find a Design stay in Lisbon with Free cancellation and open Casa Flora.",
        decider=decider,
        text_provider=MappingTextProvider(CATALOG_FILLS),
    ) as agent:
        list(agent.run())
        assert agent.state["status"] == "done"
        page = agent.browser.observe()
        destination = next(a for a in page["actions"] if a["label"] == "Destination")
        assert destination["value"] == "Lisbon"
        assert "Casa Flora" in page["text"] and "Free cancellation included" in page["text"]
        # Only Casa Flora's detail opened, not near-duplicate Casa Verde's.
        assert "Non-refundable stay" not in page["text"]


def test_js_rendered_fixture_is_an_honest_static_boundary():
    # static/fixture.html builds its DOM in script; StaticBrowser runs no
    # JavaScript, so only the static header is perceivable. The inspector's
    # live (CDP) mode is the path for that file; catalog.html is its
    # static-rendered twin. Pin the boundary so it cannot silently grow.
    # The live half of this pin (same file perceives in full over CDP and
    # the loop drives it to DONE) is tests/test_live_cdp.py (opt-in).
    from account_vault.model import action_space

    fixture = CATALOG.with_name("fixture.html")
    page = StaticBrowser(fixture).observe()
    assert labels(page) == ["forma.", "Find a stay", "Reading room", "Wait for the page to update"]
    elements, _, _ = action_space(page["actions"])
    assert len(elements) == 3  # header links only; <main> is script-rendered
    assert len(page["text"]) < 100


# --- upstream issue #247: page-changing cycles must stop -------------------
# No fix exists upstream (issue open at HEAD, no linked PR; checked with
# gh 2026-11-21). The issue's comment thread suggests detecting cycles
# over recent (url, control) pairs and giving up cleanly; each step here
# CHANGES the page, so page-unchanged stops can never fire. Before the
# fix this burned all 60 actions bouncing catalog <-> catalog_design.

class CycleDecider:
    def validate(self):
        return None

    def choose(self, state, goal, history):
        want = "All stays" if "catalog_design" in state["url"] else "Design stays"
        for a in state["actions"]:
            if a["kind"] == "click" and a["label"] == want:
                return {"choice": a["id"], "node": a.get("node"), "operation": "CLICK",
                        "target": "1", "confidence": 1.0, "probabilities": {a["id"]: 1.0},
                        "operation_probabilities": {"CLICK": 1.0}, "target_probabilities": {},
                        "target_confidence": None, "raw_answers": {}, "model": "cycle-stub",
                        "usage": {}, "latency_ms": 0, "request": {}}
        raise AssertionError(f"no {want!r} link on {state['url']}")


def test_page_changing_cycle_blocks_instead_of_burning_action_budget():
    with Agent(str(CATALOG), "Browse categories", decider=CycleDecider(),
               text_provider=MappingTextProvider({})) as agent:
        list(agent.run())
        assert agent.state["status"] == "blocked"
        assert "cycle" in agent.state["blocked_reason"].lower()
        # Pair (Design stays -> design page) recurs on steps 1, 3, 5.
        assert len(agent.state["history"]) == 5
        assert all(h["page_changed"] for h in agent.state["history"])
