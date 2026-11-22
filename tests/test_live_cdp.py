"""Live CDP exercise: real Chrome + harness daemon + CdpBrowser + Agent.

Opt-in locally (ACCOUNT_VAULT_LIVE=1): launches a dedicated throwaway
headless Chrome profile on a debug port and an isolated harness daemon,
so the user's real browser/profile are never touched. CI runs this file
as the `live (headless Chrome)` gate on ubuntu-latest (Chrome ships in
the runner image; all pages are file:/data: URLs, so no network or API
keys). Locally, run standalone, outside the sandbox (needs process
launch + loopback sockets):

    ACCOUNT_VAULT_LIVE=1 .venv/bin/python -m pytest tests/test_live_cdp.py -q

This is the live half of the JS-fixture perception boundary pinned in
tests/test_static.py::test_js_rendered_fixture_is_an_honest_static_boundary:
the same fixture.html that statically perceives as 3 header links must
perceive as the full JS-rendered control table over CDP, and the loop
must drive it to DONE (decider/text are the offline doubles; what is
exercised here is perceive/act/screenshot over CDP, not model quality).
"""

import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ACCOUNT_VAULT_LIVE") != "1",
    reason="live CDP test: set ACCOUNT_VAULT_LIVE=1 (launches a dedicated Chrome)",
)

PORT = 9333
NAME = "account-vault-live"
FIXTURE = Path(__file__).resolve().parents[1] / "src" / "account_vault" / "static" / "fixture.html"


def _chrome():
    # PATH first (CI images ship google-chrome there), then the install
    # locations that are not on PATH (the macOS app bundle).
    candidates = [
        *[shutil.which(name) for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")],
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    # LIVE=1 is an explicit opt-in (the module skipif covers default
    # runs): a missing browser is a broken environment, and as a skip
    # it would let the CI live gate pass without running anything.
    pytest.fail("no Chrome/Chromium binary found for live CDP test")


@pytest.fixture(scope="module")
def dedicated_chrome(tmp_path_factory):
    if "browser_harness.helpers" in sys.modules:
        pytest.skip("harness already imported under another daemon name; run this file standalone")
    os.environ["BU_CDP_URL"] = f"http://127.0.0.1:{PORT}"
    os.environ["BU_NAME"] = NAME
    # Fresh profile per run: a fixed directory can outlive a killed
    # Chrome and fail the next launch on its stale singleton lock.
    profile = tmp_path_factory.mktemp("av-chrome-live-profile")
    flags = [
        "--headless=new",
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--disable-gpu",
        "--window-size=1120,780",
    ]
    if sys.platform.startswith("linux"):
        # CI runners restrict unprivileged user namespaces (Ubuntu
        # 24.04 AppArmor), which Chrome's sandbox needs, and size
        # /dev/shm for containers. This is a throwaway profile serving
        # loopback-only test pages, so the sandbox buys nothing here.
        # Product code never launches Chrome; it only attaches to a
        # caller-launched dedicated instance.
        flags += ["--no-sandbox", "--disable-dev-shm-usage"]
    chrome = subprocess.Popen(
        [_chrome(), *flags, "about:blank"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                version = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2).read())
                if version.get("webSocketDebuggerUrl"):
                    break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.fail("dedicated Chrome did not expose a DevTools endpoint in 30s")
        yield version
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()
        # Stop only our named harness daemon, via its recorded pidfile.
        try:
            from browser_harness import _ipc as ipc

            pid = int(ipc.pid_path(NAME).read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def test_live_perception_sees_the_js_rendered_fixture(dedicated_chrome):
    from account_vault import StaticBrowser
    from account_vault.browser import CdpBrowser
    from account_vault.model import action_space

    url = FIXTURE.as_uri() + "?scenario=travel"

    # Static side of the boundary (same file, no JS execution): 3 links.
    static_elements, _, _ = action_space(StaticBrowser(FIXTURE).observe()["actions"])
    assert [e["label"] for e in static_elements] == ["forma.", "Find a stay", "Reading room"]

    # Live side: the script-rendered control table appears in full.
    browser = CdpBrowser(url)
    try:
        page = browser.observe(screenshot=True)
    finally:
        browser.close()
    live_elements, _, _ = action_space(page["actions"])
    live_labels = [e["label"] for e in live_elements]
    assert len(live_elements) >= 10
    for expected in ("Destination", "Find stays", "Stay category", "Free cancellation", "View Casa Flora"):
        assert expected in live_labels
    shot = page["screenshot"]
    assert shot.startswith("/9j/") and len(base64.b64decode(shot)) > 1000  # real JPEG pixels


def test_live_loop_drives_fixture_to_done(dedicated_chrome):
    from account_vault import Agent
    from account_vault.browser import CdpBrowser
    from account_vault.model import HeuristicDecider, MappingTextProvider

    url = FIXTURE.as_uri() + "?scenario=travel"
    decider = HeuristicDecider(
        fill_labels=("destination",),
        select_labels=("design",),
        check_labels=("free cancellation",),
        click_labels=("Casa Flora", "Find stays"),
        done_when=lambda page: "Free cancellation included" in page["text"],
    )
    browser = CdpBrowser(url)
    with Agent(
        url,
        "Find a Design stay in Lisbon with Free cancellation and open Casa Flora.",
        browser=browser,
        decider=decider,
        text_provider=MappingTextProvider({"destination": "Lisbon"}),
    ) as agent:
        list(agent.run())
        assert agent.state["status"] == "done"
        page = agent.browser.observe(screenshot=False)
        # Independent outcome evidence, not the decider's DONE.
        assert page["title"] == "Casa Flora · Forma"
        assert "Free cancellation included" in page["text"]


# --- combobox settle: observe waits for suggestions inside its cap ---------
# The post-fill settle in browser.py waits up to 200 ms for combobox
# options (50 ms otherwise) so the decider never chooses from an
# incomplete popup. Pin both sides of the cap with real timers.


def _combobox_url(delay_ms):
    from urllib.parse import quote

    html = f"""<!doctype html><title>Combobox settle checks</title>
<label>City <input id="city" role="combobox" aria-expanded="false"
 aria-controls="sb" autocomplete="off"></label>
<ul id="sb" role="listbox"></ul>
<script>
const i = document.getElementById('city'), sb = document.getElementById('sb');
i.addEventListener('input', () => {{
  sb.innerHTML = '';
  setTimeout(() => {{
    if (!i.value) return;
    const li = document.createElement('li');
    li.setAttribute('role', 'option'); li.textContent = 'Lisbon, Portugal';
    sb.appendChild(li); i.setAttribute('aria-expanded', 'true');
  }}, {delay_ms});
}});
</script>"""
    return "data:text/html," + quote(html)


def test_live_combobox_settle_waits_inside_cap_and_gives_up_past_it(dedicated_chrome):
    from account_vault.browser import CdpBrowser

    for delay, offered in ((120, True), (400, False)):
        browser = CdpBrowser(_combobox_url(delay))
        try:
            page = browser.observe(screenshot=False)
            fill = next(a for a in page["actions"] if a["kind"] == "fill")
            browser.act(fill, page, text="Lis")
            started = time.perf_counter()
            page = browser.observe(screenshot=False)
            waited_ms = (time.perf_counter() - started) * 1000
            labels = [a["label"] for a in page["actions"]]
            assert ("Lisbon, Portugal" in labels) is offered, (delay, labels, waited_ms)
            if offered:
                assert waited_ms > 100  # waited for the 120 ms render, past the 50 ms cap
            else:
                assert waited_ms < 350  # gave up at the 200 ms cap, not the 400 ms render
        finally:
            browser.close()


# --- upstream PR #245: a covered control leaves the offer until uncovered --
# Real geometry only exists in a real browser: a fixed overlay covers one
# button's centre, a second button stays hittable. Decision-layer drops
# cannot help here -- a covered act is refused before input and records no
# history for them to learn from -- so without snapshot-level hiding the
# agent re-picks the covered button until the stale-streak stop blocks it.

COVERED_HTML = """<!doctype html><title>Covered checks</title>
<style>body{margin:0;font:18px sans-serif}
#covered{position:absolute;left:40px;top:40px;width:160px;height:40px}
#veil{position:fixed;left:0;top:0;width:260px;height:120px;background:#333;z-index:10}
#free{position:absolute;left:40px;top:220px;width:160px;height:40px}
#status{position:absolute;left:40px;top:300px}</style>
<button id="covered" onclick="window.__clicks.push('covered')">Covered action</button>
<div id="veil">overlay</div>
<button id="free" onclick="window.__clicks.push('free');
document.getElementById('status').textContent='Free done'">Free action</button>
<p id="status">pending</p><script>window.__clicks=[]</script>"""


def _covered_url():
    from urllib.parse import quote

    return "data:text/html," + quote(COVERED_HTML)


def test_live_covered_control_leaves_offer_until_uncovered(dedicated_chrome):
    from account_vault.browser import CdpBrowser, CoveredTarget

    browser = CdpBrowser(_covered_url())
    try:
        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        assert "Covered action" in labels and "Free action" in labels
        covered = next(a for a in page["actions"] if a["label"] == "Covered action")

        with pytest.raises(CoveredTarget):
            browser.act(covered, page)
        assert browser.evaluate("window.__clicks") == []  # refused before input
        # The cached offer is known wrong even though the marker (which
        # deliberately ignores occlusion) still matches: manual predict
        # must re-observe instead of reoffering from this page (#245 P1).
        assert browser.fresh(page) is False

        for _ in range(2):  # stable across re-observes, not a one-off
            hidden = browser.observe(screenshot=False)
            assert not any(a.get("node") == covered["node"] for a in hidden["actions"])
            assert any(a["label"] == "Free action" for a in hidden["actions"])
            assert hidden["marker"] == page["marker"]  # occlusion stays out of the marker

        browser.evaluate("document.getElementById('veil').remove()")
        restored = browser.observe(screenshot=False)
        again = next(a for a in restored["actions"] if a["label"] == "Covered action")
        browser.act(again, restored)
        assert browser.evaluate("window.__clicks") == ["covered"]
    finally:
        browser.close()


# --- upstream issue #227 (fill half): native controls must commit ---------
# The issue's own repro form (text + two dates + select + checkbox +
# submit): before the fix both dates enumerated but Input.insertText
# committed nothing -- value stayed empty, zero input/change events, and
# the form submitted blank dates as success. This is the live regression
# the drift review asks for; an offline test passed even while broken.

NATIVE_HTML = """<!doctype html><title>Booking</title>
<form id="f">
 <label>Destino <input type="text" id="dest" name="dest"></label>
 <label>Check-in <input type="date" id="ci" name="ci"></label>
 <label>Check-out <input type="date" id="co" name="co" value="2027-01-15"></label>
 <label>Pickup time <input type="time" id="pt" name="pt"></label>
 <label>Shade <input type="color" id="sh" name="sh"></label>
 <label>Max price <input type="range" id="mp" name="mp" min="0" max="100" value="10"></label>
 <label>Guests <select id="g" name="g"><option value="1">1</option><option value="2">2</option></select></label>
 <label><input type="checkbox" id="fc" name="fc"> Free cancellation</label>
 <button type="submit">Search</button>
</form>
<script>window.__events=[];for (const id of ['ci','co','pt','sh','mp','dest'])
 for (const t of ['input','change'])
  document.getElementById(id).addEventListener(t,e=>window.__events.push(id+':'+t+':'+e.target.value));
 document.getElementById('f').addEventListener('submit',e=>{e.preventDefault();
  window.__submitted=new URLSearchParams(new FormData(e.target)).toString();});</script>"""


def test_live_native_fills_commit_and_rejects_loudly(dedicated_chrome):
    from urllib.parse import quote

    from account_vault.browser import CdpBrowser

    browser = CdpBrowser("data:text/html," + quote(NATIVE_HTML))
    try:

        def fill(label, text):
            page = browser.observe(screenshot=False)
            action = next(a for a in page["actions"] if a["label"] == label and a["kind"] == "fill")
            browser.act(action, page, text=text)

        def value(element_id):
            return browser.evaluate(f"document.getElementById('{element_id}').value")

        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        assert "Check-in" in labels and "Check-out" in labels  # enumeration (#215/#227)

        # The reported failure: a valid date now commits, with the events
        # a real edit fires, in order.
        fill("Check-in", "2027-03-01")
        assert value("ci") == "2027-03-01"
        assert browser.evaluate("window.__events") == ["ci:input:2027-03-01", "ci:change:2027-03-01"]

        # An invalid value fails loudly, leaves the preset untouched and
        # fires nothing -- it never reaches the form as an empty date.
        with pytest.raises(RuntimeError, match="Native input fill rejected: not a valid date value"):
            fill("Check-out", "March 5, 2027")
        assert value("co") == "2027-01-15"
        assert browser.evaluate("window.__events.filter(e=>e.startsWith('co:'))") == []

        fill("Check-out", "2027-03-05")
        fill("Pickup time", "09:30")
        fill("Shade", "#FF0000")  # the browser canonicalises case
        fill("Max price", "42")
        assert (value("co"), value("pt"), value("sh"), value("mp")) == ("2027-03-05", "09:30", "#ff0000", "42")

        # Color/range sanitise invalid input to a default rather than
        # empty, so the executor shape-checks them up front.
        with pytest.raises(RuntimeError, match="not a valid color value"):
            fill("Shade", "red")
        with pytest.raises(RuntimeError, match="not a valid range value"):
            fill("Max price", "loud")
        assert (value("sh"), value("mp")) == ("#ff0000", "42")

        # The plain-text typing path is untouched by the native branch.
        fill("Destino", "Lisbon")
        assert value("dest") == "Lisbon"

        page = browser.observe(screenshot=False)
        search = next(a for a in page["actions"] if a["label"] == "Search" and a["kind"] == "click")
        browser.act(search, page)
        submitted = browser.evaluate("window.__submitted")
        assert "ci=2027-03-01" in submitted and "co=2027-03-05" in submitted
        assert "pt=09%3A30" in submitted and "dest=Lisbon" in submitted
    finally:
        browser.close()


# --- upstream PR #246: a textarea's default text is not its name ----------
# snapshot.js name() fell back to child text for everything but INPUT, so
# an unlabeled prefilled textarea was labelled by its initial content
# ("Default draft text" below) and kept that stale label after editing,
# while placeholder/title never applied. Same shape as upstream's
# check_guards regression: name by placeholder, before and after an edit.


def test_live_textarea_is_named_by_placeholder_not_its_default_text(dedicated_chrome):
    from urllib.parse import quote

    from account_vault.browser import CdpBrowser

    html = (
        '<!doctype html><title>Compose</title><form><textarea id="message" '
        'placeholder="Message">Default draft text</textarea></form>'
    )
    browser = CdpBrowser("data:text/html," + quote(html))
    try:
        page = browser.observe(screenshot=False)
        labels = {a["label"] for a in page["actions"] if a.get("value") == "Default draft text"}
        assert labels == {"Message", "Open Message"}

        browser.evaluate("document.querySelector('#message').value='Edited by the user'")
        page = browser.observe(screenshot=False)
        labels = {a["label"] for a in page["actions"] if a.get("value") == "Edited by the user"}
        assert labels == {"Message", "Open Message"}
    finally:
        browser.close()


def test_live_agent_recovers_from_a_covered_top_choice(dedicated_chrome):
    # The run-level case the decision layer misses: the decider's top
    # choice is covered, a hittable runner-up completes the goal. Without
    # snapshot hiding this ends blocked after three refused decisions.
    from account_vault import Agent
    from account_vault.browser import CdpBrowser
    from account_vault.model import MappingTextProvider

    def stub(choice, node, operation):
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
            "model": "covered-stub",
            "usage": {},
            "latency_ms": 0,
            "request": {},
        }

    class PreferCovered:
        def __init__(self):
            self.calls = 0

        def validate(self):
            return None

        def choose(self, state, goal, history):
            self.calls += 1
            if "Free done" in state["text"]:
                return stub("DONE", None, "DONE")
            for wanted in ("Covered action", "Free action"):
                for a in state["actions"]:
                    if a["kind"] == "click" and a["label"] == wanted:
                        return stub(a["id"], a.get("node"), "CLICK")
            return stub("BLOCKED", None, "BLOCKED")

    decider = PreferCovered()
    browser = CdpBrowser(_covered_url())
    with Agent(
        _covered_url(), "Do the free action", browser=browser, decider=decider, text_provider=MappingTextProvider({})
    ) as agent:
        list(agent.run())
        assert agent.state["status"] == "done"
        assert [h["action"] for h in agent.state["history"]] == ["Free action"]
        assert decider.calls == 3  # covered refused once, runner-up, DONE
        assert browser.evaluate("window.__clicks") == ["free"]


# --- upstream PR #212: below-fold targets need scroll state ---------------
# Real geometry again: the only button sits several viewports down, so it
# is not even enumerated until scrolled into view. The decider double
# routes every decision through the real build_choose_request, which is
# where BLOCKED suppression and scroll state live, then takes the scroll
# the request offers. On the pre-fix request the run could (and upstream's
# model did) give up as BLOCKED after the first scroll.

TALL_HTML = """<!doctype html><title>Deep page</title>
<style>body{margin:0;font:18px sans-serif}.spacer{height:900px}
#deep{width:200px;height:44px}#status{margin:20px}</style>
<p>Top of a tall page</p><div class="spacer"></div><div class="spacer"></div>
<div class="spacer"></div>
<button id="deep" onclick="window.__clicks.push('deep');
document.getElementById('status').textContent='Deep done'">Deep target</button>
<p id="status">pending</p><script>window.__clicks=[]</script>"""


def test_live_boundary_scroll_exits_without_paying_the_deadline(dedicated_chrome):
    # Provable-boundary early exit (docs/latency-results.md): at the
    # bottom no commit can follow, so observe must return in well
    # under SCROLL_SETTLE_TIMEOUT (3s) instead of polling it out.
    from urllib.parse import quote

    from account_vault.browser import CdpBrowser

    browser = CdpBrowser("data:text/html," + quote(TALL_HTML))
    try:
        page = browser.observe(screenshot=False)
        while any(a.get("id") == "scroll_down" for a in page["actions"]):
            down = next(a for a in page["actions"] if a.get("id") == "scroll_down")
            browser.act(down, page)
            page = browser.observe(screenshot=False)
        stuck = {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560}
        browser.act(stuck, page)
        started = time.perf_counter()
        page = browser.observe(screenshot=False)
        waited_ms = (time.perf_counter() - started) * 1000
        assert waited_ms < 1500, f"boundary observe took {waited_ms:.0f} ms"
        assert not any(a.get("id") == "scroll_down" for a in page["actions"])
    finally:
        browser.close()


def test_live_agent_scrolls_past_first_viewports_to_deep_target(dedicated_chrome):
    from urllib.parse import quote

    from account_vault import Agent
    from account_vault.browser import CdpBrowser
    from account_vault.model import MappingTextProvider, build_choose_request

    def stub(choice, node, operation):
        return {
            "choice": choice,
            "node": node,
            "operation": operation,
            "target": None,
            "confidence": 1.0,
            "probabilities": {choice: 1.0},
            "operation_probabilities": {operation: 1.0},
            "target_probabilities": {},
            "target_confidence": None,
            "raw_answers": {},
            "model": "scroll-stub",
            "usage": {},
            "latency_ms": 0,
            "request": {},
        }

    class ScrollUntilVisible:
        def __init__(self):
            self.offered = []  # operations offered per decision, in order
            self.scroll_seen = []  # page scroll payload per decision

        def validate(self):
            return None

        def choose(self, state, goal, history):
            body, operations, _targets, _controls = build_choose_request(state, goal, history)
            self.offered.append(set(operations))
            self.scroll_seen.append(dict(body["state"]["page"]["scroll"]))
            if "Deep done" in state["text"]:
                return stub("DONE", None, "DONE")
            for a in state["actions"]:
                if a["kind"] == "click" and a["label"] == "Deep target":
                    return stub(a["id"], a.get("node"), "CLICK")
            if "SCROLL_DOWN" in operations:
                return stub("scroll_down", None, "SCROLL_DOWN")
            return stub("BLOCKED", None, "BLOCKED")

    decider = ScrollUntilVisible()
    url = "data:text/html," + quote(TALL_HTML)
    browser = CdpBrowser(url)
    with Agent(
        url, "Click the Deep target button", browser=browser, decider=decider, text_provider=MappingTextProvider({})
    ) as agent:
        list(agent.run())
        # Carry the run's own evidence in the message: which guard (or
        # decider BLOCKED) ended it, what was offered, where the
        # viewport actually was. A bare status assert gives a CI
        # reader nothing to distinguish those apart.
        diag = (
            f"blocked_reason={agent.state.get('blocked_reason')!r} offered={decider.offered} "
            f"scroll_seen={decider.scroll_seen} history={agent.state['history']}"
        )
        if agent.state["status"] != "done":
            # Failure-path probe: did the last wheel land late (observe
            # read mid-animation) or never (input swallowed)? Poll the
            # live scroll position and take one fresh observation.
            poll = []
            for _ in range(10):
                poll.append(browser.evaluate("window.scrollY"))
                time.sleep(0.1)
            fresh_page = browser.observe(screenshot=False)
            diag += (
                f" poll_y={poll} viewport={browser.evaluate('[innerWidth, innerHeight]')} "
                f"fresh_y={fresh_page['scroll']['y']} fresh_ids={[a['id'] for a in fresh_page['actions']]}"
            )
        assert agent.state["status"] == "done", diag
        assert browser.evaluate("window.__clicks") == ["deep"]
        scrolls = [h for h in agent.state["history"] if h["kind"] == "scroll"]
        assert len(scrolls) >= 3  # several viewports down, not one
        # Recorded positions advance monotonically (real viewport movement).
        assert all(h["scroll_after"]["y"] > h["scroll_before"]["y"] for h in scrolls)
        assert all(b["scroll_before"]["y"] >= a["scroll_after"]["y"] for a, b in zip(scrolls, scrolls[1:]))
    # First decision (no history): BLOCKED was available. Mid-page
    # decisions right after a scroll that advanced, with scroll_down
    # still offered: BLOCKED was suppressed (the landing that reveals
    # the target may sit at the page boundary, where it returns --
    # that case is pinned offline in tests/test_regressions.py).
    assert "BLOCKED" in decider.offered[0]
    assert all("BLOCKED" not in ops for ops in decider.offered[1:3])
    assert decider.scroll_seen[1]["y"] > 0 and decider.scroll_seen[1]["can_scroll_down"] is True
    assert decider.scroll_seen[1]["can_scroll_up"] is True


# --- upstream issue #23 residual: clickables without an onclick attribute -
# The first #23 fix covered [onclick] rows only. Three verified shapes
# from the issue thread stayed invisible: addEventListener suggestion
# rows in a positioned layer (issue body), icon-only in-flow controls
# (first comment), and AntD Radio.Button labels wrapping a 0x0 radio
# (second comment, its exact stylesheet repro). Real Chrome is required:
# cursor, position and concealment are computed-style/geometry facts.

CLICKABLE_HTML = """<!doctype html><meta charset="utf-8"><title>Clickable checks</title>
<style>body{margin:30px;font:16px sans-serif}
#cities{position:absolute;left:30px;top:70px;border:1px solid #999;background:#fff}
.cityline{cursor:pointer;padding:8px 30px}
#gear{width:24px;height:24px;cursor:pointer;margin-top:150px}
.group{display:flex;margin-top:30px}
.btn{position:relative;cursor:pointer;border:1px solid #d9d9d9;padding:4px 15px}
.btn input{width:0;height:0;margin:0;opacity:0}
#noise{cursor:pointer;margin-top:20px}</style>
<label>From <input id="from" type="text"></label>
<div id="cities">
  <div class="cityline"><span>Beijing North</span><span>beijingbei</span></div>
  <div class="cityline"><span>Beijing South</span><span>beijingnan</span></div>
</div>
<div id="gear"><svg id="settings" width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/></svg></div>
<div class="group">
  <label class="btn"><span><input type="radio" name="kind" value="expense" checked></span>
  <span>Expense reports (0)</span></label>
  <label class="btn"><span><input type="radio" name="kind" value="payment"></span>
  <span>Payment requests (3)</span></label>
</div>
<p id="noise">Just a pointer paragraph</p>
<button>Search</button>
<script>window.__picked=[];window.__gear=0;
for (const row of document.querySelectorAll('.cityline'))
  row.addEventListener('click',()=>window.__picked.push(row.textContent.trim()));
document.getElementById('gear').addEventListener('click',()=>window.__gear++);</script>"""


def test_live_non_onclick_clickables_are_offered_and_act(dedicated_chrome):
    from urllib.parse import quote

    from account_vault.browser import CdpBrowser

    browser = CdpBrowser("data:text/html," + quote(CLICKABLE_HTML))
    try:
        page = browser.observe(screenshot=False)
        clicks = {a["label"]: a for a in page["actions"] if a["kind"] == "click"}
        # addEventListener rows: offered once (innermost), as buttons.
        assert clicks["Beijing North beijingbei"]["role"] == "button", sorted(clicks)
        assert clicks["Beijing South beijingnan"]["role"] == "button"
        assert "Beijing North" not in clicks  # no per-span duplicates
        # Icon-only in-flow control, named from the svg id.
        assert clicks["settings"]["role"] == "button"
        # AntD labels keep radio semantics + checked state.
        radios = {a["label"]: a for a in page["actions"] if a.get("role") == "radio"}
        assert set(radios) == {"Expense reports (0)", "Payment requests (3)"}, sorted(radios)
        assert radios["Expense reports (0)"]["checked"] == "true"
        assert radios["Payment requests (3)"]["checked"] == "false"
        assert "Just a pointer paragraph" not in clicks  # noise gate holds

        browser.act(clicks["Beijing South beijingnan"], page)
        assert browser.evaluate("window.__picked") == ["Beijing Southbeijingnan"]
        page = browser.observe(screenshot=False)
        gear = next(a for a in page["actions"] if a["label"] == "settings")
        browser.act(gear, page)
        assert browser.evaluate("window.__gear") == 1
        page = browser.observe(screenshot=False)
        payment = next(a for a in page["actions"] if a["label"] == "Payment requests (3)")
        browser.act(payment, page)
        assert browser.evaluate("document.querySelector('input[value=payment]').checked") is True
    finally:
        browser.close()
