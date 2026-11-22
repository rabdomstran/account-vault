"""d1-2B MPS smoke: the first real-model decide measurement (ADR-001).

docs/decide-accuracy-ceiling.md section 5.4 prices this as the cheapest
decision-relevant fact in the project ($0, ~1 min of runs after the
load): the d1-2B model card reports 753 ms at a 5K-token state on an
Apple M5 Pro (MPS, warm calls after model.compile(), bf16, median of
20, best of three runs) against ADR-001's keep-gate of < 100 ms p50
decide latency on the Apple M4 dev machine. Every decide number in
this repo before this script came from offline doubles (~0.05 ms) or
arithmetic substitution of published latencies.

Cases (each: warmup calls excluded, then --runs timed calls, MPS
synchronized around every call so enqueue time is never mistaken for
the forward pass):

- card-quickstart   the model card's own example verbatim (4 named
  questions over one short sentence) -- the vendor's home turf.
- card-1-question   the same state, one question -- the card's
  "One question" column (18.6 ms on M5 Pro).
- wire-hotel / wire-catalog   frozen request bodies built by this
  port's build_choose_request on the latency harness's own pages,
  translated by local_decider.translate_request -- what the loop
  actually asks, at its actual token counts.
- wire-5k   the hotel body with its page text padded (repeated
  filler sentences, adjusted against usage.input_tokens) to a
  ~5,000-input-token call -- the card's 753 ms column, reproduced
  at matched input size. Padding changes content, not just length;
  it prices the prefill, which is the quantity the gate cares about.

Answers are sanity-checked (envelope shape, probabilities) and, for
the wire cases, pushed through the adapter's validate_choice path by
construction of the frozen bodies. This script measures latency and
envelope compatibility only -- it says nothing about decision
quality (that is Tier R in the accuracy doc).

    uv run python scripts/d1_smoke.py                 # eager, 20 runs/case
    uv run python scripts/d1_smoke.py --compile       # card method (separate process)
    uv run python scripts/d1_smoke.py --quick         # 5 runs/case

Results land in docs/d1-mps-smoke.json (+ "-compiled" with --compile);
docs/d1-mps-smoke.md reads them. Requires the `local` extra and a
one-time ~3.8 GB model download; the load itself is timed and
reported separately, never folded into decide latency.
"""

import argparse
import copy
import importlib.util
import json
import platform
import resource
import subprocess
import sys
import time
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Reuse the harness's distribution math so smoke and harness p50s are
# computed identically (scripts/ is not an installed package).
_SPEC = importlib.util.spec_from_file_location("latency_harness", ROOT / "scripts" / "latency_harness.py")
assert _SPEC is not None and _SPEC.loader is not None
harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harness)
summarize = harness.summarize

GATE_MS = 100.0
TARGET_5K_TOKENS = 5000
FILLER = (
    "The listing includes a quiet double room with garden view, breakfast on the terrace, "
    "and late checkout on request. "
)

# The model card's Transformers quick start, verbatim (state + questions).
CARD_STATE = "I was charged twice and my parcel never arrived. Please refund one charge."
CARD_QUESTIONS: dict[str, Any] = {
    "refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"},
    "team": {
        "type": "choice",
        "instructions": "Which team should handle this?",
        "criteria": {
            "billing": "Charges, refunds, invoices",
            "shipping": "Delivery and parcels",
            "technical": "App or site faults",
        },
    },
    "topics": {
        "type": "choice",
        "multiple": True,
        "instructions": "Which topics does the message raise?",
        "criteria": {
            "billing": "Payments and charges",
            "delivery": "Shipping and parcels",
            "login": "Signing in to the account",
        },
    },
    "urgency": {"type": "score", "instructions": "How urgent is this ticket?", "criteria": ["low", "medium", "high"]},
}


def _probe(*argv: str) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def environment(device: str | None, compile_model: bool, revision: str | None) -> dict[str, Any]:
    import torch
    import transformers

    return {
        "date": time.strftime("%Y-%m-%d"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": _probe("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
        "memory_gb": round(int(_probe("sysctl", "-n", "hw.memsize") or 0) / 1e9, 1),
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "git_sha": _probe("git", "rev-parse", "--short", "HEAD"),
        "model": "LiquidAI/d1-2B",
        "revision": revision or "main (unpinned)",
        "device": device,
        "mode": "compiled (model.compile())" if compile_model else "eager",
        "dtype": "float32" if device == "cpu" else "bfloat16",
        "gate": f"ADR-001: local decide p50 < {GATE_MS:.0f} ms on Apple M4",
        "card_reference": {
            "hardware": "Apple M5 Pro (mps), warm after model.compile(), bf16, median of 20, best of 3 runs",
            "one_question_ms": 18.6,
            "three_questions_ms": 26.5,
            "state_5k_tokens_ms": 753,
        },
    }


def frozen_wire_bodies() -> dict[str, Any]:
    """Harness-realistic decide bodies, frozen before any timing."""
    from account_vault.model import build_choose_request
    from account_vault.static_dom import StaticBrowser

    bodies = {}
    for label, path, goal in (
        ("wire-hotel", harness.HOTEL, harness.TRAVEL_GOAL),
        ("wire-catalog", harness.CATALOG, harness.TRAVEL_GOAL),
    ):
        browser = StaticBrowser(path)
        page = browser.observe()
        body, *_ = build_choose_request(page, goal, [])
        bodies[label] = body
    return bodies


def pad_to_tokens(model: Any, device: str | None, body: dict[str, Any], target: int) -> tuple[Any, dict[str, Any], int]:
    """Pad the body's page text until usage.input_tokens ~= target.

    Two probe calls calibrate tokens-per-filler-repeat, then one
    confirmation call freezes the body. Probe answers are discarded;
    only their token counts steer the padding.
    """
    from account_vault.local_decider import synchronize, translate_request

    def tokens_at(repeats: int) -> tuple[Any, dict[str, Any], int]:
        padded = copy.deepcopy(body)
        padded["state"]["page"]["text"] = body["state"]["page"]["text"] + " " + FILLER * repeats
        state, questions = translate_request(padded)
        result = model.system_one(state, questions)
        synchronize(device)
        return state, questions, int(result["usage"]["input_tokens"])

    _, _, base = tokens_at(0)
    _, _, probe = tokens_at(100)
    per_repeat = max((probe - base) / 100.0, 0.01)
    repeats = max(0, round((target - base) / per_repeat))
    state, questions, tokens = tokens_at(repeats)
    return state, questions, tokens


def check_answers(answers: dict[str, Any], questions: dict[str, Any]) -> list[str]:
    """Envelope sanity only (choice validity is the adapter's job)."""
    problems = []
    if set(answers) != set(questions):
        problems.append(f"answer names {sorted(answers)} != question names {sorted(questions)}")
    for name, answer in answers.items():
        if answer.get("type") == "choice" and "choices" not in answer:
            probs = answer.get("probabilities", {})
            if answer.get("choice") not in probs:
                problems.append(f"{name}: choice {answer.get('choice')!r} not in probabilities")
            if probs and abs(sum(probs.values()) - 1) >= 0.02:
                problems.append(f"{name}: probabilities sum to {sum(probs.values()):.4f}")
    return problems


def time_case(
    model: Any, device: str | None, state: Any, questions: dict[str, Any], runs: int, warmup: int
) -> dict[str, Any]:
    from account_vault.local_decider import synchronize

    last = None
    for _ in range(warmup):
        last = model.system_one(state, questions)
        synchronize(device)
    samples_ms = []
    for _ in range(runs):
        synchronize(device)
        started = time.perf_counter_ns()
        last = model.system_one(state, questions)
        synchronize(device)
        samples_ms.append((time.perf_counter_ns() - started) / 1e6)
    assert last is not None
    return {
        "latency_ms": summarize(samples_ms),
        "input_tokens": int(last["usage"]["input_tokens"]),
        "questions": len(questions),
        "answer_check": check_answers(last["answers"], questions) or "ok",
        "sample_answers": {
            name: {k: v for k, v in answer.items() if k != "probabilities"} for name, answer in last["answers"].items()
        },
    }


def memory_snapshot(device: str | None) -> dict[str, Any]:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, KiB on Linux.
    rss_mb = rss / 1e6 if sys.platform == "darwin" else rss / 1024
    out: dict[str, Any] = {"process_max_rss_mb": round(rss_mb, 1)}
    if device == "mps":
        import torch

        out["mps_current_allocated_mb"] = round(torch.mps.current_allocated_memory() / 1e6, 1)
        out["mps_driver_allocated_mb"] = round(torch.mps.driver_allocated_memory() / 1e6, 1)
    swap = _probe("sysctl", "-n", "vm.swapusage")
    if swap:
        out["swapusage"] = swap
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="d1-2B local decide-latency smoke (ADR-001 gate).")
    parser.add_argument("--runs", type=int, default=20, help="timed calls per case (card: median of 20)")
    parser.add_argument("--warmup", type=int, default=3, help="untimed warm calls per case")
    parser.add_argument("--quick", action="store_true", help="5 runs, 1 warmup")
    parser.add_argument("--device", default=None, help="pin device (default: cuda, then mps, then cpu)")
    parser.add_argument("--revision", default=None, help="pin HF revision (recorded; use for reproducibility)")
    parser.add_argument("--compile", action="store_true", help="model.compile() before timing (card method)")
    parser.add_argument("--out", default=None, help="JSON out (default: docs/d1-mps-smoke[-compiled].json)")
    args = parser.parse_args(argv)
    if args.quick:
        args.runs, args.warmup = 5, 1
    out_path = (
        Path(args.out)
        if args.out
        else ROOT / "docs" / ("d1-mps-smoke-compiled.json" if args.compile else "d1-mps-smoke.json")
    )

    from account_vault.local_decider import load_d1_model, translate_request

    caught: list[str] = []
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        load_started = time.perf_counter()
        model, device = load_d1_model(device=args.device, revision=args.revision, compile_model=args.compile)
        load_ms = (time.perf_counter() - load_started) * 1000
        print(
            f"loaded LiquidAI/d1-2B on {device} in {load_ms:.0f} ms (mode={'compiled' if args.compile else 'eager'})",
            flush=True,
        )

        cases: dict[str, tuple[Any, dict[str, Any]]] = {
            "card-quickstart": (CARD_STATE, CARD_QUESTIONS),
            "card-1-question": (CARD_STATE, {"team": CARD_QUESTIONS["team"]}),
        }
        bodies = frozen_wire_bodies()
        for label, body in bodies.items():
            cases[label] = translate_request(body)
        print(f"padding wire-hotel to ~{TARGET_5K_TOKENS} input tokens ...", flush=True)
        state5k, questions5k, tokens5k = pad_to_tokens(model, device, bodies["wire-hotel"], TARGET_5K_TOKENS)
        print(f"wire-5k frozen at {tokens5k} input tokens", flush=True)
        cases["wire-5k"] = (state5k, questions5k)

        # Compiled graphs trace on first calls (card: "about a minute");
        # time the first post-compile call separately so the trace cost
        # is visible instead of hiding inside warmup.
        first_call_ms = None
        if args.compile:
            from account_vault.local_decider import synchronize

            started = time.perf_counter_ns()
            model.system_one(CARD_STATE, {"team": CARD_QUESTIONS["team"]})
            synchronize(device)
            first_call_ms = (time.perf_counter_ns() - started) / 1e6
            print(f"first compiled call (trace): {first_call_ms:.0f} ms", flush=True)

        results: dict[str, Any] = {
            "environment": environment(device, args.compile, args.revision),
            "load_ms": round(load_ms, 1),
            "first_compiled_call_ms": None if first_call_ms is None else round(first_call_ms, 1),
            "runs_per_case": args.runs,
            "warmup_per_case": args.warmup,
            "cases": {},
        }
        for label, (state, questions) in cases.items():
            print(f"timing {label} x{args.runs} (+{args.warmup} warmup) ...", flush=True)
            results["cases"][label] = time_case(model, device, state, questions, args.runs, args.warmup)
        results["memory"] = memory_snapshot(device)
        caught = [f"{w.category.__name__}: {w.message}" for w in seen]

    # Dedupe warnings (fallback notices repeat per op); keep order.
    results["warnings"] = list(dict.fromkeys(caught))
    wire_p50 = [results["cases"][label]["latency_ms"]["p50"] for label in ("wire-hotel", "wire-catalog", "wire-5k")]
    results["verdict"] = {
        "gate_p50_ms": GATE_MS,
        "wire_cases_p50_ms": dict(zip(("wire-hotel", "wire-catalog", "wire-5k"), wire_p50, strict=True)),
        "every_wire_case_below_gate": all(p50 < GATE_MS for p50 in wire_p50),
        "note": "ADR-001 gate is on loop decide p50 (see latency harness --decider local-d1); "
        "this smoke prices the forward pass the loop would pay per step at matched token counts.",
    }
    out_path.write_text(json.dumps(results, indent=1), encoding="utf-8")

    print(f"\n{'case':<18} {'tokens':>7} {'p50 ms':>9} {'p95 ms':>9} {'max ms':>9}  vs {GATE_MS:.0f} ms gate")
    for label, case in results["cases"].items():
        lat = case["latency_ms"]
        mark = "PASS" if lat["p50"] < GATE_MS else "FAIL"
        row = (
            f"{label:<18} {case['input_tokens']:>7} {lat['p50']:>9.1f} "
            f"{lat['p95']:>9.1f} {lat['max']:>9.1f}  {mark} (n={lat['n']})"
        )
        print(row)
    print(f"load {load_ms:.0f} ms | memory {results['memory']} | warnings {len(results['warnings'])} distinct")
    for line in results["warnings"][:10]:
        print(f"  warning: {line}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
