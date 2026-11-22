# ADR-001: System-One model for the decide step

- **Status:** Accepted — **validation gate FAILED on latency
  (2026-11-22, see addendum below); revisit triggered**
- **Date:** 2026-11-21
- **Deciders:** Project owner + Muse Code (research)
- **References:**
  - [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) — loop design, `jev_ultrafast/agent.py`, `jev_ultrafast/model.py`, `docs/performance.md`
  - [andyrewlee/awesome-system-one](https://github.com/andyrewlee/awesome-system-one) — model census (Models / Hosted / Serving sections)
  - [Cactus Compute — Mirage](https://cactuscompute.com/blog/mirage) + [Cactus-Compute/mirage model card](https://huggingface.co/Cactus-Compute/mirage)
  - Model cards read in full: [LiquidAI/d1-2B](https://huggingface.co/LiquidAI/d1-2B), [baselabs/joey-1](https://huggingface.co/baselabs/joey-1), [Beveridge-Large-C](https://huggingface.co/sathiskumarjothi0/Beveridge-Large-C), [TypeSafe Models docs](https://docs.typesafe.ai/models.md), [Contrastive-LM/CLM](https://github.com/Contrastive-LM/CLM), [jaredpalmer/kev](https://github.com/jaredpalmer/kev), [NandhaKishorM/laya](https://github.com/NandhaKishorM/laya)

## Context

This repo builds an ultrafast autonomous browsing agent: a tight
**perceive → decide → act** loop where each step should cost milliseconds, not
the seconds a frontier LLM needs to generate and parse free-form text. Later
the agent runs flows end to end (form filling, creating its own logins), which
is why an encrypted, local-first account store (`~/.local/share/account-vault/`,
never in the repo) is part of the same project. Speed is the reason to exist:
if the loop is not dramatically faster than a normal LLM browser agent, there
is no product.

### What jev-ultrafast proves, and the budget it sets

`jev-ultrafast` is the reference loop. Its design, not its brand, is what we
adopt:

1. **Indexed action space.** Each observation atomically reads visible DOM
   controls into a numbered element table (`[1] button …`, `[2] combobox …`).
   The model never emits selectors, coordinates, or code — only an operation
   (`CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP/DOWN`, `WAIT`, `DONE`,
   `BLOCKED`) and an element index that must resolve to an observed node.
2. **One request per cycle, speculative fan-out.** A single
   `POST /v1/systemone` call carries the shared `state` (URL, title, visible
   text, element table, last 10 actions) plus parallel questions: one
   `operation` choice and one target choice *per operation type*, each
   restricted to compatible elements. Only the selected operation's target
   head executes. Two decisions, one round trip (`model.py: choose()`).
3. **No screenshots in the loop by default.** Structured DOM state is the
   input; screenshots are for the inspector/recording only. One browser call
   per snapshot; post-action waits are capped (≤200 ms for combobox
   suggestions, ≤50 ms / two animation frames otherwise).
4. **Text is the only generation.** `TYPE_TEXT` delegates to a small
   OpenAI-compatible LLM (the demo uses `inception/mercury-2.5`, reasoning
   disabled) that must return a tiny validated JSON object. Everything else
   is a typed choice with probabilities.

Measured on Google Flights (Zürich → London, verified independently):
**7.073 s end to end, 17 System-One requests, median decide latency 178 ms**,
~5.3k input tokens per request on average (90,558 tokens / 17 requests), with
the two text-helper calls costing 581 ms and 346 ms. Matched runs cut median
task time 9.450 s → 7.092 s and browser protocol calls 1,092 → 101 versus the
original runtime. Wikipedia lookup took 2.798 s; a local hotel filter task
1.896 s.

**Budget derived from that evidence:** to match or beat the reference, the
decide step must sustain **< 200 ms p50 end to end** (network included) and
ideally **< 50 ms of model compute**, so browser waits — not inference —
dominate. A hosted API already spends much of that budget on round-trip
latency before any compute happens. That is the central tension this ADR
resolves.

### Requirements for the decide model

| # | Requirement | Why |
|---|---|---|
| R1 | Single forward pass, typed `choice` / `noul` / `score` answers with calibrated probabilities + confidence; nothing to parse | Parsing generated JSON is the failure mode we are removing; confidence drives act / re-observe / escalate |
| R2 | Many questions over one shared state in **one** call (operation + speculative target heads) | Two sequential calls would double latency and break atomicity with the observed snapshot |
| R3 | `choice` over **up to ~255 dynamic options** whose labels change every page | Target heads index live elements; fixed-class classifiers cannot express this |
| R4 | Validated context **≥ 8k tokens, preferably ≥ 16k** (state + largest question) | Reference runs average ~5k tokens/request; dense pages (tables, long forms) will exceed 4k |
| R5 | **< 200 ms p50** decide latency on our hardware (Apple M4 dev machine; no NVIDIA GPU assumed) | See budget above |
| R6 | Runs **locally / open weights** with a commercially usable license | Credential flows must not ship page state (which will contain emails, form values, session data) to a hosted API by default; also removes per-token cost at high step counts |
| R7 | Speaks (or cleanly adapts to) the **TypeSafe `/v1/systemone` wire** | Lets us reuse the jev-ultrafast loop shape and swap models by repointing a base URL |
| R8 | Multimodal input (screenshot) at least as a fallback | DOM snapshots miss canvas, shadow DOM, and image-only controls; perception fallback needs a path in |

## Options considered

The awesome-system-one list catalogues 100+ entrants. Most fail R3–R6 on
inspection (see Rejected). Three finalists can realistically sustain the loop.

### Option A — TypeSafe Jev 1.14 (hosted) — the proven baseline

The original System-One model and the one jev-ultrafast was built and measured
on (`jev-latest` → `jev-1.14.0`).

- **Interface:** `POST /v1/systemone`; `choice` / `score` / `noul` / `box`
  (1.14); unlimited questions per request subject to context; text + PNG/JPEG
  image input.
- **Context:** 64k per-request / 32k per-question by default; 262k both with
  the `TypeSafe-Danger-Zone-Long-Context: true` header (slower, less
  accurate). Comfortably satisfies R4.
- **Latency / cost:** list describes ~70–500 ms; **178 ms median measured** in
  the Flights recording; Laya's independent comparison measured 364 ms per
  request from India. **$0.042 / 1M input tokens, output free.** At ~5k
  tokens/step that is ~$0.0002/step — cheap, but not zero, and latency is
  dominated by network + queueing we do not control.
- **Accuracy:** the reference point every open model chases (e.g. joey-1
  reports Jev 1.14 at 60.70 on the Jev Decision Index 0.4 public split;
  Airbnb's benchmark, quoted in the list, calls it the most accurate
  off-the-shelf model under 100 ms). No fine-tuning; behaviour is shaped via
  `state` / `instructions` / `criteria` only, and the `jev-latest` alias can
  move under tuned thresholds.
- **Fit:** satisfies R1–R5 and R8 today with zero infrastructure. Fails R6:
  closed weights, every page state leaves the machine. For an agent whose
  roadmap is *creating and storing its own logins*, that is a structural
  privacy problem, not a preference.

### Option B — Liquid AI d1-2B (open weights) — **selected**

A 1.92B-parameter decision model post-trained from `LFM3-VL-2B` (24 layers:
gated short convolutions + sliding-window + global attention). Returns
calibrated typed answers in one forward pass with no generated tokens.

- **Interface:** `model.system_one(state, questions, media=…)` (Transformers,
  `trust_remote_code=True`) — named questions over one state in one pass,
  plus `system_one_batch`. Question types: `noul`, `choice` (incl.
  `multiple`), `score` (2–10 levels), **and** `text` / `number` / `date`
  extraction with spans, ranged numeric estimates with intervals, and
  `box` / `boxes` visual grounding. The extraction + box heads matter: some
  `TYPE_TEXT` cases (copy a value already on the page) and the
  screenshot-fallback path can be served by the same model instead of a
  second system. Served natively by upstream vLLM as a structured-decisions
  model; GGUF builds exist for the family.
- **Context:** **128k tokens** (card notes answers get less reliable past
  ~64k of state) — the largest validated window among the small finalists,
  8× joey-1's limit. Satisfies R4 with headroom for long pages and history.
- **Latency (vendor-measured, to verify locally):** **5.7 ms per decision on
  an NVIDIA H100, 3.8 ms on an AMD MI325X**, independent of question count in
  the call. The predecessor d1-3B was reported at ~8 ms on an RTX 4090. Even
  if Apple Silicon MPS is an order of magnitude slower, it fits the < 50 ms
  compute target; the card's own quick-start explicitly selects `mps` on
  Apple hardware. No network round trip at all when run in-process.
- **Accuracy:** card claims best-in-class among open d1 models across 100+
  decision benchmarks, ahead of d1-3B at under two-thirds its size. Honest
  caveat: on public composite boards the d1 family trails the 27B class
  (d1-3B scored 48.57 on the Decision Index October public split, best under
  10B *that week*). Browser element-choice is a narrower skill than general
  decision benchmarks, but we must measure it ourselves (see Validation).
- **Multimodal:** text + pictures + video natively (R8). A screenshot can ride
  in `media` alongside the DOM state for canvas/image-only pages.
- **License — action required:** **LFM Open License v1.0** (`license: other`),
  *not* Apache-2.0. Acceptable for research and internal use; **commercial
  terms must be reviewed before any commercial deployment.** This is the
  pick's biggest non-technical risk and is recorded as a revisit trigger.
- **Cost of adoption:** `transformers>=5.19`, `torch`, `torchvision`,
  `pillow` (per model card). Heavy; therefore isolated behind an optional
  extra (see Decision) so the core loop stays light.

### Option C — Base Labs joey-1 (open weights, 2B) — close second

A 2B full fine-tune of `Qwen3.5-2B` with block-bidirectional attention on
full-attention layers (worth +3.8 Decision Index points at fixed data in the
mammoth-1 report) and a Brier calibration term with a trained
"I don't know" option. Sibling to elephant-1 (9B) and mammoth-1 (27B).

- **Accuracy:** strongest small-model *public* numbers of the three —
  **63.30 on Jev Decision Index 0.4 public split (best under 4B, #37/230),
  ahead of Jev 1.14's 60.70**, winning 4 of 5 areas head-to-head; 62.9 on a
  replica of Artificial Analysis's Decision Index.
- **Context:** **16,384 tokens** max; over-length requests are refused, not
  truncated. Passes R4 for typical pages but with far less headroom than
  d1-2B for long tables / histories.
- **Interface / serving:** `noul` / `choice` / `score` from one shared-prefix
  prefill; hosted on Baseten at **$0.01 / 1M input** (4× cheaper than Jev).
  **Self-hosting today requires one CUDA GPU**, the
  `pytorch/pytorch:2.8.0-cuda12.8` image, and a patched OneJev runtime
  (`setup.sh`). No documented Apple Silicon / MLX / llama.cpp path. On our
  M4 dev machine that means *hosted-only in practice*, which reintroduces
  the R6 privacy/latency problems it was meant to solve. The list cites a
  provider-advertised 20 ms p50; the model card itself publishes no latency
  table, so we treat that figure as unverified.
- **License:** cleanest of the finalists — MIT (Baseten modifications) +
  Apache-2.0 (Qwen base).

joey-1 would be the pick if we had an NVIDIA serving box or if its accuracy
edge proves decisive in our browser eval. It remains the designated fallback
open model.

### Comparison

| Attribute | Jev 1.14 (hosted) | d1-2B (selected) | joey-1 |
|---|---|---|---|
| Params / weights | undisclosed / closed | 1.92B / open | 2B / open |
| Context | 64k req / 32k q (262k opt-in) | 128k (~64k reliable) | 16k, refuse-not-truncate |
| Decide latency | 178 ms median measured (hosted) | 5.7 ms H100 vendor; MPS TBD | 20 ms p50 provider-advertised, unverified |
| Input | text + image | text + image + video | text + image |
| Extra heads | `box` | extract + estimate + `box`/`boxes` | — |
| Runs on M4 locally | No (API only) | Yes (MPS path in card) | No (CUDA self-host only) |
| Price | $0.042/M in | local compute only | $0.01/M hosted / local CUDA |
| License | proprietary API | LFM Open License v1.0 ⚠️ | MIT + Apache-2.0 ✓ |
| Public accuracy signal | 60.70 (JDI 0.4 public, per joey card) | best open-d1 (vendor board) | 63.30 JDI 0.4 public, best <4B |
| Wire | defines `/v1/systemone` | Python API; vLLM `/v1/systemone` | SystemOne request shape |

## What Mirage does differently — and where it fits

Mirage (Cactus Compute) is **not a System-One competitor; it is a perception
model with a small decision head**, and conflating the two would be a design
error. Understanding the split clarifies our architecture:

- **51.3M parameters in ~22 MB** (2-bit Cactus Quants; embeddings/routing at
  4-bit, HConvGLU bands in fp16), running **74 ms end to end on a Galaxy S26
  NPU, 107 ms on its CPU**. One shared encoder (512 px input, 16×16 feature
  grid, HConvGLU channel mixing at ~8× fewer weights than dense) feeds
  detection, segmentation-mask, and CTC OCR heads plus an 8-layer decoder
  that emits structured JSON: scene, objects (open-vocabulary names, boxes
  on a 0–1000 scale, attributes), masks, text regions with boxes + scores,
  relations (a small scene graph), and a short reasoning trace.
- **Its "decide" is form-filling over the image**: `vision.extract(img,
  {"sign": {"enum": [...]}})` scores a handful of caller-supplied labels.
  That is R1 in miniature, but it has no notion of a 100-element dynamic
  action table, multi-question speculative fan-out over a textual state, or
  calibrated operation/target probabilities. It cannot drive the browser
  loop alone.
- **Hard limits for desktop UI:** OCR is **Latin-script only** (306 chars);
  the 512 px / 256-cell encoder will lose small dense UI text that the DOM
  gives us exactly and for free; the blog's own VQA probe (Mirage JSON →
  Qwen3-0.6B) scored 31.3/100 and warns that any detail missing from
  Mirage's record is unavailable downstream.
- **What it proves for us:** (1) single-pass on-device structured perception
  at phone scale is real — our local-first direction (R6) is sound;
  (2) the right interface between perception and decision is a **structured
  record (JSON with boxes), not pixels-into-LLM**; (3) 74–107 ms is the
  *entire* perception budget on a phone, which validates keeping DOM
  snapshots (one browser call, exact text) as the primary perception path
  and reserving screenshot perception for pages where the DOM is silent.

**Adoption:** Mirage via `cactus-compute` (`cactus.Mirage().describe(...)`,
Apache-2.0 weights) as the **optional perception fallback** — its
objects/text/boxes JSON is normalised into the same indexed element table
the decide step already consumes, with boxes mapped to click coordinates
only when no DOM node backs the element. Primary perception stays the
jev-ultrafast DOM snapshot. Mirage's enum head is not used for action
selection.

## Decision

**Adopt Liquid AI d1-2B as the primary decide-step model, behind the
TypeSafe `/v1/systemone` contract, with hosted Jev 1.14 retained as (a) the
bootstrap/baseline for evaluation and (b) an explicit escalation path for
low-confidence steps.**

Concretely:

1. The agent loop is written once against the System-One request/response
   shape (`state` + named `questions` → typed `answers` with probabilities),
   exactly as in `jev_ultrafast/model.py`. Model output is validated the
   same way (choice ∈ offered ids, probabilities sum to ~1, finite,
   argmax-consistent) before any browser mutation; `DONE` still requires
   independent outcome verification.
2. `SYSTEMONE_BASE_URL` / `SYSTEMONE_MODEL` / `SYSTEMONE_API_KEY` select the
   backend: local d1-2B (in-process Transformers on MPS/CUDA, or a local
   vLLM `/v1/systemone` server) by default for development; TypeSafe Jev
   when the base URL points at `api.typesafe.ai`. Swapping models must
   never require loop changes.
3. Confidence gating: if the operation or target confidence falls below a
   tuned threshold, the step escalates — re-observe once, then optionally
   re-ask Jev hosted (never with vault secrets or credential field values
   in `state`; those steps are local-only by policy).
4. `TYPE_TEXT` keeps the jev-ultrafast pattern (small OpenAI-compatible
   text model, strict JSON validation, no hardcoded strings), except that
   values already present on the page are served by d1-2B's extraction
   types and vault values are injected from the local encrypted store,
   never generated.
5. Dependencies follow the split (see `pyproject.toml`): the core loop
   needs only `httpx[http2]`, `browser-use`, `pydantic`, and
   `cryptography`/`argon2-cffi` for the vault. The torch stack lives in the
   `local` extra; `cactus-compute` in the `perception` extra; neither is
   required to run against hosted Jev.

### Why d1-2B over the alternatives

- **vs Jev hosted:** local inference removes the network from the latency
  budget (5.7 ms vendor GPU figure vs 178 ms measured hosted median),
  removes per-step cost, and — decisively for this roadmap — keeps page
  state containing account data on-device. Jev remains one env var away as
  baseline/escalation, so we lose none of its proven accuracy while we
  validate the local model.
- **vs joey-1:** comparable size and strong public accuracy, but CUDA-only
  self-hosting makes it hosted-in-practice on our Apple Silicon dev
  machine, and its 16k context is 8× smaller. d1-2B's documented MPS path,
  128k context, vLLM-native serving, and extraction/box heads fit the
  browser loop better today. joey-1's cleaner license is noted; if the LFM
  license blocks our intended use, joey-1 (hosted, then CUDA) is the
  fallback and this ADR must be revised.

## Rejected options

| Option | Why rejected |
|---|---|
| Frontier LLM as decider (GPT/Claude/Gemini chat) | Seconds per step, generated-text parsing failures, highest cost — precisely what System-One replaces. Retained only as the `TYPE_TEXT` helper in miniature (fast small model, strict JSON). |
| 27B–31B open leaders (mammoth-1, Karpo-31B, basal-omni-ultra, Drex Max, REAX 1.3 ensemble ~115 GB) | Top composite accuracy, wrong operating point: datacentre GPUs, tens of GB, serving cost/latency incompatible with a millisecond local loop on consumer hardware. |
| Kev-4B (Qwen3.5 + pointer head) | Admirable Mac story (MLX, measured to 65k tokens, TypeSafe drop-in, within ~4 pts of Jev on new sources) — but its own example shows **495 ms latency on an Apple M5 for a 101-token ticket**, ~3× over budget before browser waits. Revisit if serving gets faster. |
| CLM-8B (contrastive dual encoder) | Clever candidate-caching (claims Jev parity at up to 9× lower latency, best when actions repeat) — but browser targets are *new every page*, erasing the cache advantage; default state truncation at 2,048 tokens; two-process serving (vLLM embedding server + API) is operational weight we don't need yet. |
| Laya / vegaml (33 ms on T4, 73k ctx v0.5) | Fast and genuinely local, with conformal abstention we like — but its own benchmarks concede hosted Jev leads zero-shot (89.5% vs 84.7% day-one), and the physics-engine readout is unproven for 100-way dynamic element choice. Candidate for a learned router later, not the primary decider. |
| Tiny encoders (Beveridge-Large-C 398M, Julia-1 144M, GLiNER2.5-Decide, Open Jev 150M) | Delightfully fast (Beveridge: ~50 ms MPS / 180 ms CPU per 3-question doc on M4 Pro) and CPU-friendly, but 4,096-token inputs (Beveridge), text-only, and near-chance on harder multi-step items (GLiNER per the list) fail R4/R8 for real pages. Possible future pre-filter, not the decider. |
| Other hosted Decisions APIs (OpenAI, Gemini Decision 1, Microsoft-Decision-1, kettei, etc.) | Capable (Gemini: 50–100 ms compute, 150–250 ms end to end, 256k ctx) but same R6 failure as Jev with *less* browser-loop evidence. If we pay for hosted at all, Jev is the one with a measured 7-second Flights run. |
| Mirage alone as decider | See above: excellent perception, insufficient decision interface; Latin-only OCR and 512 px grid are wrong for dense desktop UI as the primary path. Adopted as perception fallback instead. |
| Training our own System-One model now | Premature. No labelled browser-decision dataset yet; the loop + eval harness must exist first to generate one. Fine-tuning d1-2B/joey-1 on collected traces is the explicit next step after validation. |

## Consequences

**Positive:** millisecond-class local decisions with no per-step API cost;
credential-bearing state never has to leave the machine; one wire contract
with two backends de-risks both accuracy (Jev baseline) and lock-in; d1-2B's
extraction/box heads give the screenshot fallback and value-copy cases a
home in the same model.

**Negative / risks:** (1) **LFM Open License v1.0 must be cleared** for the
intended (eventually commercial?) use — unresolved at decision time.
(2) d1-2B's browser-specific accuracy is **unproven**; public composites
suggest a gap to 27B models and possibly to Jev. (3) Torch/Transformers is a
heavy, fast-moving dependency (`transformers>=5.19`, `trust_remote_code`);
pin and isolate it. (4) MPS latency is **assumed, not measured** — the
vendor's 5.7 ms is H100. (5) Two backends means threshold tuning per model;
probabilities are not interchangeable across models.

## Validation plan (gate for keeping this decision)

1. Reproduce the jev-ultrafast tasks as our eval harness: Google Flights
   (Zürich→London), Wikipedia (Gödel article), local hotel filter fixture —
   each with an **independent outcome checker**, never the model's `DONE`.
2. Run each task ≥3× per backend (d1-2B local vs Jev hosted), recording:
   task success, wall time, per-step decide p50/p95, token counts, and
   confidence calibration on executed steps.
3. **Keep d1-2B iff:** success within 1 failure of Jev across the suite
   **and** local decide p50 < 100 ms on the M4. Otherwise escalate per
   Revisit triggers (joey-1 hosted/CUDA, or Jev-primary with local-only
   credential steps) and revise this ADR with the measurements attached.
4. Log every (state, questions, answers, executed action, outcome) tuple
   locally from day one — this is the fine-tuning dataset that eventually
   closes any accuracy gap.

### Gate result — first measurement (2026-11-22, addendum)

The latency half of this gate ran for the first time against a real
model (everything before was offline doubles at ~0.05 ms or
published figures substituted arithmetically). Full record:
[d1-mps-smoke.md](d1-mps-smoke.md); raw data in
`d1-mps-smoke{,-compiled}.json` and `latency-measurement-d1.json`.

- **Setup:** d1-2B @ `a6bce77`, in-process Transformers on MPS
  (bf16; eager and `model.compile()`), Apple M4 MacBook Air 16 GB —
  the dev machine this gate names. Decide measured through the
  loop's own request builder/validator via
  `account_vault.local_decider.LocalD1Decider` and
  `scripts/latency_harness.py --decider local-d1`.
- **Loop decide p50: 8,056 ms (p95 22,268 ms, n=18 acted steps,
  compiled).** Isolated forward passes at matched sizes agree:
  2,689-token frozen hotel request 7,539 ms p50; 4,990-token state
  12,023 ms p50 (card: 753 ms on M5 Pro); the card's 64-token
  one-question example 161 ms p50 (card: 18.6 ms). Eager is
  1.5–2.7× slower still. Smallest decide observed anywhere at a
  loop-realistic size: ~2.0 s (one question, 1,087 tokens).
- **Verdict: the "local decide p50 < 100 ms on the M4" keep
  condition FAILS by ~80×.** The accuracy half (within 1 failure
  of Jev on Flights/Wikipedia/hotel) remains unrun — the only
  quality signal is anecdotal (catalog fixture completed 2/2 under
  its independent checker; hotel blocked 2/2 at the 0.5 confidence
  gate after six plausible actions) — but the gate is conjunctive,
  so **the keep condition is not met and this decision stands
  triggered for revision** per Revisit triggers below. This
  addendum records the evidence; selecting the successor
  configuration (joey-1 hosted/CUDA, Jev-primary with local-only
  credential steps, an MLX-served d1 variant, or a smaller/faster
  local model) is the follow-up decision, to be taken with the
  Tier R accuracy numbers the accuracy-ceiling doc prices.
- **What this does not overturn:** the loop design, the wire
  contract, and the local-first requirement (R6) are untouched —
  the harness now measures any candidate's decide stage for real
  (`--decider`), which is how the successor should be priced.
  Caveats (swap pressure on a 16 GB Air, thermals, vendor
  best-of-3 methodology) are recorded in the smoke doc §5; they
  are worth ~2–3×, not 80×.
- Consequences item (4) above ("MPS latency is assumed, not
  measured") and the comparison table's "MPS TBD" are superseded
  by this addendum; the original text is left as the decision-time
  record.

### Dependency cleanup (2026-11-22, addendum)

Decision 5's split named `browser-use`, `pydantic`, and
`cryptography`/`argon2-cffi` as core and `cactus-compute` as the
`perception` extra. None was ever imported — no vault or Mirage code
exists, and `browser.py` drives `browser-harness` directly — so all
four (and the `perception` extra) were dropped from `pyproject.toml`;
they return with the code that needs them. The `local` extra is
unchanged: `torch`/`transformers` back `local_decider.py`, and
`torchvision`/`pillow` are imported by the d1 checkpoint's own
`trust_remote_code` processors. The Decision 5 text above is left as
the decision-time record.

## Revisit triggers

- LFM license review concludes commercial use is not permitted → switch
  primary to joey-1 (or re-evaluate Kev/mammoth family) and revise this ADR.
- Validation gate above fails on accuracy or MPS latency.
- A ≤4B Apache-2.0 model with first-class MLX/llama.cpp serving beats
  d1-2B on our browser suite (watch: joey-1 serving ports, Kev speed work,
  d3-nano/edge).
- Hosted Jev (or Browser Use Cloud `bu-ultrafast`) pricing/latency changes
  materially, or on-device requirements are dropped by the product.

## Skills enabled for this work

From the available skill catalog, `bundled:python-env` was loaded and
followed (project-local `.venv` via `uv`, no system-interpreter installs).
`browseros-neo` (signed-in user browser) was considered and *not* needed:
all three references are public and were read directly. No other catalog
skill governs this task; browser driving itself will use `browser-use` /
`browser-harness` as libraries, per the reference implementation.

