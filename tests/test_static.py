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


def test_textarea_is_not_named_by_its_default_text():
    # Upstream PR #246: a textarea's child text is its default value, not
    # its name. Only INPUT was excluded from child-text naming, so an
    # unlabeled prefilled textarea was labelled by stale (possibly
    # template-sized) content and title/placeholder never applied; the
    # label then disagreed with the value after any edit. aria-label and
    # <label> keep precedence; with nothing else the role name applies.
    markup = """<html><head><title>t</title></head><body><form>
      <textarea name="msg" placeholder="Message">Default draft text</textarea>
      <textarea name="titled" title="Your notes">Prefilled notes</textarea>
      <label>Comment <textarea name="wrapped">Draft comment</textarea></label>
      <label for="bio">Bio</label><textarea id="bio" name="bio">Draft bio</textarea>
      <textarea name="bare">Just a draft</textarea>
      <textarea name="aria" aria-label="Compose">Draft</textarea>
    </form></body></html>"""
    page = StaticBrowser.from_html(markup).observe()
    by_name: dict[str, set[str]] = {}
    for action in page["actions"]:
        if action.get("name"):
            by_name.setdefault(action["name"], set()).add(action["label"])
    assert by_name == {
        "aria": {"Compose", "Open Compose"},
        "bare": {"Open textbox", "textbox"},
        "bio": {"Bio", "Open Bio"},
        "msg": {"Message", "Open Message"},
        "titled": {"Open Your notes", "Your notes"},
        "wrapped": {"Comment", "Open Comment"},
    }
    # The content is still the field's value -- only its naming changed.
    msg = next(a for a in page["actions"] if a.get("name") == "msg" and a["kind"] == "fill")
    assert msg["value"] == "Default draft text"


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


# --- native fill verdicts (upstream issue #227, fill half) -----------------
# The CDP executor commits native controls through the value setter and
# rejects unusable values loudly; the static backend must return the same
# verdicts or offline runs would "fill" dates the live backend rejects.

NATIVE_VALID = {
    "date": "2027-03-01",
    "time": "09:30",
    "datetime-local": "2027-03-01T09:30",
    "month": "2027-03",
    "week": "2027-W09",
    "color": "#1a73e8",
    "range": "42",
}
NATIVE_INVALID = {
    "date": "2027-02-30",  # right shape, impossible day
    "time": "25:00",
    "datetime-local": "March 1, 2027 9:30am",
    "month": "2027-13",
    "week": "2027-W54",
    "color": "red",  # browsers sanitise this to #000000, never to empty
    "range": "loud",
}


def _native_browser(input_type, preset=""):
    markup = f"""<html><head><title>t</title></head><body><form>
      <label>Field <input type="{input_type}" name="f" value="{preset}"></label>
    </form></body></html>"""
    return StaticBrowser.from_html(markup)


def _fill_action(page):
    return next(a for a in page["actions"] if a["kind"] == "fill")


def test_native_fills_commit_canonical_values():
    for input_type, value in NATIVE_VALID.items():
        browser = _native_browser(input_type)
        page = browser.observe()
        browser.act(_fill_action(page), page, text=value)
        assert _fill_action(browser.observe())["value"] == value, input_type


def test_native_fill_rejects_unusable_values_without_mutating():
    for input_type, bad in NATIVE_INVALID.items():
        preset = NATIVE_VALID[input_type]
        browser = _native_browser(input_type, preset=preset)
        page = browser.observe()
        with pytest.raises(RuntimeError, match="Native input fill rejected: not a valid"):
            browser.act(_fill_action(page), page, text=bad)
        # Untouched: the previous value survives and the page never moved.
        assert _fill_action(browser.observe())["value"] == preset, input_type
        assert browser.fresh(page) is True, input_type


def test_native_fill_rejects_freeform_and_missing_values():
    browser = StaticBrowser(FIXTURE)  # its Check-in field is a native date
    page = browser.observe()
    checkin = next(a for a in page["actions"] if a["label"] == "Check-in" and a["kind"] == "fill")
    with pytest.raises(RuntimeError, match="not a valid date value: March 1, 2027"):
        browser.act(checkin, page, text="March 1, 2027")
    with pytest.raises(RuntimeError, match="no value supplied for date input"):
        browser.act(checkin, page, text=None)


def test_text_fill_still_accepts_any_string():
    browser = _native_browser("text")
    page = browser.observe()
    browser.act(_fill_action(page), page, text="March 1, 2027")
    assert _fill_action(browser.observe())["value"] == "March 1, 2027"


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
# No fix exists upstream (issue open at HEAD cf3c406, no linked PR; checked
# with gh 2026-11-21). The issue's comment thread proposes tracking recent
# (url, chosen control) pairs, dropping a pair that recurs twice from the
# next offered set, and giving up cleanly at three; PR #209 (no-op drop +
# episode cap) and #67's fork (dead target after 2) argue the same
# drop-first shape for the no-op cousin. Each step here CHANGES the page,
# so page-unchanged stops can never fire. Before any guard this burned all
# 60 actions bouncing catalog <-> catalog_design; a block-only guard
# stopped it at 5 but never gave the runner-up a turn.


def _stub_decision(choice, node, operation):
    return {
        "choice": choice,
        "node": node,
        "operation": operation,
        "target": "1",
        "confidence": 1.0,
        "probabilities": {choice: 1.0},
        "operation_probabilities": {operation: 1.0},
        "target_probabilities": {},
        "target_confidence": None,
        "raw_answers": {},
        "model": "cycle-stub",
        "usage": {},
        "latency_ms": 0,
        "request": {},
    }


class CycleDecider:
    """Always takes the category link that bounces to the other file.

    Records every offered label set so tests can prove the cycling control
    actually left the offer. When it is gone, waits once (a real decider
    can always choose WAIT/BLOCKED; raising here would only prove the stub
    assumed the offer never shrinks).
    """

    def __init__(self):
        self.offered = []

    def validate(self):
        return None

    def choose(self, state, goal, history):
        self.offered.append([a["label"] for a in state["actions"]])
        want = "All stays" if "catalog_design" in state["url"] else "Design stays"
        for a in state["actions"]:
            if a["kind"] == "click" and a["label"] == want:
                return _stub_decision(a["id"], a.get("node"), "CLICK")
        wait = next(a for a in state["actions"] if a["kind"] == "wait")
        return _stub_decision(wait["id"], None, "WAIT")


def test_page_changing_cycle_blocks_instead_of_burning_action_budget():
    decider = CycleDecider()
    with Agent(str(CATALOG), "Browse categories", decider=decider, text_provider=MappingTextProvider({})) as agent:
        list(agent.run())
        assert agent.state["status"] == "blocked"
        assert "cycle" in agent.state["blocked_reason"].lower()
        # Transition (catalog --Design stays--> design) executes on steps
        # 1 and 3, so the 5th decision must not offer it (drop at two);
        # the decider waits, the suppression is spent, it executes a 3rd
        # time and the run blocks (give up at three).
        assert [h["action"] for h in agent.state["history"]] == [
            "Design stays",
            "All stays",
            "Design stays",
            "All stays",
            "Wait for the page to update",
            "Design stays",
        ]
        assert "Design stays" not in decider.offered[4]
        assert "Design stays" in decider.offered[5]
        assert all(h["page_changed"] for h in agent.state["history"] if h["kind"] != "wait")


class RecoveringCycleDecider:
    """Prefers the cycling category link, but takes the productive
    runner-up (opening Casa Flora) when the link is not offered."""

    def validate(self):
        return None

    def choose(self, state, goal, history):
        if "Free cancellation included" in state["text"]:
            return _stub_decision("DONE", None, "DONE")
        want = "All stays" if "catalog_design" in state["url"] else "Design stays"
        for wanted in (want, "View Casa Flora"):
            for a in state["actions"]:
                if a["kind"] == "click" and a["label"] == wanted:
                    return _stub_decision(a["id"], a.get("node"), "CLICK")
        return _stub_decision("BLOCKED", None, "BLOCKED")


def test_cycle_drop_lets_the_runner_up_finish_the_task():
    # The difference drop-first makes: a block-only guard ends this run
    # blocked on the 3rd Design click; dropping the link for one decision
    # lets the same decider open Casa Flora and finish.
    with Agent(
        str(CATALOG), "Open Casa Flora", decider=RecoveringCycleDecider(), text_provider=MappingTextProvider({})
    ) as agent:
        list(agent.run())
        assert agent.state["status"] == "done"
        assert [h["action"] for h in agent.state["history"]] == [
            "Design stays",
            "All stays",
            "Design stays",
            "All stays",
            "View Casa Flora",
        ]
        assert "Free cancellation included" in agent.state["page"]["text"]
