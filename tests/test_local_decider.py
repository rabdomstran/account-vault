"""Offline contracts for the in-process d1-2B decider (no torch, no download).

The real model needs the `local` extra plus 3.8 GB of weights, so CI
never loads it. What is pinned here is everything around the forward
pass: the wire->d1 translation is lossless and schema-clean, the
answers go through the same validate_choice strictness as the
hosted path (a malformed local answer executes nothing), the
device/dtype/compile plumbing follows the model card, and a missing
torch stack is a named ModelError with the install command. The
fake model records exactly what the adapter asked it.
"""

import json
import sys
import types

import pytest

from account_vault import local_decider, model
from account_vault.browser import fingerprint
from account_vault.local_decider import LocalD1Decider, pick_device, translate_request
from account_vault.settings import Settings

SETTINGS = Settings(systemone_model="d1-2B")


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def _answer(ids, selected):
    probs = {i: float(i == selected) for i in ids}
    return {"type": "choice", "choice": selected, "confidence": probs[selected], "probabilities": probs}


class FakeD1:
    """Stands in for the loaded checkpoint: answers from the asked ids."""

    def __init__(self, picks=None, mutate=None):
        self.calls = []
        self.picks = picks or {}
        self.mutate = mutate

    def system_one(self, state, questions, media=None):
        self.calls.append((state, questions))
        answers = {}
        for name, question in questions.items():
            ids = list(question["criteria"])
            answers[name] = _answer(ids, self.picks.get(name, ids[0]))
        if self.mutate:
            self.mutate(answers)
        return {"answers": answers, "usage": {"input_tokens": 123, "output_tokens": 0}}


def test_translate_request_stringifies_dicts_and_keeps_every_option():
    body, operations, targets, _ = model.build_choose_request(page(), "Find stays", [], SETTINGS)
    state, questions = translate_request(body)
    assert state == body["state"]
    assert set(questions) == set(body["questions"])
    for name, question in questions.items():
        assert question["type"] == "choice"
        # d1's check_question wants instructions as text and string
        # criteria keys with text descriptions.
        assert isinstance(question["instructions"], str) and question["instructions"].strip()
        assert all(isinstance(v, str) for v in question["criteria"].values())
    # Nothing was dropped or reworded: the dict payloads round-trip.
    assert json.loads(questions["operation"]["instructions"]) == body["questions"]["operation"]["instructions"]
    assert list(questions["operation"]["criteria"]) == list(operations)
    for operation, candidates in targets.items():
        translated = questions[operation.lower() + "_target"]["criteria"]
        assert list(translated) == list(candidates)
        for index in candidates:
            assert json.loads(translated[index]) == body["questions"][operation.lower() + "_target"]["criteria"][index]


def test_translate_request_passes_string_instructions_and_list_criteria_through():
    body = {
        "state": "a state",
        "questions": {
            "urgent": {"type": "noul", "instructions": "Is it urgent?"},
            "level": {"type": "score", "instructions": "How big?", "criteria": ["small", "large"]},
            "tags": {"type": "choice", "multiple": True, "instructions": "Which?", "criteria": {"a": "A"}},
        },
    }
    state, questions = translate_request(body)
    assert state == "a state"
    assert questions["urgent"] == {"type": "noul", "instructions": "Is it urgent?"}
    assert questions["level"]["criteria"] == ["small", "large"]
    assert questions["tags"]["multiple"] is True


def test_choose_runs_one_pass_and_returns_a_validated_decision():
    fake = FakeD1(picks={"operation": "CLICK", "click_target": "2"})
    decider = LocalD1Decider(settings=SETTINGS, model=fake, device="cpu")
    decision = decider.choose(page(), "Find stays", [])
    assert len(fake.calls) == 1
    assert decision["operation"] == "CLICK"
    assert decision["choice"] == "e3"  # element index 2 is the Go button
    assert decision["node"] == 20
    assert decision["model"] == "LiquidAI/d1-2B"
    assert decision["usage"] == {"input_tokens": 123, "output_tokens": 0}
    assert decision["latency_ms"] >= 0
    from account_vault.agent import _check_decision

    _check_decision(decision)  # the loop's own shape guard accepts it


def test_choose_rejects_malformed_local_answers_like_the_wire_path():
    def break_probabilities(answers):
        first = next(iter(answers.values()))
        first["probabilities"] = {k: 0.5 for k in first["probabilities"]}  # no longer sums/argmax-consistent

    decider = LocalD1Decider(settings=SETTINGS, model=FakeD1(mutate=break_probabilities), device="cpu")
    with pytest.raises(ValueError, match="Invalid TypeSafe response"):
        decider.choose(page(), "Find stays", [])


def test_choose_rejects_an_off_envelope_local_result():
    class Empty:
        def system_one(self, state, questions, media=None):
            return {"usage": {}}

    decider = LocalD1Decider(settings=SETTINGS, model=Empty(), device="cpu")
    with pytest.raises(model.ModelError, match="unexpected response envelope"):
        decider.choose(page(), "Find stays", [])


def _fake_torch(cuda=False, mps=False):
    torch_stub = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)),
        float32="f32",
        bfloat16="bf16",
    )
    return torch_stub


def test_pick_device_follows_the_card_order_unless_pinned():
    assert pick_device("cpu", _fake_torch(cuda=True, mps=True)) == "cpu"
    assert pick_device(None, _fake_torch(cuda=True, mps=True)) == "cuda"
    assert pick_device(None, _fake_torch(cuda=False, mps=True)) == "mps"
    assert pick_device(None, _fake_torch(cuda=False, mps=False)) == "cpu"


def test_load_d1_model_without_the_extra_names_the_install(monkeypatch):
    for name in ("torch", "transformers"):
        monkeypatch.setitem(sys.modules, name, None)  # import raises ImportError
    with pytest.raises(model.ModelError, match="local.*extra"):
        local_decider.load_d1_model()
    with pytest.raises(model.ModelError, match="local.*extra"):
        LocalD1Decider(settings=SETTINGS).validate()


def test_validate_accepts_an_injected_model_without_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    assert LocalD1Decider(settings=SETTINGS, model=FakeD1()).validate() is None


def test_load_d1_model_loads_bf16_on_mps_and_compiles_only_when_asked(monkeypatch):
    calls = {}

    class Loaded:
        def to(self, device):
            calls["to"] = device
            return self

        def eval(self):
            calls["eval"] = True
            return self

        def compile(self):
            calls["compile"] = True

    def from_pretrained(model_id, **kwargs):
        calls["from_pretrained"] = (model_id, kwargs)
        return Loaded()

    monkeypatch.setitem(sys.modules, "torch", _fake_torch(mps=True))
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        types.SimpleNamespace(AutoModel=types.SimpleNamespace(from_pretrained=from_pretrained)),
    )
    loaded, device = local_decider.load_d1_model(revision="abc123")
    assert device == "mps" and calls["to"] == "mps" and calls["eval"] is True
    assert calls["from_pretrained"] == (
        "LiquidAI/d1-2B",
        {"trust_remote_code": True, "dtype": "bf16", "revision": "abc123"},
    )
    assert "compile" not in calls

    calls.clear()
    local_decider.load_d1_model(compile_model=True)
    assert calls["compile"] is True

    # CPU loads in float32, per the card quick start.
    monkeypatch.setitem(sys.modules, "torch", _fake_torch())
    calls.clear()
    _, device = local_decider.load_d1_model()
    assert device == "cpu" and calls["from_pretrained"][1]["dtype"] == "f32"


def test_decider_loads_lazily_and_only_once(monkeypatch):
    fake = FakeD1()
    loads = []

    def fake_load(model_id, **kwargs):
        loads.append((model_id, kwargs))
        return fake, "cpu"

    monkeypatch.setattr(local_decider, "load_d1_model", fake_load)
    decider = LocalD1Decider(settings=SETTINGS)
    assert loads == []  # construction downloads nothing
    assert decider.load() is fake and decider.device == "cpu"
    decider.choose(page(), "Find stays", [])
    assert len(loads) == 1 and len(fake.calls) == 1


def test_synchronize_is_a_noop_without_an_accelerator():
    local_decider.synchronize(None)
    local_decider.synchronize("cpu")
