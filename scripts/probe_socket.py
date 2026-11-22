"""Phase-timing probe for test_inspector_serves_over_a_real_socket."""
import http.client
import json
import socket
import sys
import threading
import time

sys.path.insert(0, "src")
from account_vault import demo

T0 = time.perf_counter()


def t(label):
    print(f"{time.perf_counter() - T0:7.3f}s  {label}", flush=True)


t(f"start py={sys.version.split()[0]} getfqdn...")
start = time.perf_counter()
print("  getfqdn(127.0.0.1) =", socket.getfqdn("127.0.0.1"), f"({time.perf_counter()-start:.3f}s)", flush=True)
start = time.perf_counter()
try:
    print("  gethostbyaddr =", socket.gethostbyaddr("127.0.0.1"), f"({time.perf_counter()-start:.3f}s)", flush=True)
except Exception as e:
    print("  gethostbyaddr err", e, f"({time.perf_counter()-start:.3f}s)", flush=True)

server = demo.make_server(0)
t(f"make_server done port={demo.PORT}")
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

# Raw connect timing x3
for i in range(3):
    start = time.perf_counter()
    s = socket.create_connection(("127.0.0.1", demo.PORT), timeout=10)
    print(f"  raw connect {i}: {time.perf_counter()-start:.3f}s", flush=True)
    s.close()

conn = http.client.HTTPConnection("127.0.0.1", demo.PORT, timeout=15)
for label, method, path, body in [
    ("GET /", "GET", "/", None),
    ("GET /api/state", "GET", "/api/state", None),
    ("POST /api/reset", "POST", "/api/reset", json.dumps({"scenario": "travel", "mode": "offline", "goal": "x"})),
]:
    start = time.perf_counter()
    conn.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
    print(f"  {label} request sent: {time.perf_counter()-start:.3f}s", flush=True)
    resp = conn.getresponse()
    print(f"  {label} getresponse {resp.status}: {time.perf_counter()-start:.3f}s", flush=True)
    resp.read()
    print(f"  {label} read done: {time.perf_counter()-start:.3f}s", flush=True)
    t(f"{label} complete")

start = time.perf_counter()
conn.close()
print(f"  conn.close: {time.perf_counter()-start:.3f}s", flush=True)
start = time.perf_counter()
server.shutdown()
print(f"  server.shutdown: {time.perf_counter()-start:.3f}s", flush=True)
start = time.perf_counter()
server.server_close()
print(f"  server.server_close: {time.perf_counter()-start:.3f}s", flush=True)
start = time.perf_counter()
thread.join(timeout=5)
print(f"  thread.join(alive={thread.is_alive()}): {time.perf_counter()-start:.3f}s", flush=True)
t("done")
