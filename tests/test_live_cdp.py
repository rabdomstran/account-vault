"""Live CDP exercise: real Chrome + harness daemon + CdpBrowser + Agent.

Opt-in only (ACCOUNT_VAULT_LIVE=1): launches a dedicated throwaway
Chrome profile on a debug port and an isolated harness daemon, so the
user's real browser/profile are never touched. Run standalone, outside
the sandbox (needs process launch + loopback sockets):

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
PROFILE = Path("/tmp/av-chrome-live-profile")
FIXTURE = Path(__file__).resolve().parents[1] / "src" / "account_vault" / "static" / "fixture.html"
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def _chrome():
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    pytest.skip("no Chrome/Chromium binary found for live CDP test")


@pytest.fixture(scope="module")
def dedicated_chrome():
    if "browser_harness.helpers" in sys.modules:
        pytest.skip("harness already imported under another daemon name; run this file standalone")
    os.environ["BU_CDP_URL"] = f"http://127.0.0.1:{PORT}"
    os.environ["BU_NAME"] = NAME
    chrome = subprocess.Popen(
        [
            _chrome(),
            "--headless=new",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE}",
            "--no-first-run",
            "--disable-gpu",
            "--window-size=1120,780",
            "about:blank",
        ],
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
