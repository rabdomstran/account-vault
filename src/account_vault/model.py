"""Decide step: a System-One model makes choices; a small LLM writes field values.

Ported from jev_ultrafast/model.py (MIT, browser-use/jev-ultrafast). The core
design is kept intact: one request per cycle carrying one operation question
plus one speculative target question per operation type, strict validation of
every answer before anything executes, and only the selected operation's
target head is consumed. Every place this port deliberately differs from
upstream is marked DEVIATION with the upstream issue that forced it.
"""

import json
import math
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE
from .settings import load_settings


class ModelError(RuntimeError):
    """The decide/text provider failed; no action was executed."""


class MissingValueError(ValueError):
    """The text helper reported that a required value is missing ({"text": null})."""


# DEVIATION: upstream builds httpx.Client(http2=True) at import time, so merely
# importing the package crashes when a SOCKS proxy env var is set without the
# socksio package installed (upstream issue #16). The client is created lazily
# on first use; tests and the offline static-file path never trigger it.
_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(http2=True, timeout=25)
    return _CLIENT


def post_json(url, key, body):
    for attempt in range(3):
        try:
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            response = _client().post(url, json=body, headers=headers)
        except httpx.HTTPError:
            raise ModelError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise ModelError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        # DEVIATION: upstream returns response.json() unchecked. A non-JSON
        # body leaks a raw JSONDecodeError (upstream issue #87), and some
        # providers answer HTTP 200 with an {"error": ...} body, which
        # upstream then misreads as success until a later KeyError
        # (upstream issues #158 and #133). Both are model failures here.
        try:
            payload = response.json()
        except ValueError:
            raise ModelError("Model provider returned invalid JSON; no action executed.") from None
        if isinstance(payload, dict) and "error" in payload and not ({"answers", "choices"} & payload.keys()):
            detail = payload["error"]
            message = detail.get("message", detail) if isinstance(detail, dict) else detail
            raise ModelError(f"Model provider returned an error in an HTTP 200 body ({message}); no action executed.")
        return payload
    raise ModelError("Model unavailable")


def validate_choice(answer, ids):
    """Unchanged from upstream: this strictness is the safety core of the loop."""
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            # DEVIATION: also surface input_type so native date/time/range
            # controls (newly enumerated, see snapshot.js) are distinguishable
            # from plain textboxes by the decider (upstream issues #215/#227).
            keys = ("role", "value", "checked", "selected", "expanded", "input_type")
            element = {k: action[k] for k in keys if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def build_choose_request(state, goal, history, settings=None):
    """Build the single speculative-fan-out request. Extracted for testability."""
    settings = settings or load_settings()
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded", "input_type") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": settings.systemone_model,
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    return body, operations, targets, controls


def parse_choose_response(result, operations, targets, controls, body, started):
    """Validate and unpack a decide response.

    DEVIATION: upstream indexes result["answers"] / result["model"] directly,
    so an off-envelope HTTP 200 body raises a raw KeyError out of choose()
    (upstream issue #133). The envelope is checked here and reported as a
    ModelError naming that no action was executed.
    """
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        raise ModelError("Model provider returned an unexpected response envelope; no action executed.")
    answers = result["answers"]
    operation_answer = validate_choice(answers.get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    node = None
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(answers.get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        selected_action = targets[operation][target]
        choice = selected_action["id"]
        # DEVIATION: carry the stable node identity with the decision. Upstream
        # carries only the positional id ("e3"); if the page changes so a
        # different control inherits that position, the id-only lookup can
        # execute the wrong control without error (upstream issue #132).
        # Agent.act now requires id AND node to match the observation.
        node = selected_action.get("node")
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        if operation in controls:
            node = controls[operation].get("node")
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "node": node,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": answers,
        "model": result.get("model", "unknown"),
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def choose(state, goal, history, settings=None):
    settings = settings or load_settings()
    # DEVIATION: endpoint, model and key come from Settings (ADR-001: local
    # d1-2B by default, hosted Jev by env var). Upstream hardcodes
    # https://api.typesafe.ai/v1/systemone and os.environ["TYPESAFE_API_KEY"],
    # which locks out alternate gateways/backends and KeyErrors when unset
    # (upstream issues #241, #25, #133). A local backend may need no key.
    settings.validate(need_decide=True)
    body, operations, targets, controls = build_choose_request(state, goal, history, settings)
    started = time.perf_counter()
    result = post_json(settings.systemone_url, settings.systemone_api_key, body)
    return parse_choose_response(result, operations, targets, controls, body, started)


def field_context(goal, action, page, history):
    # DEVIATION: upstream sends only {label, role, value} for the field. Two
    # same-labeled fields (shipping vs billing "Street Address") then produce
    # byte-identical helper input: the text model cannot tell which field it
    # is writing, and the stale-retry cache cannot either (upstream issues
    # #198, #202, #191). The context now carries stable element identity
    # (node, element index, DOM id/name/placeholder when observed) and the
    # nearest form/fieldset/row scope text when the snapshot provides it.
    field = {k: action.get(k) for k in ("label", "role", "value")}
    for key in ("node", "index", "dom_id", "name", "placeholder", "input_type", "scope"):
        if action.get(key) not in (None, ""):
            field[key] = action[key]
    return {
        "goal": goal,
        "field": field,
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def _reasoning_field(settings):
    # DEVIATION: upstream always sends a reasoning field inferred from the
    # base URL, which some OpenAI-compatible providers reject with HTTP 400
    # (upstream issue #11). Reasoning is now driven only by explicit config:
    # "none" disables it (the .env.example default), "omit" sends nothing.
    mode = (settings.text_model_reasoning or "none").lower()
    if mode == "omit":
        return {}
    if mode == "none":
        return {"reasoning": {"enabled": False}}
    if "api.deepseek.com/" in settings.text_model_base_url:
        return {"thinking": {"type": "disabled"}}
    return {"reasoning": {"effort": mode}}


def _parse_field_text(result):
    content = result["choices"][0]["message"]["content"]
    # DEVIATION: small models often wrap the JSON in a markdown fence;
    # upstream's bare json.loads then aborts the whole run (upstream issue
    # #157). Strip one surrounding fence before parsing. Nothing else is
    # "repaired" — the payload must still be exactly {"text": ...}.
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines and lines[-1].strip().startswith("```") else lines[1:]).strip()
    output = json.loads(text)
    if set(output) != {"text"}:
        raise ValueError("unexpected keys")
    value = output["text"]
    # DEVIATION: {"text": null} is the documented "value missing" answer in
    # TEXT_VALUE, not a malformed payload (upstream issue #203). Report it
    # distinctly so the agent can stop as BLOCKED with a clear reason.
    if value is None:
        raise MissingValueError("Text helper reported the required value is missing; nothing typed.")
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("invalid text value")
    return value


def field_text(context, settings=None, attempts=3):
    settings = settings or load_settings()
    # Startup validation normally catches this in Agent; keep the guard so a
    # direct call still fails clearly instead of guessing (upstream kept this
    # part right). DEVIATION: defaults come from Settings so code and
    # .env.example can no longer disagree (upstream issue #36).
    settings.validate(need_text=True)
    base = settings.text_model_base_url.rstrip("/")
    model = settings.text_model
    body = {
        "model": model,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
        **_reasoning_field(settings),
        "messages": [
            {"role": "system", "content": TEXT_VALUE},
            {"role": "user", "content": json.dumps(context)},
        ],
    }
    started = time.perf_counter()
    last_error = None
    # DEVIATION: upstream makes exactly one attempt, so one transient
    # malformed sample or provider hiccup kills the entire run (upstream
    # issue #157). Retry the helper call (never a browser mutation) with
    # backoff; a missing-value answer is final and is not retried.
    for attempt in range(max(1, attempts)):
        try:
            result = post_json(base + "/chat/completions", settings.text_model_api_key, body)
            value = _parse_field_text(result)
            return value, {
                "model": model,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "usage": result.get("usage", {}),
                "attempts": attempt + 1,
            }
        except MissingValueError:
            raise
        except (ValueError, KeyError, TypeError) as error:
            last_error = error
        except ModelError as error:
            last_error = error
        if attempt + 1 < max(1, attempts):
            time.sleep(0.3 * 2**attempt)
    raise ValueError("Text helper returned no valid field value; nothing typed.") from last_error


class SystemOneDecider:
    """Decide backend speaking the /v1/systemone wire shape (ADR-001).

    DEVIATION: upstream exposes only the module-level choose() bound to the
    hosted TypeSafe endpoint. A decider object lets the agent run against a
    local server, a gateway, or an offline test double without loop changes
    (upstream issues #241, #25; ADR-001 Decision 2).
    """

    def __init__(self, settings=None):
        self.settings = settings or load_settings()

    def validate(self):
        self.settings.validate(need_decide=True)

    def choose(self, state, goal, history):
        return choose(state, goal, history, settings=self.settings)


class HeuristicDecider:
    """Offline rule-based decider with the same contract as SystemOneDecider.

    This is a test/demo double so the loop can run end to end against a
    static HTML file with no API key and no network. It is deliberately
    dumb: fill configured fields, set configured selects/checkboxes, click
    the configured submit control, then DONE only when the caller's
    independent done_when(page) check passes. It never invents values —
    field values come from the injected text provider, exactly as with the
    real backend. Probabilities are 1.0/0.0 so validate_choice semantics
    and history shape stay identical to the real path.
    """

    def __init__(
        self, *, fill_labels=(), select_labels=(), check_labels=(),
        click_labels=("Search", "Submit"), done_when=None,
    ):
        self.fill_labels = [label.lower() for label in fill_labels]
        self.select_labels = [label.lower() for label in select_labels]
        self.check_labels = [label.lower() for label in check_labels]
        self.click_labels = [label.lower() for label in click_labels]
        self.done_when = done_when

    def validate(self):
        return None

    def _decision(self, operation, action, targets, controls, state, goal, history):
        elements, _, _ = action_space(state["actions"])
        target = None
        probabilities = {}
        operations = {
            **{key: key for key in targets},
            **{key: key for key in controls},
            "DONE": "DONE",
            "BLOCKED": "BLOCKED",
        }
        operation_probs = {key: float(key == operation) for key in operations}
        target_probs = {}
        if action is not None and operation in targets:
            for index, candidate in targets[operation].items():
                target_probs[index] = float(candidate["id"] == action["id"])
                if candidate["id"] == action["id"]:
                    target = index
            probabilities = {a["id"]: target_probs[i] for i, a in targets[operation].items()}
            choice, node = action["id"], action.get("node")
        else:
            choice = operation
            node = controls[operation].get("node") if operation in controls else None
            if operation in controls:
                choice = controls[operation]["id"]
            probabilities = {choice: 1.0}
        return {
            "choice": choice,
            "node": node,
            "operation": operation,
            "target": target,
            "confidence": 1.0,
            "probabilities": probabilities,
            "operation_probabilities": operation_probs,
            "target_probabilities": target_probs,
            "target_confidence": 1.0 if target else None,
            "raw_answers": {},
            "model": "heuristic",
            "usage": {},
            "latency_ms": 0,
            "request": {"model": "heuristic", "elements": elements, "goal": goal},
        }

    def choose(self, state, goal, history):
        _, targets, controls = action_space(state["actions"])
        clicked = {h.get("action", "").lower() for h in history}
        # 1. Fill the first still-empty configured field.
        for index, action in targets.get("TYPE_TEXT", {}).items():
            label = action["label"].split(" → ")[0].lower()
            empty = not str(action.get("value", "")).strip()
            wanted = not self.fill_labels or any(w in label for w in self.fill_labels)
            if empty and wanted:
                return self._decision("TYPE_TEXT", action, targets, controls, state, goal, history)
        # 2. Apply a configured select whose target option was requested.
        for index, action in targets.get("SELECT", {}).items():
            label = action["label"].lower()
            already = {h.get("action") for h in history}
            if any(w in label for w in self.select_labels) and action["label"] not in already:
                return self._decision("SELECT", action, targets, controls, state, goal, history)
        # 3. Check configured unchecked checkboxes/switches.
        for index, action in targets.get("CLICK", {}).items():
            label = action["label"].lower()
            if any(w in label for w in self.check_labels) and str(action.get("checked", "false")).lower() != "true":
                return self._decision("CLICK", action, targets, controls, state, goal, history)
        # 4. DONE only on independent evidence, never on the decider's say-so.
        if self.done_when is not None and self.done_when(state):
            return self._decision("DONE", None, targets, controls, state, goal, history)
        # 5. Click the submit/search control once fields are ready.
        for index, action in targets.get("CLICK", {}).items():
            label = action["label"].lower()
            if any(w == label or w in label for w in self.click_labels) and action["label"].lower() not in clicked:
                return self._decision("CLICK", action, targets, controls, state, goal, history)
        if self.done_when is not None and self.done_when(state):
            return self._decision("DONE", None, targets, controls, state, goal, history)
        return self._decision("BLOCKED", None, targets, controls, state, goal, history)


class MappingTextProvider:
    """Text provider double: exact field values supplied by the caller.

    Matches field_text's (value, helper) return shape. Lookup is by field
    label substring (case-insensitive); unknown fields raise
    MissingValueError, mirroring the real helper's {"text": null} path.
    """

    def __init__(self, values):
        self.values = {key.lower(): value for key, value in values.items()}

    def __call__(self, context):
        label = str(context.get("field", {}).get("label", "")).lower()
        for key, value in self.values.items():
            if key in label:
                return value, {"model": "mapping", "latency_ms": 0, "usage": {}, "attempts": 1}
        raise MissingValueError(f"No supplied value for field {label!r}; nothing typed.")
