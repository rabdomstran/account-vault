"""browser-harness interface contract (the seam browser.py leans on).

browser-harness is pinned exactly (==0.1.13): it is pre-1.0, declares
no __all__ or stability promise, and browser.py depends on three
callables, one through a private-convention parameter
(helpers.cdp's _response_timeout). If a bump renamed that parameter,
the value would silently fall into **params and be forwarded to
Chrome as a CDP parameter while the deadline reverted to the 5s
default -- a failure the fake-transport suite cannot see, because it
monkeypatches the harness modules with fakes shaped like today's.

These tests read the installed package's source and metadata without
executing it (helpers.py binds the daemon NAME at import, which the
offline suite must never trigger; find_spec only locates the files),
and fail a bump loudly until a human re-verifies the seam. The
dependency allowlist also guards the §5 cleanup: a future harness
release must not pull the removed stacks back in transitively.
"""

import ast
import importlib.metadata
import importlib.util
from pathlib import Path

# Runtime deps browser-harness 0.1.13 declares; anything beyond this
# on a bump needs a conscious decision, not a silent lockfile change.
ALLOWED_RUNTIME_DEPS = {"cdp-use", "fetch-use", "pillow", "websockets"}


def _functions(module: str) -> dict[str, ast.FunctionDef]:
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin is not None, module
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _arg_names(fn: ast.FunctionDef) -> list[str]:
    return [a.arg for a in fn.args.posonlyargs + fn.args.args]


def _all_have_defaults(fn: ast.FunctionDef) -> bool:
    return len(fn.args.defaults) == len(fn.args.posonlyargs) + len(fn.args.args)


def test_helpers_cdp_keeps_the_signature_browser_py_calls():
    cdp = _functions("browser_harness.helpers")["cdp"]
    assert _arg_names(cdp) == ["method", "session_id", "_response_timeout"]
    assert cdp.args.kwarg is not None and cdp.args.kwarg.arg == "params"


def test_admin_entry_points_stay_callable_with_no_arguments():
    admin = _functions("browser_harness.admin")
    # browser.py calls both bare: ensure_daemon() and daemon_browser_kind().
    assert _all_have_defaults(admin["ensure_daemon"])
    assert _all_have_defaults(admin["daemon_browser_kind"])


def test_harness_runtime_deps_do_not_grow_or_revive_removed_stacks():
    names = set()
    for requirement in importlib.metadata.requires("browser-harness") or []:
        spec, _, marker = requirement.partition(";")
        if "extra ==" in marker:  # optional extras (e.g. mcp) are not installed
            continue
        name = spec.split("=")[0].split("<")[0].split(">")[0].split("[")[0].strip()
        names.add(name.lower().replace("_", "-"))
    assert names <= ALLOWED_RUNTIME_DEPS, names - ALLOWED_RUNTIME_DEPS
