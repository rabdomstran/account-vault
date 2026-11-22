"""In-process local decider: Liquid AI d1-2B behind the System-One contract.

ADR-001 Decision 2 selects the backend by environment: a local vLLM
server, hosted Jev, or -- the path taken here -- d1-2B loaded
in-process via Transformers (``AutoModel.from_pretrained(...,
trust_remote_code=True).system_one(state, questions)``). The loop
never changes: ``choose()`` builds the exact speculative-fan-out
request the wire path sends (``model.build_choose_request``),
translates it into d1's native question schema, runs one forward
pass, and validates the answers through the same
``model.parse_choose_response`` / ``validate_choice`` strictness as
the hosted path. A malformed local answer therefore fails exactly
like a malformed hosted one, before any browser mutation.

Translation (``translate_request``) is deliberately mechanical. The
wire shape carries ``instructions`` as a dict (``{"goal": ...,
"rules": ...}``) and target ``criteria`` values as dicts
(``{"element": ..., "role": ...}``); d1's schema wants
``instructions`` as text and ``criteria`` values as text (its
``check_question`` requires string keys and renders values as
option descriptions). Both are rendered as compact JSON strings, so
nothing the wire request said is dropped or reworded -- the model
sees the same goal, rules, and element descriptions the hosted
model would, only stringified. The ``state`` dict passes through
unchanged (d1 accepts any JSON value as state).

The torch stack is heavy and lives in the ``local`` optional extra
(ADR-001 Decision 5), so ``torch``/``transformers`` are imported
lazily inside the loader, never at module import: the core loop
imports this module's pure translation helpers without them, and a
missing extra surfaces as a named ``ModelError`` with the install
command, not an ``ImportError`` traceback.

``trust_remote_code=True`` executes the checkpoint's own Python
files from the Hub. That is the model card's documented load path
and the code was read before first use here (see
docs/d1-mps-smoke.md); pin ``revision`` when reproducibility
matters, as the smoke script does.

Timing honesty on Apple Silicon: MPS work is asynchronous, so
``choose()`` synchronizes the MPS stream before stamping
``latency_ms`` -- without the sync the recorded latency would be
the enqueue time, not the forward pass. Compilation
(``model.compile()``, i.e. ``torch.compile``) is opt-in: the model
card's MPS figures are warm compiled calls, but Inductor on MPS
has no Triton-for-Metal path and is frequently no faster (or
slower) than eager; the smoke script measures both and the harness
records which one produced a number.
"""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .model import Decision, JsonDict, ModelError, Page, build_choose_request, parse_choose_response
from .settings import Settings, load_settings

DEFAULT_MODEL_ID = "LiquidAI/d1-2B"


def _as_text(value: Any) -> str:
    """Render one wire instructions/criteria value as d1 question text."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def translate_request(body: JsonDict) -> tuple[Any, dict[str, JsonDict]]:
    """One wire request body -> d1's ``(state, questions)`` pair.

    Pure and total over the bodies ``build_choose_request`` emits:
    every question keeps its name, ``type``, option names, and
    option order; only dict-valued text becomes JSON text.
    """
    questions: dict[str, JsonDict] = {}
    for name, question in body["questions"].items():
        criteria = question.get("criteria")
        translated: JsonDict = {
            "type": question["type"],
            "instructions": _as_text(question.get("instructions", "")),
        }
        if isinstance(criteria, Mapping):
            translated["criteria"] = {str(key): _as_text(value) for key, value in criteria.items()}
        elif criteria is not None:
            translated["criteria"] = criteria
        if question.get("multiple"):
            translated["multiple"] = True
        questions[name] = translated
    return body["state"], questions


def pick_device(requested: str | None, torch_module: Any) -> str:
    """Card order -- cuda, then mps, then cpu -- unless pinned."""
    if requested:
        return requested
    if torch_module.cuda.is_available():
        return "cuda"
    if torch_module.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_d1_model(
    model_id: str = DEFAULT_MODEL_ID,
    *,
    device: str | None = None,
    revision: str | None = None,
    compile_model: bool = False,
) -> tuple[Any, str]:
    """Load d1-2B the model card's way; return ``(model, device)``.

    bf16 off-CPU, float32 on CPU (card quick start). Raises
    ``ModelError`` naming the missing extra when torch/transformers
    are not installed.
    """
    try:
        import torch
        from transformers import AutoModel
    except ImportError:
        raise ModelError(
            "Local d1-2B needs the 'local' extra (torch/transformers); "
            'install with: uv sync --extra local (or: uv pip install -e ".[local]"). No action executed.'
        ) from None
    resolved = pick_device(device, torch)
    dtype = torch.float32 if resolved == "cpu" else torch.bfloat16
    model = AutoModel.from_pretrained(model_id, trust_remote_code=True, dtype=dtype, revision=revision).to(resolved)
    model.eval()
    if compile_model:
        model.compile()
    return model, resolved


def synchronize(device: str | None) -> None:
    """Block until queued accelerator work finishes (no-op on CPU).

    Imported lazily like the loader; safe to call when torch is
    absent only if ``device`` needs no sync -- callers that loaded a
    real model have torch by construction.
    """
    if device == "mps":
        import torch

        torch.mps.synchronize()
    elif device == "cuda":
        import torch

        torch.cuda.synchronize()


class LocalD1Decider:
    """Decide backend running d1-2B in-process (ADR-001 primary).

    Same contract as ``SystemOneDecider``: ``validate()`` fails fast
    at startup, ``choose()`` returns a fully validated Decision.
    The model loads lazily on first use (or eagerly via ``load()``)
    so constructing the decider never downloads 3.8 GB by surprise;
    tests inject a fake ``model`` with a ``system_one`` method and
    never touch torch.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        device: str | None = None,
        revision: str | None = None,
        compile_model: bool = False,
        settings: Settings | None = None,
        model: Any = None,
        done_when: Callable[[Page], bool] | None = None,
    ) -> None:
        self.model_id = model_id
        self.requested_device = device
        self.revision = revision
        self.compile_model = compile_model
        self.settings = settings or load_settings()
        self._model = model
        # Only set when a model was injected on a known device;
        # load() records the resolved device for synchronize().
        self.device: str | None = device if model is not None else None
        # Independent completion checker, same pickup convention as
        # HeuristicDecider.done_when (Agent reads it off the decider).
        self.done_when = done_when

    def validate(self) -> None:
        """Fail before any browser work if the local stack is absent.

        Import-only probe: loading weights here would make every
        Agent construction pay the download/load cost even when the
        run never decides.
        """
        if self._model is not None:
            return None
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            raise ModelError(
                "Local d1-2B needs the 'local' extra (torch/transformers); "
                'install with: uv sync --extra local (or: uv pip install -e ".[local]"). No action executed.'
            ) from None
        return None

    def load(self) -> Any:
        """Load (once) and return the underlying d1 model."""
        if self._model is None:
            self._model, self.device = load_d1_model(
                self.model_id, device=self.requested_device, revision=self.revision, compile_model=self.compile_model
            )
        return self._model

    def choose(self, state: Page, goal: str, history: Sequence[JsonDict]) -> Decision:
        body, operations, targets, controls = build_choose_request(state, goal, history, self.settings)
        d1_state, d1_questions = translate_request(body)
        model = self.load()
        started = time.perf_counter()
        result = model.system_one(d1_state, d1_questions)
        # MPS/CUDA enqueue asynchronously: the forward pass is not
        # done when system_one returns its already-read answers on
        # some paths, and latency_ms must be the pass, not the call.
        synchronize(self.device)
        if not isinstance(result, dict) or "answers" not in result:
            raise ModelError("Local d1-2B returned an unexpected response envelope; no action executed.")
        envelope = {
            "answers": result["answers"],
            "model": self.model_id,
            "usage": result.get("usage", {}),
        }
        return parse_choose_response(envelope, operations, targets, controls, body, started)
