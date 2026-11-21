"""Inspector server (ported demo.py): loopback API over the real loop.

The handler is driven in-process (no socket bind): sandboxed runners
forbid listening sockets, which previously made every test here skip.
Only the TCP transport itself (stdlib ThreadingHTTPServer, wired in
make_server) is bypassed; routing, Host/token/Origin checks, locking,
command dispatch and the full offline agent loop all run for real.
Live mode shares every path except Agent construction (mocked, as
upstream); the CDP construction is exercised by tests/test_live_cdp.py.

The transport gets its own end-to-end coverage in
test_inspector_serves_over_a_real_socket (marked `socket`): it binds
an ephemeral loopback port and speaks real HTTP, so it runs in CI and
on a normal dev machine, and skips with an explicit reason only where
the sandbox refuses the bind.
"""

import http.client
import io
import json
import threading
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


@pytest.mark.socket
def test_inspector_serves_over_a_real_socket():
    """Real TCP transport: make_server + ThreadingHTTPServer + HTTP.

    This is the one path the in-process tests above bypass. It binds an
    ephemeral loopback port, so it runs wherever listening sockets are
    allowed (CI, dev machines) and skips — with the reason visible via
    `pytest -rs` — only under sandboxes that forbid the bind.
    """
    original_port = demo.PORT
    try:
        server = demo.make_server(0)
    except PermissionError as error:
        pytest.skip(f"listening sockets not permitted in this environment: {error}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", demo.PORT, timeout=5)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "__TOKEN__" not in body and demo.TOKEN in body

        connection.request("GET", "/api/state")
        response = connection.getresponse()
        state = json.loads(response.read())
        assert response.status == 200
        assert state["max_steps"] == 60

        # Untokened POSTs are refused over the wire too.
        payload = json.dumps({"scenario": "travel", "mode": "offline", "goal": "x"})
        connection.request("POST", "/api/reset", body=payload,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        response.read()
        assert response.status == 403
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        demo.PORT = original_port


def test_offline_flights_is_rejected_and_state_endpoint_reports_budget():
    status, body = request("POST", "/api/reset",
                           {"scenario": "flights", "mode": "offline", "goal": "x"},
                           token=demo.TOKEN)
    assert status == 400 and "live mode" in json.loads(body)["error"]
    status, body = request("GET", "/api/state")
    assert status == 200
    state = json.loads(body)
    assert state["max_steps"] == 60 and state["text_model"] == demo.SETTINGS.text_model
