"""Loopback-only inspector for the browsing agent.

Ported from jev_ultrafast/demo.py (MIT, browser-use/jev-ultrafast) — the
piece deliberately left out of the first port. Same design: a loopback
HTTP server exposing the Agent's predict/act/tick commands plus the
bundled inspector UI (static/), Host/Origin/token checks on every
request, one serialized browser step at a time. DEVIATION comments mark
deliberate differences.
"""

import atexit
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .agent import Agent
from .model import HeuristicDecider, MappingTextProvider
from .questions import MAX_ACTIONS
from .settings import load_settings

ROOT = Path(__file__).parent
# DEVIATION: port env renamed for this project; the upstream name still
# works so existing muscle memory/scripts do not silently change port.
PORT = int(os.environ.get("ACCOUNT_VAULT_DEMO_PORT", os.environ.get("TYPESAFE_DEMO_PORT", "8766")))
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
AGENT: Agent | None = None
SETTINGS = load_settings()


def origin() -> str:
    # DEVIATION: computed from the live PORT (upstream freezes it at import)
    # so tests can bind an ephemeral port without rewriting module state.
    return f"http://127.0.0.1:{PORT}"


# DEVIATION: upstream's load_environment() hand-parses .env and keeps
# quotes/inline comments/export prefixes in values (upstream issue #217).
# Settings already load via python-dotenv in settings.load_settings();
# there is deliberately no second parser here.


def response_state() -> dict[str, Any]:
    state: dict[str, Any] = (
        AGENT.snapshot() if AGENT else {"page": None, "status": "idle", "history": [], "decision": None}
    )
    # DEVIATION: model name from Settings, not an env fallback that
    # disagrees with .env.example (upstream issue #36); budget key kept
    # as max_steps for the bundled app.js, value is MAX_ACTIONS (#55).
    return {**state, "text_model": SETTINGS.text_model, "max_steps": MAX_ACTIONS}


def close_browser() -> None:
    global AGENT
    if AGENT:
        AGENT.close()
        AGENT = None


def _offline_agent(scenario: str, goal: str) -> Agent:
    """DEVIATION (new): upstream's inspector needs a live Chrome daemon
    plus paid model APIs for every scenario, so it cannot run in CI or
    before credentials exist. Offline mode drives the bundled static
    catalog (static/catalog.html, the static-rendered twin of the JS
    fixture) with the same loop and the offline decider/text doubles.
    It is a machinery demo, not a model evaluation; live mode below is
    upstream behaviour unchanged."""
    catalog = ROOT / "static" / "catalog.html"
    if scenario == "research":
        decider = HeuristicDecider(
            click_labels=("browser is a choice",),
            done_when=lambda page: "finite vocabulary" in page["text"],
        )
        return Agent(str(catalog), goal, decider=decider, text_provider=MappingTextProvider({}))
    decider = HeuristicDecider(
        fill_labels=("destination", "arrive", "gift note"),
        select_labels=("design",),
        check_labels=("free cancellation",),
        click_labels=("Casa Flora", "Find stays"),
        done_when=lambda page: "Free cancellation included" in page["text"],
    )
    fills = {"destination": "Lisbon", "arrive": "2027-03-01", "gift note": "Quiet room please"}
    return Agent(str(catalog), goal, decider=decider, text_provider=MappingTextProvider(fills))


def command(name: str, body: dict[str, Any]) -> dict[str, Any]:
    global AGENT
    if name == "reset":
        scenario = body.get("scenario", "flights")
        if scenario not in {"travel", "research", "flights"}:
            raise ValueError("Unknown demo scenario")
        mode = body.get("mode", "live")
        if mode not in {"live", "offline"}:
            raise ValueError("Unknown demo mode")
        if mode == "offline" and scenario == "flights":
            raise ValueError("The flights scenario needs live mode (a real browser and model)")
        goal = body.get("goal", "").strip()
        if not goal or len(goal) > 2000:
            raise ValueError("Enter 1–2,000 characters")
        close_browser()
        if mode == "offline":
            agent = _offline_agent(scenario, goal)
        else:
            agent = Agent(
                "https://www.google.com/travel/flights?hl=en"
                if scenario == "flights"
                else f"{origin()}/fixture.html?scenario={scenario}",
                goal,
                screenshots=True,
                record_dir=Path.cwd() / "artifacts" / "frames" if body.get("record") else None,
                settings=SETTINGS,
            )
        AGENT = agent
        agent.state["scenario"] = scenario
        agent.state["mode"] = mode
    else:
        if AGENT is None:
            raise ValueError("Start a demo first")
        AGENT.command(name, body)
    return response_state()


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, content: str | bytes, mime: str = "application/json") -> None:
        data = content if isinstance(content, bytes) else content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            return self.send(403, "Forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                return self.send(200, json.dumps(response_state(), default=str))
        if path == "/demo.mp4":
            video = ROOT.parent / "docs" / "demo.mp4"
            if video.exists():
                return self.send(200, video.read_bytes(), "video/mp4")
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/fixture.html": ("fixture.html", "text/html"),
            "/catalog.html": ("catalog.html", "text/html"),
        }
        if path not in files:
            return self.send(404, "Not found", "text/plain")
        name, mime = files[path]
        # DEVIATION: explicit UTF-8 (upstream issue #5: Path.read_text()
        # without encoding picks the Windows locale and cannot decode
        # its own app.js; upstream PR #208 fixes this the same way).
        content = (ROOT / "static" / name).read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
        self.send(200, content, mime + "; charset=utf-8")

    def do_POST(self) -> None:
        if (
            self.headers.get("Host") != f"127.0.0.1:{PORT}"
            or self.headers.get("X-Demo-Token") != TOKEN
            or self.headers.get("Origin") not in (None, origin())
        ):
            return self.send(403, json.dumps({"error": "Local demo requests only"}))
        if not LOCK.acquire(blocking=False):
            return self.send(409, json.dumps({"error": "A browser step is already running"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 8192:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            result = command(self.path.removeprefix("/api/"), body)
            self.send(200, json.dumps(result, default=str))
        except (ValueError, RuntimeError, TimeoutError) as error:
            self.send(400, json.dumps({"error": str(error)}))
        except Exception:
            self.send(500, json.dumps({"error": "Local demo failed; no automatic retry. Reset to recover."}))
        finally:
            LOCK.release()

    def log_message(self, *_args: Any) -> None:
        pass


def make_server(port: int | None = None) -> ThreadingHTTPServer:
    """Bind the inspector (ephemeral ports allowed, for tests)."""
    global PORT
    server = ThreadingHTTPServer(("127.0.0.1", PORT if port is None else port), Handler)
    PORT = server.server_address[1]
    return server


def main() -> None:
    atexit.register(close_browser)
    server = make_server()
    print(f"account-vault inspector: {origin()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
