"""Inspector server (ported demo.py): loopback API over the real loop.

The handler is driven in-process (no socket bind): sandboxed runners
forbid listening sockets, which previously made every test here skip.
Only the TCP transport itself (stdlib ThreadingHTTPServer, wired in
make_server) is bypassed; routing, Host/token/Origin checks, locking,
command dispatch and the full offline agent loop all run for real.
Live mode shares every path except Agent construction (mocked, as
upstream); the CDP construction is exercised by tests/test_live_cdp.py.
"""

import io
import json
from email.message import Message

import pytest

from account_vault import demo


def request(method, path, body=None, token=None, host=None):
    """Invoke demo.Handler directly, capturing status/headers/body."""
    handler = demo.Handler.__new__(demo.Handler)
    payload = json.dumps(body).encode() if body is not None else b""
    headers = Message()
    headers["Host"] = host or f"127.0.0.1:{demo.PORT}"
    if token is not None:
        headers["X-Demo-Token"] = token
    if payload:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
    handler.headers = headers
    handler.path = path
    handler.rfile = io.BytesIO(payload)
    handler.wfile = io.BytesIO()
    captured = {}

    def send_response(code, *args):
        captured["status"] = code

    def send_header(key, value):
        captured.setdefault("headers", {})[key] = value

    handler.send_response = send_response
    handler.send_header = send_header
    handler.end_headers = lambda: None
    getattr(handler, f"do_{method}")()
    return captured["status"], handler.wfile.getvalue().decode("utf-8")


@pytest.fixture(autouse=True)
def _close_agent():
    yield
    demo.close_browser()


def test_index_and_assets_are_served_with_token_substituted():
    status, body = request("GET", "/")
    assert status == 200
    assert "__TOKEN__" not in body and demo.TOKEN in body
    for path in ("/app.js", "/style.css", "/fixture.html", "/catalog.html"):
        status, _ = request("GET", path)
        assert status == 200, path
    assert request("GET", "/nope")[0] == 404
    assert request("GET", "/", host="evil.test")[0] == 403


def test_post_requires_demo_token():
    body = {"scenario": "travel", "mode": "offline", "goal": "x"}
    assert request("POST", "/api/reset", body)[0] == 403
    assert request("POST", "/api/reset", body, token="wrong")[0] == 403


def test_offline_reset_and_ticks_drive_the_loop_to_done():
    goal = "Find a Design stay in Lisbon with Free cancellation and open Casa Flora."
    status, body = request("POST", "/api/reset",
                           {"scenario": "travel", "mode": "offline", "goal": goal},
                           token=demo.TOKEN)
    assert status == 200
    state = json.loads(body)
    assert state["status"] == "ready" and state["mode"] == "offline"
    assert len(state["elements"]) >= 20  # the messy catalog, not an empty table

    for _ in range(20):
        status, body = request("POST", "/api/tick", {}, token=demo.TOKEN)
        assert status == 200
        state = json.loads(body)
        if state["status"] in {"done", "blocked"}:
            break
    assert state["status"] == "done"
    assert "Free cancellation included" in state["page"]["text"]


def test_offline_flights_is_rejected_and_state_endpoint_reports_budget():
    status, body = request("POST", "/api/reset",
                           {"scenario": "flights", "mode": "offline", "goal": "x"},
                           token=demo.TOKEN)
    assert status == 400 and "live mode" in json.loads(body)["error"]
    status, body = request("GET", "/api/state")
    assert status == 200
    state = json.loads(body)
    assert state["max_steps"] == 60 and state["text_model"] == demo.SETTINGS.text_model
