# d1-2B MPS smoke: the first real decide measurement vs ADR-001

- **Date:** 2026-11-22
- **Measured tree:** branch `perf/d1-mps-smoke` on `main` @ `691fe6e`
  (the adapter + harness wiring in this change; no loop-behaviour
  changes)
- **Machine:** Apple M4 MacBook Air (Mac16,12), 16 GB unified memory,
  macOS 27.0, Python 3.12.16, torch 2.15.0, transformers 5.23.0
- **Model:** [LiquidAI/d1-2B](https://huggingface.co/LiquidAI/d1-2B)
  @ `a6bce774e6dde338dcc3036ceb5d91f78bf4c19e`, bf16 on `mps`
- **Raw data:** [d1-mps-smoke.json](d1-mps-smoke.json) (eager),
  [d1-mps-smoke-compiled.json](d1-mps-smoke-compiled.json)
  (`model.compile()`, the card's method),
  [latency-measurement-d1.json](latency-measurement-d1.json)
  (full loop, compiled)
- **Scripts:** [scripts/d1_smoke.py](../scripts/d1_smoke.py),
  [scripts/latency_harness.py](../scripts/latency_harness.py)
  `--decider local-d1`, adapter in
  [src/account_vault/local_decider.py](../src/account_vault/local_decider.py)

## Verdict first

**ADR-001's latency gate — local decide p50 < 100 ms on the M4 — is
FAILED, by roughly two orders of magnitude at loop-realistic sizes.**

- In the loop (latency harness, compiled d1-2B driving the static
  tasks): **decide p50 8,056 ms, p95 22,268 ms over 18 acted steps**
  — 80× the gate at p50. Decide is 99.9–100% of step time; the
  ~1–5 ms of local machinery the harness was built to measure is
  noise next to it.
- Isolated forward passes (smoke, compiled, n=20/case): a frozen
  hotel request (2,689 input tokens) costs **7,539 ms p50**; the
  catalog request (4,186 tokens) **11,017 ms**; a 4,990-token padded
  state **12,023 ms** — against the model card's **753 ms** for its
  5K-token column on an M5 Pro. Even the card's own 64-token
  one-question example takes **161 ms p50** here (card: 18.6 ms),
  already over the gate before any page state is involved.
- The accuracy half of the ADR-001 gate (within one failure of Jev
  on Flights/Wikipedia/hotel with independent checkers) is still
  unrun and this document says nothing about decision quality
  beyond two loop anecdotes (§4). But the gate is conjunctive
  ("keep d1-2B **iff** … **and** p50 < 100 ms"), so the keep
  condition is already not met. Per ADR-001's own rule this fires
  the Revisit triggers; the measurements are recorded in the ADR.
  Choosing the successor (joey-1 hosted/CUDA, Jev-primary, an MLX
  serving path, …) is follow-up work, not part of this record.

Nothing about the gap looks like a fixable misconfiguration at our
end: the same binary answers the card's quick-start correctly, no
MPS fallback warnings fired, compilation (the card's method) was
applied and helped only 2–3×, and the cheapest single loop-shaped
question we can ask still costs ~2 s (§3). Plausible machine-level
corrections (swap pressure, Air thermals — §5) are worth perhaps
2–3×, against a 65–80× miss.

## 1. What independent runs led us to expect (checked before running)

Model-card figures are vendor numbers, so the first step was to see
what non-Liquid runs on Apple Silicon look like. Summary (primary
pages opened 2026-11-22; vendor card included as the baseline):

| Source | Setup | Reported |
|---|---|---|
| [d1-2B card](https://huggingface.co/LiquidAI/d1-2B) (vendor) | M5 Pro MPS, warm after `model.compile()`, bf16, median of 20, best of 3 runs | 18.6 ms (1 question), 26.5 ms (3 questions), **753 ms (5K-token state)**, 45.2 ms (384 px picture) |
| [mlx-community/d1-2B-OptiQ-4bit](https://huggingface.co/mlx-community/d1-2B-OptiQ-4bit) | MLX (not MPS), M3 Max, Typed Decisions 400 cases × 5 questions, short states | **270 ms median per case** |
| Ivan Fioravanti, `mlx-d1-gestures` (via AGI Hunt summaries) | mlx-vlm, M5 Max, webcam gesture frames | 68–70 ms/frame (~14 decisions/s) |
| [Kev (jaredpalmer/kev) MLX PR](https://github.com/jaredpalmer/kev/pull/43) | Kev-4B, M5, 5 questions / ~270 tokens: torch-MPS vs MLX | **3,302 ms torch-MPS vs 721 ms MLX** (cached: 847 vs 136 ms) — "MPS has no Gated DeltaNet kernels, so the torch path runs reference code" |
| [Mornati home-lab bench](https://blog.mornati.net/jev-1-14-and-kev-4b-a-better-classifier-and-the-closest-open-model/) | Kev-4B bf16 via MLX server, **16 GB M4** (closest machine to ours) | 2.6–3 s p50 per decision once warm, 12.5 s p90 under swap, first request 143 s; "memory-bound models need patient benchmarks" |

Expectations going in, from those primaries:

1. **Small-state numbers do not transfer to loop-sized states.**
   Every sub-100 ms public figure is a tiny state (one short
   sentence, one image, ≤270 tokens). The card's own 5K column
   (753 ms) is the honest anchor for our ~2.7–5K-token requests —
   already 7.5× over the gate before our slower hardware enters.
2. **Torch-on-MPS is the slow path for this model class.** The Kev
   comparison (4.6× MLX-over-MPS on identical requests) and the
   OptiQ quant's existence both exist because the MPS eager path
   underperforms; d1's hybrid convolution/attention stack has no
   fused Metal kernels in torch.
3. **16 GB + swap dominates tails.** Mornati's p90 blow-up on the
   same memory size predicted wide distributions here (confirmed:
   §5).

MPS gotchas collected before running, and what happened with each:

- **Unsupported ops → `NotImplementedError`, or silent CPU fallback
  under `PYTORCH_ENABLE_MPS_FALLBACK=1`.** We ran *without* the
  fallback flag: nothing threw and zero fallback warnings were
  recorded, so the slowness below is genuine MPS execution, not a
  hidden CPU detour. (d1's attention code takes flash kernels only
  on CUDA/ROCm and SDPA elsewhere — read in `attention.py`.)
- **No float64 on MPS.** Verified locally before loading
  (`TypeError: Cannot convert a MPS Tensor to float64`); the card's
  load path uses bf16 off-CPU, so this never bit.
- **`torch.compile`/Inductor has no Triton-for-Metal** and is
  reported slower-than-eager for many MPS workloads. Here compiled
  was consistently *faster* than eager (2–3×, §3) — d1's runner
  compiles with `dynamic=True` + inductor freezing — but the first
  compiled call pays a 6.8–10.8 s trace, and each new prompt shape
  can re-trace (visible as spread in the compiled distributions).
- **Async timing.** MPS calls return before work completes; every
  timed call here is bracketed with `torch.mps.synchronize()`, in
  both the smoke script and `LocalD1Decider.choose()`. Unsynchronized
  timing would have understated latency by the whole queue depth.
- **`trust_remote_code=True` executes Hub Python.** All 26 `.py`
  files of the pinned revision were downloaded and read/scanned
  before first execution (no network/subprocess/eval surface; the
  only `importlib` use is a `find_spec("causal_conv1d")` probe).
  The revision is pinned in every command below.

## 2. Method

Three measurements, in increasing loop-realism. All: MPS-synchronized
`perf_counter_ns` around each `system_one` call, warmups excluded,
distributions computed with the harness's own `summarize()` so p50s
are comparable across documents.

1. **Smoke cases** (`scripts/d1_smoke.py`, 3 warmup + 20 timed per
   case, run eager and compiled in separate processes):
   `card-quickstart` (the card's example verbatim, 4 questions,
   189 tokens), `card-1-question` (same state, 1 question,
   64 tokens), `wire-hotel` / `wire-catalog` (frozen bodies from
   this port's `build_choose_request` on the harness pages,
   translated by `local_decider.translate_request`), and `wire-5k`
   (hotel body padded against `usage.input_tokens` to 4,990 tokens —
   the card's 5K column at matched size; padding prices prefill,
   which is what the gate cares about).
2. **Decomposition probe** (throwaway, compiled, 2 warmup + 5 timed):
   the hotel body's questions asked singly and together, plus
   `set_size` variants — to attribute cost before blaming the model.
3. **Full loop** (`latency_harness.py --decider local-d1
   --d1-compile`, static backend, 2 runs/config, 0 warmup): the
   real decider drives `Agent` end to end; each config keeps its
   independent `done_when` checker and `MappingTextProvider`, so
   `done` still means checker-verified. Decide latency here is the
   gate quantity itself, no longer substituted arithmetically.

The adapter (`LocalD1Decider`) reuses `build_choose_request` and
`parse_choose_response` unchanged: a local answer passes the same
`validate_choice` strictness as a hosted one before anything
executes (pinned in `tests/test_local_decider.py` with a fake model;
CI never downloads weights).

## 3. Results

### Smoke: isolated forward passes (ms, n=20 per case)

| Case | Tokens | Eager p50 | Eager p95 | Compiled p50 | Compiled p95 | Card (M5 Pro) |
|---|---:|---:|---:|---:|---:|---:|
| card-1-question | 64 | 264.2 | 319.1 | 160.7 | 205.2 | 18.6 (1 question) |
| card-quickstart | 189 | 729.7 | 915.8 | 382.3 | 677.2 | 26.5 (3 questions) |
| wire-hotel | 2,689 | 10,999.1 | 13,414.4 | 7,538.8 | 10,113.5 | — |
| wire-catalog | 4,186 | 20,257.3 | 23,491.6 | 11,016.9 | 12,561.0 | — |
| wire-5k | 4,990 | 18,198.2 | 22,040.8 | 12,022.6 | 13,123.2 | 753 (5K state) |

Load: 187 s cold (includes the one-time 3.8 GB unauthenticated
download), ~10 s warm. First compiled call (graph trace): 6.8 s.
MPS memory: 3.84 GB current / 6.86 GB driver-allocated. All answers
passed envelope checks; the quick-start reproduces the card's
printed answers (`billing`, `['billing', 'delivery']`, medium).

Readings:

- **We are 8.6× the card on its smallest case and 16× on its 5K
  case** (compiled vs compiled). Hardware explains part — base M4
  Air (passively cooled, ~120 GB/s) vs M5 Pro, best-of-3-runs
  vendor methodology vs our single full run — but the card's
  question shapes are also far lighter than the loop's (below).
- **Eager is 1.5–2.7× slower than compiled** here, contra the usual
  MPS-compile warning; compiled is the right default for any future
  local-decider work, at the price of trace stalls on new shapes.
- **Every case fails the 100 ms gate at p50, including the toy
  ones.** The minimum single compiled call observed anywhere was
  143.6 ms (64 tokens, 1 question).

### Probe: where the wire-case time goes (compiled, hotel body)

| Ask | Tokens | p50 (ms, n=5) |
|---|---:|---:|
| operation only | 1,087 | 2,026.5 |
| type_text_target only | 1,179 | 2,363.4 |
| select_target only | 1,195 | 2,411.7 |
| click_target only | 1,277 | 2,526.7 |
| all 4, default set packing | 2,689 | 5,490.1 |
| all 4, `set_size=64` | 2,689 | 6,500.2 |
| all 4, `set_size=1` (state re-read per question) | 4,738 | 11,455.5 |

- **One loop-shaped question alone is ~2–2.5 s.** The speculative
  fan-out is not the problem: set packing already shares the state
  (default beats `set_size=1` by 2×), and a bigger set does not
  help. Cost tracks total prompt tokens at roughly 2 ms/token.
- **The prompt is mostly repeated rules.** The state JSON is only
  1,624 chars; each question carries 1.5–1.9K chars of instructions
  (`NEXT_ACTION`/`TARGET`) because the wire shape repeats the rule
  text per question. That repetition is inherited prompt parity
  with upstream, priced here for the first time: it is a real
  optimization lever for any future local backend (shorter/shared
  instructions), but even zeroing it entirely would leave the
  state+element-table prefill at seconds scale.
- Probe vs smoke for the same body: 5.49 s vs 7.54 s p50 — sizable
  run-to-run drift (§5), same order of magnitude.

### Full loop: the gate quantity (harness, compiled)

| Config | Status (2 runs) | Acted steps | Decide p50 (ms) | Decide p95 (ms) | Decide share |
|---|---|---:|---:|---:|---:|
| static-hotel | blocked, blocked | 12 | 7,688.1 | 12,426.2 | 99.9% |
| static-catalog | done, done | 6 | 15,039.1 | 22,319.4 | 100.0% |
| **All acted steps** | — | **18** | **8,056.1** | **22,268.1** | — |

Run totals (steps only): hotel 63–68 s per run without finishing;
catalog 29 s and 64 s for two identical 3-action completions —
the spread is decide-time variance, not different behaviour.
Against the machinery baseline in
[latency-results.md](latency-results.md) (33–34 ms CDP step, of
which decide-double ≈0.05 ms), substituting any published decide
figure is now unnecessary: with d1-2B local, a step *is* its decide
call, and act+settle rounds to 0.0%.

## 4. Behaviour anecdotes (not an accuracy measurement)

Two runs per config prove nothing about quality, but two loop
facts are worth recording because they exercise machinery no double
ever triggered:

- **Catalog completed under the independent checker, twice**, in 3
  actions each (`SELECT Design` → `CLICK Free cancellation` →
  `CLICK View Casa Flora`) — a shorter path than the heuristic's 8.
- **Hotel blocked on the confidence gate, twice, identically:**
  after six plausible actions (fills, promo select, free-cancellation
  check, Search ×2), target confidence fell below the 0.5 floor
  after re-observing and nothing further executed. The
  both-confidences gate landed in `9991a6f` fired on a real model
  for the first time, and `DONE` was never taken on the model's
  word in either config.

## 5. Limits — read the numbers with these

- **Memory pressure:** swap sat at 6.9–8.0 GB used of 8–9 GB total
  throughout, most of it pre-existing (the machine's everyday apps;
  7.2 GB remained swapped after the model exited). This is the dev
  machine as actually used, not a quiesced benchmark rig. A cleaner
  machine would be faster; the identical-path catalog runs (29 s vs
  64 s) bound that effect at roughly 2×, not 80×.
- **Thermals:** a fanless Air under ~20-minute sustained MPS loads
  throttles; distributions widen as runs progress (compiled
  quick-start p95 677 ms vs p50 382 ms). The card's M5 Pro figures
  come from actively cooled hardware and best-of-3 selection.
- **Small loop n:** 2 runs/config, 18 acted steps — enough to
  falsify a 100 ms gate by 80×, not enough to quote p95 precisely.
  The smoke's n=20/case carries the distributional weight.
- **Static backend only** for the loop runs (decide dominates so
  completely that CDP would change nothing at these magnitudes);
  screenshots off, as everywhere in this project.
- **One derived field in the eager JSON is unit-wrong:**
  `process_max_rss_mb: 696928.0` in `d1-mps-smoke.json` used the
  Linux KiB conversion on macOS's byte-valued `ru_maxrss`
  (unit-corrected: ≈714 MB). The script is fixed; the file is left
  as measured rather than hand-edited, and no conclusion uses it.
- **Prompt-shape caveat:** instructions/criteria dicts are
  JSON-stringified for d1's schema (lossless, pinned by test). A
  hand-tuned d1-native phrasing might differ in tokens and quality;
  what was measured is the loop's actual ask, which is the gate's
  subject.

## 6. Reproduce

```bash
uv sync --locked --dev --extra local   # one-time; model download ~3.8 GB on first load
uv run python scripts/d1_smoke.py --revision a6bce774e6dde338dcc3036ceb5d91f78bf4c19e
uv run python scripts/d1_smoke.py --compile --revision a6bce774e6dde338dcc3036ceb5d91f78bf4c19e
uv run python scripts/latency_harness.py --decider local-d1 --d1-compile \
  --d1-revision a6bce774e6dde338dcc3036ceb5d91f78bf4c19e \
  --runs 2 --warmup 0 --micro-n 10 --out docs/latency-measurement-d1.json
```

Wall-clock warning from §1's evidence, now first-hand: budget
~25 min for the eager smoke, ~15 min compiled, ~5 min for the
harness at these run counts — on this machine, that patience *is*
the finding.
