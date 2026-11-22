"""Run the loop against a static HTML file, fully offline by default.

    uv run python -m account_vault
    uv run python -m account_vault --html examples/hotel_search.html \\
        --fill Destination=Zurich --fill Check-in=2027-03-01 \\
        --select promo --check "Free cancellation"

The default decider is the rule-based HeuristicDecider (no network, no API
key) so the perceive -> decide -> act machinery can be exercised anywhere.
Pass --decider systemone to drive the same static page with the real
System-One backend + text helper configured in .env (ADR-001).

Success is decided by verify(), an independent check of the final page —
never by the decider's DONE answer (upstream design rule, kept).
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .agent import Agent
from .model import HeuristicDecider, MappingTextProvider
from .settings import load_settings

DEFAULT_HTML = Path(__file__).resolve().parents[2] / "examples" / "hotel_search.html"
DEFAULT_GOAL = (
    "Search stays near Zurich checking in on 2027-03-01 for the promo "
    "1-guest rate with free cancellation. Stop when matching stays are visible."
)
DEFAULT_FILLS = {"destination": "Zurich", "check-in": "2027-03-01", "notes": "Late arrival"}


def verify(page: dict[str, Any], expect: dict[str, str] | None = None) -> dict[str, Any]:
    """Independent outcome checks on the resulting page (not the model's DONE)."""
    expect = {**DEFAULT_FILLS, **(expect or {})}
    actions = page["actions"]
    by_label: dict[str, dict[str, Any]] = {}
    for action in actions:
        by_label.setdefault(action["label"].split(" → ")[0], action)

    def field_value(fragment: str) -> Any:
        for label, action in by_label.items():
            if fragment.lower() in label.lower():
                return action.get("current_value", action.get("value", ""))
        return None

    def is_checked(fragment: str) -> bool:
        for label, action in by_label.items():
            if fragment.lower() in label.lower():
                return str(action.get("checked", "")).lower() == "true"
        return False

    checks = {
        "destination": field_value("destination") == expect["destination"],
        "check_in": field_value("check-in") == expect["check-in"],
        # current_value is the selected option's LABEL: with duplicate
        # values ("1" twice) only index-based selection lands on promo.
        "guests_promo": field_value("guests") == "1 guest (promo)",
        "free_cancellation": is_checked("free cancellation"),
        "notes": field_value("notes") == expect["notes"],
        "results_visible": f"stays near {expect['destination']}" in page["text"],
    }
    return {"passed": all(checks.values()), "checks": checks}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the perceive-decide-act loop on a static HTML file.")
    parser.add_argument("--html", default=str(DEFAULT_HTML), help="Static HTML file to drive")
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument(
        "--fill",
        action="append",
        default=[],
        metavar="LABEL=VALUE",
        help="Field value for the offline text provider (repeatable)",
    )
    parser.add_argument("--select", action="append", default=[], help="Select-option label fragment (repeatable)")
    parser.add_argument("--check", action="append", default=[], help="Checkbox label fragment (repeatable)")
    parser.add_argument("--click", action="append", default=[], help="Submit/control label fragment (repeatable)")
    parser.add_argument("--decider", choices=("heuristic", "systemone"), default="heuristic")
    args = parser.parse_args(argv)

    fills = dict(DEFAULT_FILLS)
    for item in args.fill:
        label, _, value = item.partition("=")
        fills[label.strip().lower()] = value
    expect = dict(fills)

    if args.decider == "systemone":
        settings = load_settings()
        # The real decider's DONE is accepted only through the same
        # independent verify() used for the final report below.
        agent = Agent(args.html, args.goal, settings=settings, done_when=lambda page: verify(page, expect)["passed"])
    else:
        decider = HeuristicDecider(
            fill_labels=tuple(fills),
            select_labels=tuple(args.select) or ("promo",),
            check_labels=tuple(args.check) or ("free cancellation",),
            click_labels=tuple(args.click) or ("Search",),
            done_when=lambda page: verify(page, expect)["passed"],
        )
        agent = Agent(
            args.html,
            args.goal,
            decider=decider,
            text_provider=MappingTextProvider(fills),
        )

    with agent:
        state = agent.snapshot()
        print(f"start  {state['page']['url']}  elements={len(state['elements'])}")
        for state in agent.run():
            last = state["history"][-1] if state["history"] else {}
            detail = f" -> {last['text']!r}" if last.get("text") else ""
            print(
                f"{state['elapsed_ms']:>5} ms  step {len(state['history']):>2}  "
                f"{last.get('operation', '-')} {last.get('action', '')}{detail}  [{state['status']}]"
            )
        result = verify(state["page"], expect)
        reason = f"  reason={state['blocked_reason']}" if state.get("blocked_reason") else ""
        print(
            f"final  status={state['status']}  decisions={len(state['decisions'])}  "
            f"text_calls={len(state['text_calls'])}{reason}"
        )
        print(json.dumps({"verification": result}, indent=2))
    if state["status"] != "done" or not result["passed"]:
        raise SystemExit("Static run did not satisfy the independent checks")
    print("OK: loop reached DONE and independent verification passed.")


if __name__ == "__main__":
    main()
