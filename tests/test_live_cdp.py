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
        [_chrome(), "--headless=new", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={PROFILE}", "--no-first-run", "--disable-gpu",
         "--window-size=1120,780", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                version = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/json/version", timeout=2).read())
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
    with Agent(url, "Find a Design stay in Lisbon with Free cancellation and open Casa Flora.",
               browser=browser, decider=decider,
               text_provider=MappingTextProvider({"destination": "Lisbon"})) as agent:
        list(agent.run())
        assert agent.state["status"] == "done"
        page = agent.browser.observe(screenshot=False)
        # Independent outcome evidence, not the decider's DONE.
        assert page["title"] == "Casa Flora · Forma"
        assert "Free cancellation included" in page["text"]
