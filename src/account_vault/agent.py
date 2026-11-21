"""The complete agent loop. Typed choices, observable state, bounded execution.

Ported from jev_ultrafast/agent.py (MIT, browser-use/jev-ultrafast): the
perceive (Browser.observe) -> decide (one speculative-fan-out request) ->
act (consume-once, freshness-checked execution) cycle, split into
predict/act/tick commands exactly as upstream. DEVIATION comments mark
every deliberate difference and the upstream issue behind it.
"""

import base64
import json
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol, Self

from .browser import CdpBrowser, StalePage
from .model import MissingValueError, SystemOneDecider, action_space, field_context, field_text
from .questions import MAX_ACTIONS, MAX_DECISIONS
from .settings import Settings, load_settings
from .static_dom import StaticBrowser, is_static_source

JsonDict = dict[str, Any]


class BrowserLike(Protocol):
    """The browser contract both backends (CDP, static) implement."""

    def observe(self, screenshot: bool = ...) -> JsonDict: ...
    def fresh(self, page: JsonDict, action: JsonDict | None = ...) -> bool: ...
    def act(self, action: JsonDict, page: JsonDict, text: str | None = ...) -> Any: ...
    def close(self) -> None: ...


class DeciderLike(Protocol):
    def choose(self, state: JsonDict, goal: str, history: Sequence[JsonDict]) -> JsonDict: ...


TextProvider = Callable[[JsonDict], tuple[str, JsonDict]]


def _count_elements(page: JsonDict) -> int:
    return len(action_space(page["actions"])[0])


def _check_decision(decision: Any) -> None:
    # DEVIATION: the loop accepts injected deciders, so unlike stock
    # upstream choose() a decision's shape is not guaranteed by its
    # producer. Unchecked, a malformed decision (e.g. probabilities
    # missing the chosen id) executed its browser mutation first and then
    # crashed with a raw KeyError while recording history — mutation done,
    # record lost. Validate the full shape the act path indexes, before
    # anything can execute (upstream issue #133: no raw KeyError escapes).
    valid = (
        isinstance(decision, dict)
        and isinstance(decision.get("choice"), str)
        and bool(decision.get("choice"))
        and isinstance(decision.get("probabilities"), dict)
        and decision.get("choice") in decision.get("probabilities", {})
        and all(key in decision for key in ("operation", "confidence", "latency_ms", "usage", "target"))
    )
    if not valid:
        raise ValueError("Invalid decide response; no action executed.")


class Agent:
    def __init__(
        self,
        url: str,
        goals: str | Sequence[str],
        *,
        record_dir: str | Path | None = None,
        screenshots: bool = False,
        browser: BrowserLike | None = None,
        decider: DeciderLike | None = None,
        text_provider: TextProvider | None = None,
        settings: Settings | None = None,
        min_confidence: float = 0.0,
        max_empty_observes: int = 5,
        max_stale_decisions: int = 3,
        max_cycle_repeats: int = 3,
    ) -> None:
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text: tuple[tuple[Any, str], str, JsonDict] | None = None
        self.settings = settings or load_settings()
        # DEVIATION: browser, decider and text provider are injectable, and a
        # local .html path / file:// URL selects the offline StaticBrowser.
        # Upstream always constructs the CDP Browser and calls module-level
        # choose()/field_text(), so the loop cannot run without Chrome plus
        # hosted API keys. Defaults preserve upstream behaviour for http(s)
        # URLs (CDP + SystemOne wire + OpenAI-compatible text helper).
        self.browser: BrowserLike
        if browser is not None:
            self.browser = browser
        elif is_static_source(url):
            self.browser = StaticBrowser(url)
        else:
            self.browser = CdpBrowser(url)
        self.decider: DeciderLike = decider or SystemOneDecider(self.settings)
        self.text_provider: TextProvider = text_provider or (
            lambda context: field_text(context, settings=self.settings)
        )
        self.min_confidence = min_confidence
        self.max_empty_observes = max_empty_observes
        self.max_stale_decisions = max_stale_decisions
        self.max_cycle_repeats = max_cycle_repeats
        self._low_confidence_reobserves = 0
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self._observe_settled()
            # DEVIATION: validate backend config at startup, before any
            # action can execute. Upstream discovers a missing
            # TEXT_MODEL_API_KEY mid-run, after earlier steps already ran
            # (upstream issue #36). The text key is required up front only
            # when the first page actually has editable fields and the real
            # helper (not an injected provider) will serve them.
            validate = getattr(self.decider, "validate", None)
            if callable(validate):
                validate()
            if text_provider is None and any(a["kind"] == "fill" for a in page["actions"]):
                self.settings.validate(need_text=True)
        except Exception:
            self.browser.close()
            raise
        self.state: JsonDict = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            stale_streak=0,
            blocked_reason=None,
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            # Static pages have no screenshot; record only when one exists.
            if page.get("screenshot"):
                (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def _observe_settled(self) -> JsonDict:
        """Observe, retrying while the element table is still empty.

        DEVIATION: upstream's first observation can return zero indexed
        elements (the readyState race in Browser.__init__ samples the
        pre-navigation document); the decider then sees an empty action
        space and the run ends as BLOCKED in under a second with no retry
        (upstream issue #1). An empty table on a settling page is a
        perception timing problem, not evidence the task is impossible, so
        re-observe a bounded number of times before ever asking the model.
        A page that is legitimately empty still proceeds after the bound.
        """
        page = self.browser.observe(screenshot=self.screenshots)
        attempts = 0
        while _count_elements(page) == 0 and attempts < self.max_empty_observes:
            time.sleep(0.05)
            page = self.browser.observe(screenshot=self.screenshots)
            attempts += 1
        return page

    def snapshot(self) -> JsonDict:
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def _elapsed(self, state: JsonDict) -> int:
        if state["started_at"] is None:
            return int(state["elapsed_ms"])
        return int(round((time.perf_counter() - state["started_at"]) * 1000))

    def command(self, name: str, body: JsonDict | None = None) -> JsonDict:
        body = body or {}
        state = self.state
        if name == "tick":
            history_before = len(state["history"])
            try:
                self.command("predict", {})
                # A low-confidence predict leaves no decision (see below);
                # there is nothing to act on this tick.
                if state["decision"] is None:
                    return self.snapshot()
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage as error:
                state["decision"] = None
                # DEVIATION: bound consecutive never-executed decisions.
                # Upstream's only stuck-run guard counts executed actions
                # in history; a decision whose target is missing (bad
                # index, vanished element, identity change — #132) raises
                # StalePage here and records no history, so that guard
                # could never fire and the run re-asked the model until
                # the 120-call budget (#55) crashed run() — #94's "spend
                # the entire budget proving nothing happened" via stale
                # decisions. Three in a row (mirroring the 3-repeat
                # no-progress stop) end the run blocked. A tick that did
                # execute before its post-action observation went stale
                # is progress, not a strike.
                executed = len(state["history"]) > history_before
                state["stale_streak"] = 0 if executed else state.get("stale_streak", 0) + 1
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = self._elapsed(state)
                if state["stale_streak"] >= self.max_stale_decisions:
                    state["status"] = "blocked"
                    state["blocked_reason"] = f"{state['stale_streak']} consecutive decisions never executed: {error}"
                else:
                    state["status"] = "ready"
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            # DEVIATION: named budget. Upstream's `MAX_STEPS * 2` is
            # undocumented and its error does not say which limit fired
            # (upstream issue #55).
            if len(state["decisions"]) >= MAX_DECISIONS:
                # Terminal state first, error second (as act already does
                # for MAX_ACTIONS), so run() can land on blocked instead
                # of leaking this as a traceback (upstream issue #55).
                state["status"] = "blocked"
                state["blocked_reason"] = f"Reached the {MAX_DECISIONS}-request model-call budget"
                raise ValueError(state["blocked_reason"])
            decision = self.decider.choose(state["page"], state["goal"], state["history"])
            _check_decision(decision)
            state["decisions"].append(
                {
                    **decision,
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": self._elapsed(state),
                }
            )
            # DEVIATION: optional confidence gate for ordinary actions.
            # Upstream records confidence but executes whatever was chosen,
            # however unsure the model was (upstream issue #120). With
            # min_confidence > 0, a below-threshold decision is not acted
            # on: re-observe once (the page may still be settling), and stop
            # as blocked if the model is still unsure. Default 0.0 keeps
            # upstream behaviour; ADR-001's escalation hook plugs in here.
            if (
                self.min_confidence > 0
                and decision["operation"] not in {"DONE", "BLOCKED"}
                and decision["confidence"] < self.min_confidence
            ):
                self._low_confidence_reobserves += 1
                if self._low_confidence_reobserves >= 2:
                    state["status"] = "blocked"
                    state["elapsed_ms"] = self._elapsed(state)
                else:
                    state["page"] = state["browser"].observe(screenshot=self.screenshots)
                    state["status"] = "ready"
                return self.snapshot()
            self._low_confidence_reobserves = 0
            state["decision"] = decision
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            _check_decision(decision)  # direct act callers get predict's guard too
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = self._elapsed(state)
                return self.snapshot()
            # DEVIATION: resolve by id AND stable node. Upstream resolves by
            # positional id alone, so if a different live control inherited
            # the id the wrong control is clicked with no error (upstream
            # issue #132). A node mismatch is stale: re-observe, never act.
            expected_node = decision.get("node")
            action = next(
                (
                    a
                    for a in page["actions"]
                    if a["id"] == selected and (expected_node is None or a.get("node") == expected_node)
                ),
                None,
            )
            if action is None:
                raise StalePage("Selected element changed identity since the decision. Observe again.")
            if len(state["history"]) >= MAX_ACTIONS:
                state["status"] = "blocked"
                state["blocked_reason"] = f"Stopped at the {MAX_ACTIONS}-action budget"
                raise ValueError(state["blocked_reason"])
            text: str | None = None
            helper: JsonDict | None = None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                # DEVIATION: the stale-retry cache key includes the element's
                # stable node. Upstream keys only on the helper input, so a
                # value generated for one field can be typed into a
                # different same-labeled field (upstream issue #191).
                cache_key = (action.get("node"), json.dumps(context, sort_keys=True, default=str))
                try:
                    if self.pending_text and self.pending_text[0] == cache_key:
                        _, text, helper = self.pending_text
                    else:
                        text, helper = self.text_provider(context)
                        self.pending_text = (cache_key, text, helper)
                        state["text_calls"].append({**helper, "field": action["label"], "value": text})
                except MissingValueError as error:
                    # DEVIATION: a documented {"text": null} answer means
                    # "cannot fill this field", i.e. no supported operation
                    # can progress — stop as blocked with the reason kept,
                    # instead of upstream's generic malformed-payload crash
                    # (upstream issue #203).
                    state["status"] = "blocked"
                    state["blocked_reason"] = str(error)
                    state["elapsed_ms"] = self._elapsed(state)
                    return self.snapshot()
            # Browser.act checks freshness immediately before input, including after text generation.
            state["browser"].act(action, page, text=text)
            state["stale_streak"] = 0  # executed: not a stale strike (see tick)
            self.pending_text = None
            state["elapsed_ms"] = self._elapsed(state)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": self._elapsed(state),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = self._elapsed(state)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if self.record_dir is not None and state["page"].get("screenshot"):
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            # DEVIATION: page_changed stays None when the post-action
            # observation failed (the exception path above preserves the
            # history entry). Upstream's `is False` test then disables this
            # no-progress stop forever and a stalled run keeps spending
            # model calls (upstream issue #94). None means "no observed
            # progress" and counts toward the stop, like False.
            no_progress = len(repeated) == 3 and all(
                h["page_changed"] in (False, None) and h["kind"] != "wait" for h in repeated
            )
            state["status"] = "blocked" if no_progress else "ready"
            if no_progress:
                state["blocked_reason"] = "Three consecutive actions left the page unchanged"
            elif self.max_cycle_repeats and action["kind"] not in {"wait", "scroll"}:
                # DEVIATION: page-changing cycle stop (upstream issue
                # #247: sort -> index -> category -> repeat, 44 actions,
                # every step changing the page so repeat/unchanged guards
                # never fire). No fix exists upstream — the issue is open
                # at HEAD with no linked PR (checked with gh, 2026-11-21);
                # adjacent PRs (#245 covered reoffer, #209 no-op caps,
                # #212/#195 scroll) do not address cycles. The issue's own
                # comment thread proposes tracking recent (url, control)
                # pairs and giving up cleanly; we adopt that in bounded
                # form: the same (action label, landing URL) pair executed
                # max_cycle_repeats times within the last 12 actions ends
                # the run blocked. We block instead of dropping the
                # control from the next offered set (the comment's other
                # half) because mutating the offered set mid-decision
                # breaks the one-request/atomicity contract; escalation
                # can revisit with a fresh observation. 0 disables.
                pair = (action["label"], state["history"][-1]["url"])
                recent = [h for h in state["history"][-12:] if h["kind"] not in {"wait", "scroll"}]
                repeats = sum(1 for h in recent if (h["action"], h["url"]) == pair)
                if repeats >= self.max_cycle_repeats:
                    state["status"] = "blocked"
                    state["blocked_reason"] = (
                        f"Action cycle: {pair[0]!r} landed on {pair[1]} "
                        f"{repeats} times in the last {len(recent)} actions"
                    )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self) -> Iterator[JsonDict]:
        while self.state["status"] not in {"done", "blocked"}:
            try:
                yield self.command("tick")
            except ValueError:
                # DEVIATION: budget exhaustion already set the terminal
                # blocked state (+ blocked_reason) before raising, for
                # command-level callers. run() is drive-to-completion:
                # surface that as a clean blocked finish, not a traceback
                # (upstream issues #55/#94). Other ValueErrors propagate.
                if self.state["status"] != "blocked":
                    raise
                yield self.snapshot()
                return

    def close(self) -> None:
        self.browser.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
