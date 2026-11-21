"""Offline end-to-end demo check: the agent loop as users actually run it.

Two real entry points, no mocks, no network, no API keys:

1. `python -m account_vault` drives examples/hotel_search.html to DONE
   and must pass its own independent verify() (nonzero exit otherwise).
2. The inspector server (`python -m account_vault.demo`) is started as
   a real subprocess on an ephemeral port; this script scrapes the
   demo token from the served page, resets the offline travel
   scenario over HTTP, and ticks the loop to `done`, checking the
   resulting page independently ("Free cancellation included").

Run locally with `uv run python scripts/e2e_offline_demo.py`; CI runs
it in the `e2e (offline demo)` job so a regression in the loop fails
CI even if the unit suite happens to stay green. Exits nonzero and
says which stage failed.
"""

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time

GOAL = "Find a Design stay in Lisbon with Free cancellation and open Casa Flora."


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def request(port, method, path, body=None, token=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body) if body is not None else None
    headers = {"Content-Type": "application/json"} if payload else {}
    if token:
        headers["X-Demo-Token"] = token
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    data = response.read().decode("utf-8")
    connection.close()
    return response.status, data


def run_cli_demo():
    print("e2e 1/2: python -m account_vault (offline hotel search)")
    result = subprocess.run(
        [sys.executable, "-m", "account_vault"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    sys.stdout.write(result.stdout)
    if result.returncode != 0 or "OK: loop reached DONE" not in result.stdout:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"FAILED: CLI demo exited {result.returncode} without the OK line")
    print("e2e 1/2: PASS")


def run_inspector_demo():
    print("e2e 2/2: inspector server subprocess (offline travel scenario)")
    port = free_port()
    env = {**os.environ, "ACCOUNT_VAULT_DEMO_PORT": str(port)}
    server = subprocess.Popen(
        [sys.executable, "-m", "account_vault.demo"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 30
        index = None
        while time.time() < deadline:
            if server.poll() is not None:
                raise SystemExit(f"FAILED: inspector exited early:\n{server.stdout.read()}")
            try:
                status, index = request(port, "GET", "/")
                if status == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("FAILED: inspector did not serve / within 30s")

        match = re.search(r'name="demo-token" content="([^"]+)"', index or "")
        if not match:
            raise SystemExit("FAILED: demo token not found in the served index page")
        token = match.group(1)

        status, body = request(
            port,
            "POST",
            "/api/reset",
            {"scenario": "travel", "mode": "offline", "goal": GOAL},
            token=token,
        )
        if status != 200:
            raise SystemExit(f"FAILED: /api/reset returned {status}: {body}")
        state = json.loads(body)

        for _ in range(25):
            status, body = request(port, "POST", "/api/tick", {}, token=token)
            if status != 200:
                raise SystemExit(f"FAILED: /api/tick returned {status}: {body}")
            state = json.loads(body)
            if state["status"] in {"done", "blocked"}:
                break

        if state["status"] != "done":
            raise SystemExit(f"FAILED: loop ended {state['status']!r}, expected 'done'")
        if "Free cancellation included" not in state["page"]["text"]:
            raise SystemExit("FAILED: final page lacks 'Free cancellation included'")
        print(f"e2e 2/2: PASS (status=done, steps={len(state['history'])})")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    run_cli_demo()
    run_inspector_demo()
    print("e2e: all stages passed")
