# The decide-accuracy ceiling — #67 / #248 assessed

- **Date:** 2026-11-22. Local HEAD `eaabc44` (branch `main` at assessment time).
- **Upstream:** [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast).
  Anchor threads: [#67](https://github.com/browser-use/jev-ultrafast/issues/67)
  “Jev ultra fast Can't complete simple task” and
  [#248](https://github.com/browser-use/jev-ultrafast/issues/248)
  “Community benchmark: how accurate is the decision model at each step?
  (Mind2Web replay)”.
- **Question:** the issue analysis
  ([jev-ultrafast-issue-analysis.md](jev-ultrafast-issue-analysis.md)) puts
  #67/#248 at the top of the queue as “the decide-accuracy ceiling”. What is
  actually being claimed, does this port inherit it, which local changes
  would move it, and what would a real test here cost?
- **Method:** both threads read in full (body + every comment + timeline),
  every link they contain followed to its primary page, the whole upstream
  tracker searched for accuracy/wrong-pick/benchmark items, independent
  Mind2Web measurements re-opened at their primaries, and the local decide
  path audited file-by-file. §0 states the evidence rules; §7 + Appendix A
  give the source map and the full unresolved register. Nothing in this
  document is a new measurement — **no real decide model was run for it**
  (see §3.5).

## Contents

0. [Scope and evidence rules](#0-scope-and-evidence-rules)
1. [TL;DR — four verdicts](#1-tldr--four-verdicts)
2. [What the ceiling actually is (Q1)](#2-what-the-ceiling-actually-is-q1)
3. [Does this port inherit it? (Q2)](#3-does-this-port-inherit-it-q2)
4. [Which changes would move it? (Q3)](#4-which-changes-would-move-it-q3)
5. [What a real test here would cost (Q4)](#5-what-a-real-test-here-would-cost-q4)
6. [Claims to avoid](#6-claims-to-avoid)
7. [Method, limits, and corrections to local docs](#7-method-limits-and-corrections-to-local-docs)
- [Appendix A. Source map](#appendix-a-source-map)
- [Appendix B. Tracker inventory (accuracy-relevant)](#appendix-b-tracker-inventory-accuracy-relevant)
- [Appendix C. Unresolved register](#appendix-c-unresolved-register)

## 0. Scope and evidence rules

Read this before quoting any number below.

- **Verified** means the cited primary body (issue/PR thread, bench README,
  paper, model card, local source file) was opened and read. **Inferred**
  means the mechanism was read but no live/real-model run backs the
  magnitude. Search hits were used only as pointers.
- Tracker items are classed **(a)** measured benchmark, **(b)** concrete
  wrong-pick repro, **(c)** proposal without measurement, **(d)** noise.
  Only (a)/(b) carry evidential weight; (c) is recorded so it is not
  re-triaged as evidence later.
- **STEP** = offline next-step replay accuracy (right operation *and*
  right element against a recorded gold step, with true history). **TASK**
  = live end-to-end completion judged by an independent checker, never by
  the model's own `DONE`. **COST** = calls/tokens/wall-clock or safety
  (bounded spend, refused mutations). A change can move one without
  moving the others; most confusion in the threads comes from treating
  STEP as TASK.
- **Within-harness-only rule:** step percentages are comparable only
  *inside* one harness (same steps, same candidate construction, same
  scoring). Cross-harness comparisons in §2.4 are context bands, not
  rankings. Candidate construction alone (10 guaranteed-gold vs 50 sampled
  vs top-50 ranker at ~85–89% recall) moves the number by construction.
- The #248 bench builder is **vendor-affiliated** (“I work on Decido”,
  disclosed in the issue). Its numbers are *reported consistently* across
  issue + bench README + `results/scores.txt` + write-up; they were **not
  independently reproduced** here (bench code/data not executed; see
  Appendix C).
- Two conflicts inside the research bundle were resolved or preserved, not
  smoothed over:
  1. *#248 candidate count* — one researcher described the bench as the
     “full live element table up to ~250”. Direct re-read of the bench
     README for this document disproves that for the bench: `--k 50`,
     “the recorded element and up to 49 other elements sampled from the
     same page, in page order”. **k=50 is the bench figure used
     throughout.** (`~250` is this port's *live* snapshot cap,
     `offered.splice(250)` in `src/account_vault/snapshot.js:283-285` —
     a different number in a different harness.)
  2. *Decido bench “opened” vs “not opened”* — the #67/#248 researcher
     opened the bench README, `scores.txt`, and the write-up; the
     tracker/external researchers cited #248 from the issue body only.
     Where method detail matters (§2.2) this document follows the opened
     README/write-up, corroborated by a direct `gh api` re-read of the
     README (500/split, `--k 50`, quirk note) at writing time.
- Local `file:line` citations are at HEAD `eaabc44`. Upstream-side
  byte-parity claims (prompt identity) rest on local `DEVIATION` comments
  plus the drift review's AST comparison as a *secondary* source — the
  upstream bodies at `cf3c406` were not re-opened for this audit
  (Appendix C). Local-side text and behaviour *are* verified.
- The site-knowledge MCP database was inspected: tables `sites` (8 rows),
  `selectors` (32 rows, all `strategy`/`value` NULL stubs),
  `action_hints` (0 rows). It holds **no accuracy finding** and is cited
  nowhere below as evidence.

## 1. TL;DR — four verdicts

1. **The ceiling is a replay number, not a task-success rate (Q1).** The
   only quantified claim is #248: Jev 1.14 scores **59.9% STEP** (operation
   *and* element both right) replaying 1,500 Mind2Web steps through
   unmodified `choose()` with true history and 50 candidates/step
   (element 62.3%, operation 86.8%; quirk-excluded 61.5%, §2.2). The same
   authors' live run erases the offline gap (Decido system 74% vs Jev 1.14
   75% on 100 tasks), so 59.9% must never be read as “the agent completes
   59.9% of tasks”. #67 contains **no accuracy metric at all** — its
   proposed fix was disconfirmed in-thread (0/20 on both main and fork).
   The concrete looping repro is #247, not #67 (§2.3).
2. **This port inherits the decision *shape* intact; “inherits the number
   unchanged” is unprovable (Q2).** `validate_choice`, the one-request
   speculative fan-out, the 6,000-char flattened text, the 250-element
   cap, and the 10-step history window are faithful copies (§3.1). Local
   divergences widen the offered set, add one new input class (scroll
   state), and bound the cost of wrong picks (§3.2–§3.4) — none changes
   first-pick ranking. The one lever that could move STEP, the default
   swap from hosted Jev to local `d1-2B`, has **never been run**: every
   green test uses offline doubles (§3.5). Until the ADR-001 gate runs,
   any claim that local changes raise (or preserve) step accuracy is
   unsupported.
3. **What would move it (Q3):** already-landed fixes move TASK
   (unblocking) or COST, not STEP. The credible STEP/TASK levers are all
   unbuilt or unmeasured here: structured page text (#149, proposal only),
   a completed confidence→escalation path (currently off and
   half-implemented), task fine-tuning (strongest external evidence, not
   local evidence), and the model swap itself (direction unknown). The
   planner branch is measured and does *not* establish improvement
   (§4). Budget effort on the offered set, stopping behaviour, and
   verification — not on operation vocabulary.
4. **A real test is cheap in dollars, expensive in engineering (Q4).**
   Hosted-Jev replay of 1,500 steps costs **~$0.33** at the verified
   $0.042/Mtok input price (5.3k tok/step anchor; sensitivity $0.13–$0.95)
   and <1 h wall-clock. The real price is **~1–2 engineering weeks**:
   fork-adapting the #248 bench to this port's deviated snapshot/prompts
   (4–8 days) plus live-task checkers (3–6 days). The cheapest
   decision-relevant fact comes first and costs $0: `d1-2B`'s own card
   reports **753 ms MPS at 5K-token state** (M5 Pro) against ADR-001's
   **<100 ms p50 on M4** gate — smoke-test that before building anything
   (§5.4).

## 2. What the ceiling actually is (Q1)

### 2.1 The mechanism being measured

Both upstream and this port decide each step with **one forward pass, no
reasoning text**: the model is shown a numbered element table plus page
text and recent history, and returns typed answers — an operation
(`CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP/DOWN`, `WAIT`, `DONE`,
`BLOCKED`) and an element index. One request carries the operation
question plus one speculative target question per operation type; only the
selected operation's target head executes. Locally this is
`build_choose_request` (`src/account_vault/model.py:175-233`),
`parse_choose_response` (`src/account_vault/model.py:236-293`, head
selection at `model.py:260-262`), `action_space`
(`src/account_vault/model.py:102-141`), and prompts `NEXT_ACTION` /
`TARGET` / `TEXT_VALUE` (`src/account_vault/questions.py:7-39`). The
“ceiling” claim is therefore a claim about **how often that single pass
picks the gold operation+element**, and about what happens live when it
does not — two different questions that the threads keep separate only
if read carefully.

Three structural facts bound any such number before model quality enters:

- **The offered set is the ceiling's ceiling.** If the gold element is
  never enumerated (perceive-enumeration failures) or is offered but
  unhittable, no ranker can pick it. Upstream measured one unguarded run
  choosing an unhittable control **118 times** until the 120-call budget
  ran out ([PR #137](https://github.com/browser-use/jev-ultrafast/pull/137),
  (a)+(b)); offered controls on 25 public pages fell 847→760 once
  unhittable controls were filtered.
- **Stopping has no replay gold.** Mind2Web trajectories contain only
  `CLICK`/`TYPE`/`SELECT` — “all 6,454 Mind2Web steps are CLICK,
  TYPE_TEXT or SELECT, because an offline trajectory records one action
  per step and simply stops”, with no observation after the last action,
  so no task-complete state exists to learn or score `DONE`/`BLOCKED`
  from ([Visual-Jev status](https://github.com/guanxuyu-sv/visual-jev-browser-use/blob/HEAD/docs/status.md),
  verified). Consequence there: 105/106 successful runs acted until the
  budget was exhausted. Any replay score is therefore silent on the two
  failure modes the live threads actually report — premature stops and
  loops.
- **Typed values are a second model.** `TYPE_TEXT` delegates text
  generation to a separate helper (`field_text`,
  `src/account_vault/model.py:390-433`). Replay through `choose()` alone
  scores *which field*, never *what text*. The TinyFish field report's
  decide→type handoff failure (correct field, wrong text) is invisible
  to STEP by construction (§2.4).

### 2.2 #248 in full — the 59.9%

[Issue #248](https://github.com/browser-use/jev-ultrafast/issues/248)
(gauravapiscean, 2026-11-17, OPEN, **0 comments**, no maintainer response)
is a one-post benchmark announcement. All method detail lives in its two
links, both opened: the bench repo
[decido-ai/browser-agent-decision-bench](https://github.com/decido-ai/browser-agent-decision-bench)
(README + `results/scores.txt` + `compare.txt`) and the write-up
[getdecido.ai/research/decido-1-web](https://getdecido.ai/research/decido-1-web)
(Gaurav Agarwal, Nov 2026). Disclosure in the issue body: “I work on
Decido. Live results are closer, 74% vs 75% on 100 tasks.”

**Design (verified from README, corroborated by direct re-read):**

- Source: Multimodal-Mind2Web
  ([osunlp/Multimodal-Mind2Web](https://huggingface.co/datasets/osunlp/Multimodal-Mind2Web))
  test sets, **500 steps per set × 3** (new tasks / new websites / new
  domains) = **1,500 steps**.
- State: recorded HTML converted by a Python port of `snapshot.js` into
  the element table (roles/labels/values/select options) + visible text;
  **history = the recorded true earlier steps** (the model never lives
  with its own mistakes); goal = task description.
- Request: body built by **unmodified `jev_ultrafast.model.choose()`**
  with only the endpoint URL swapped (harness at upstream commit
  `8251e27` per the write-up; commit body not inspected — Appendix C).
  Works against any `/v1/systemone` endpoint. Jev called via the TypeSafe
  API, Nov 2026.
- Candidates: **gold element + up to 49 sampled others (`--k 50`), in
  page order** “so position gives nothing away”. This is *easier* than
  live (gold guaranteed present, ≤50 options) and *harder* than fixed-10
  harnesses (§2.4). Do not confuse it with the live snapshot cap (§0).
- Scoring: a step counts **only if operation *and* element are both
  right**; element-only and operation-only also reported. Failed
  requests count wrong (same 6 very-long requests failed for every
  model, +1 more for Jev; write-up: Decido 6 over its 32,768-token
  limit, Jev 7). Re-scorable without an API key (`score`/`compare`,
  mock-endpoint tests) — the harness is reusable (§5.2).

**Results (all-1,500 step / element / operation; per-split step in
parentheses):**

| Model | Step | Element | Operation | Per-split step |
|---|---|---|---|---|
| Decido-1 web-trained (26B MoE, 4B active) | **78.6** | 80.7 | 94.1 | 79.4 / 79.6 / 76.8 |
| Jev 1.14 | **59.9** | 62.3 | 86.8 | 62.4 / 59.0 / 58.2 |
| Jev 1.13 | 54.4 | 56.9 | 80.0 | 55.8 / 52.0 / 55.4 |
| Decido-1 released (no web training) | 42.5 | 44.8 | 65.2 | — |

Paired bootstrap 95% CI: Decido-web − Jev 1.14 = **+18.7 [+16.2, +21.3]**
(all) / +19.6 [+16.4, +22.8] (unseen); − Jev 1.13 = +24.2 [+21.7, +26.9].
Head-to-head: only-Decido-right 356 vs only-Jev-1.14-right 75. The
no-web-training row (42.5% vs 78.6% web-trained, same harness) is the
bench's internal evidence that task training, not architecture alone,
drives most of the gap — see fine-tuning in §4.

**Disclosed builder quirk (read the headline with this):** in 56/1,500
steps the gold element is non-interactive and unnamed (or >80 chars)
while the builder drops such elements from sampled alternatives — a
learnable cue. Excluding them (n=1,444): Decido-web 79.1 vs **Jev 1.14
61.5** (+17.6 [+15.0, +20.2]), Jev 1.13 55.9. **The headline 59.9%
includes the quirk steps; 61.5% is the quirk-excluded Jev 1.14 figure.**
Local docs quote 59.9% without either caveat (§7.2).

**Sensitivities (write-up, Decido-web only — do not transfer to Jev):**
candidate count 85.0% @25 → 77.3% @50 → 74.7% @100 (300 steps); shuffled
candidate order 71.0% with the action changing on 20% of steps, called
“the model's main weakness”. Typical misses are “near-ties, such as two
filters with the same label”; **no fuller error taxonomy is given**, so
no share of the 40.1% miss rate can be attributed to any specific cause
(multi-update tracking included) from this source.

**Stated limitations (bench README, verbatim substance):** offline
replay (true history + 50 candidates vs live own-history + all elements
+ must decide when to stop); “Mind2Web has no wait/scroll/done labels,
so those operations are never correct here”; typed text unscored
offline; Decido-web training disjointness disclosed (no shared
tasks/near-duplicate goals, no unseen-split websites; “new tasks”
websites seen by design); **Jev training data not public**, so
contamination can neither be ruled in nor out for the baseline.

**The live falsifier (write-up Table 3).** The same authors ran 100 new
live tasks (mostly Wikipedia/GitHub/docs navigation), 2 runs each, with
frozen systems and a frozen final-page rule check: **Decido system
74.0% vs Jev 1.14 75.0% vs Jev 1.13 72.0%** (Decido−Jev1.14 = −1.0,
95% CI −9.0 to +7.0) — “the 19-point offline lead does not carry over
yet”. Two cautions before over-reading parity either: live compares
*systems*, not pickers (Decido system = web-trained picker + released
Decido-1 stop-check + 3 rules vs Jev alone, with shared page-read
tweaks and Qwen3-4B typing for all), and live failures were dominated
by **premature stops and repeated no-ops** — precisely the behaviours
replay cannot score. The correct takeaway is narrow and strong:
**replay STEP does not predict live TASK ordering, let alone level.**

### 2.3 #67 in full — no metric, disconfirmed fix; looping is #247

[Issue #67](https://github.com/browser-use/jev-ultrafast/issues/67)
(yonikremer, 2026-09-20, OPEN, **8 comments**, updated 2026-09-21) is a
qualitative simple-task failure report: “there isn't one place where it
goes wrong, it goes wrong in many steps” on a bus-journey-time task
(address X → place Y by name, next Sunday). Verified contents:

- **No accuracy claim exists in the thread:** no percentage, dataset,
  harness, scoring rule, step/element/operation breakdown, named wrong
  pick, or loop trace. The only quantification is the author's fork
  succeeding “15 times in a row” on one personal task and “much higher”
  success “on the few tasks I asked it to do”, with the explicit caveat
  “I didn't run an entire benchmark” (2026-09-20). Neither the 15 runs
  nor the task list is verifiable (no trace/log; fork code not
  line-inspected — Appendix C).
- **Proposed fix:** a fork
  ([yonikremer/jev-ultrafast](https://github.com/yonikremer/jev-ultrafast),
  single commit `57de39b`) routing via OpenRouter (`typesafe/jev-1.13`),
  adding `verify_outcome()` / `classify_goal()` / `answer_goal()`,
  verify/retry (≤2 retries, replay good steps + “step 2 was wrong,
  avoid it”), dead-target stop after 2 tries, prompt changes, ~15 tests;
  later, a step-count-rank + subtask-split proposal. Exact prompt
  strings/confidence logic are the author's summaries, unverified
  against code.
- **Maintainer disconfirmation (the load-bearing comment):** gregpr07
  (MEMBER, 2026-09-21): “ran 20 BU Bench tasks: **0/20 verified on
  both, ~5.7x inference cost on the fork**. captcha/browser issues
  muddy it though.” I.e. the thread's own only head-to-head measurement
  shows verify/retry did **not** beat main, at ~5.7× cost, confounded by
  captcha/browser issues. BU Bench itself (task list, “verified”
  definition, cost method) is undefined beyond this line + an unread
  image (Appendix C) — cite it as a disconfirmation signal, not a
  benchmark. No later resolution; the issue remains open.
- **Timeline links followed:** referenced commit `a8b5509` and
  [PR #74](https://github.com/browser-use/jev-ultrafast/pull/74)
  (“Fix portability…”) touch #67 only as the `MAX_STEPS × 2` budget
  doc/test — **not an accuracy fix**. Cross-references:
  [#145](https://github.com/browser-use/jev-ultrafast/issues/145)
  (jev-browse speed/cost, “same pass rate” unquantified, N=3) and
  [#247](https://github.com/browser-use/jev-ultrafast/issues/247).

**#247 is the looping repro.** [#247](https://github.com/browser-use/jev-ultrafast/issues/247)
(oleg-koval, OPEN, 1 comment): goal “five cheapest coffee products” on
roazon.nl at `cf3c406`; the agent “repeatedly chose sort beans → the
price index → Coffees… **44 browser actions** without returning product
names, prices, or a final URL”, with the honest caveat “I did not save
screenshots or a structured run trace, so the exact page state and
underlying cause remain unknown.” Its sole comment (gauravapiscean —
also #248's author) proposes (url+label) cycle detection
(drop-at-2/block-at-3) and notes “loops and premature stops were the
two main failure modes” live. Local drift-review text attributing
“looping/wrong choices” to #67 conflates the two (§7.2). The other
decide-accuracy “repro”,
[#129](https://github.com/browser-use/jev-ultrafast/issues/129) (MDN
wrong-waypoint), is **468 chars of task JSON with no observed trace at
all** — (b)-thin; its value is as a pinned eval task
([PR #151](https://github.com/browser-use/jev-ultrafast/pull/151) adds a
DONE-independent URL-path verifier), not as failure evidence.

### 2.4 What everyone else measured — and why the numbers don't line up

The tracker-wide search (57 issues + 182 PRs listed; keyword searches
for accuracy/benchmark/Mind2Web/planner/confidence/loop/`choose()`/
`DONE`/`BLOCKED` — Appendix B) found a stark negative result: **the
only STEP numbers in the entire upstream tracker are #248 and a
branch-only planner study**, plus small fixture A/Bs. Everything else
with a percentage is either asserted (#187's “~70%→72–80%”, explicitly
theoretical, (c)) or measures something else (speed, influence,
fixture passes). Independent outside measurements, each re-opened at
its primary:

**Mind2Web itself defines the scale.** ([arXiv 2306.06070](https://arxiv.org/html/2306.06070v3),
[dataset card](https://huggingface.co/datasets/osunlp/Mind2Web).) 2,350
tasks / 137 websites / 31 domains; splits train 1,009, Cross-Task 252,
Cross-Website 177, Cross-Domain 912 (tasks, not steps); operations
`CLICK`/`TYPE`/`SELECT` only; offline replay with gold history;
Element Accuracy + Operation F1 (*token F1 including typed value*,
macro-averaged over tasks) + Step SR (both) + task SR (all steps).
Candidate generation: a fine-tuned DeBERTa ranker pre-filters to a
top-50 pool (Recall@50 85.3–88.9%), predictors see ≤5/group (GPT-4:
top-10, 50 tasks/split). **Any percentage quoted without its candidate
construction is meaningless.**

| Source (harness) | Model | Step | Element / Op | Method notes |
|---|---|---|---|---|
| #248 bench (k=50 sampled, production `choose()`, n=1,500) | Jev 1.14 | **59.9** (61.5 quirk-excl.) | 62.3 / 86.8 (acc., value ignored) | Offline replay, true history; op excludes value |
| kev table (fixed-10 guaranteed-gold, simplified request, n=1,000) | Hosted Jev | **43.5** | 47.9 / 82.7 | Offline replay; split mix + Jev version unpinned |
| MindAct (top-50 ranker pool, macro) | Flan-T5-XL 3B fine-tuned | 52.0 / 38.9 / 39.6 | Ele 55.1/42.0/42.1; OpF1 75.7/65.2/66.5 | Cross-Task/Website/Domain; task SR 5.2/5.1/2.9% |
| MindAct ICL (top-10, 50 tasks/split) | GPT-4 | 36.2 / 30.1 / 26.4 | — | Text-only, 3 demos |
| AWM (GPT-4, filtered HTML) | AWM-offline GPT-4 | 45.1 / 33.7 / 32.6 | Ele 50.6 (Task split) | Same Mind2Web metrics; gain explained by +5–9 Ele pts |
| SeeAct-Oracle (screenshot plan + *human* grounding) | GPT-4V | 61.9 / 65.0 / 62.1 | — | Planning ceiling, **not a picker**; live task SR 51.1% oracle vs GPT-4 13.3% |

Sources: [kev-browser-use](https://www.arbazsiddiqui.me/kev-browser-use/)
(2026-09-23; also Kev-9B 32.5%, kev-0.6b 32.2%, Laya zero-shot 3.8% ≈
random 3.6% — see §4 on fine-tuning);
[Mind2Web Table 2](https://arxiv.org/html/2306.06070v3);
[AWM](https://arxiv.org/html/2409.07429v1);
[SeeAct](https://arxiv.org/html/2401.01614v2) (automatic SeeAct-Choice
beats text GPT-4 by +6.8/+5.7/+12.3 pts; absolute automatics not fully
extractable — Appendix C; oracle gap 20–30 pts = grounding bottleneck).

**Comparability verdicts (adversarially checked):**

- **59.9% (#248) vs 43.5% (kev): apples-to-oranges.** They share only
  the label “step = op+element”. They differ in n (1,500 vs 1,000),
  candidates (50 sampled incl. gold vs fixed 10 guaranteed-gold),
  request shape (production `choose()` with speculative heads vs
  simplified goal+10-list), split sampling (500/split declared vs
  unstated mix), Jev version pinning, and operation scoring details.
  Neither shows Jev “improving” or “regressing” against the other.
- **59.9% vs MindAct/SeeAct:** #248 sits above fine-tuned Flan-T5-XL on
  unseen splits (38.9/39.6%) and below the SeeAct-Oracle *planning*
  ceiling (61.9–65.0% with human grounding). In context, **43–60% STEP
  is normal-to-strong for Mind2Web**, not pathologically low: even
  frontier VLM+reasoning without oracle grounding stays in the ~40%
  class, and offline task SR collapses to 3–5% because one wrong step
  fails the task. A 2025–26 leaderboard SOTA was not opened; the band
  rests on 2023–24 primaries (Appendix C).
- **JDI is not browser accuracy.** Jev 1.14's 60.70 on the Jev Decision
  Index v0.4 *public general-decisions* split (joey-1 63.30, per the
  [joey-1 card](https://huggingface.co/baselabs/joey-1)) and d1-2B's
  51.1 on JDI v0.3 ([d1-2B card](https://huggingface.co/LiquidAI/d1-2B),
  older version, not comparable to v0.4) measure general one-pass
  decisions. Their nearness to 59.9% is a **numeric coincidence**. The
  [TypeSafe model docs](https://docs.typesafe.ai/models.md) publish no
  accuracy numbers at all. Never quote JDI as browser/decide-element
  accuracy (see also [adr-001-model-choice.md](adr-001-model-choice.md),
  which quotes JDI correctly as a general signal).

**Live/field evidence (no STEP % — failure classes and time shares):**

- [TinyFish, “When Jev Breaks”](https://www.tinyfish.ai/blog/jev-web-agent-speed-failures)
  (2026-10-14): 142 live runs / 1,395 decisions; Jev 102 ms/decision
  (~8,700 input tokens) = **9% of step time; act+settle 64%**, mean step
  1.25 s. Four failures: 32K menu budget, no persistent plan on
  multi-part goals, decide→type handoff (correct field, wrong text),
  settle/bot-visibility. Picks “about as good as Sonnet-class” given the
  same menu+log is qualitative — **no accuracy % is reported**, so it
  cannot corroborate or refute 59.9%; it reframes where live time and
  live failures actually sit (browser/settle, not inference).
- Fixture A/Bs in PRs measure *availability*, not STEP:
  [PR #212](https://github.com/browser-use/jev-ultrafast/pull/212)
  scroll tasks 0/5→5/5 once scroll state is sent (premature `BLOCKED`
  after one scroll);
  [PR #195](https://github.com/browser-use/jev-ultrafast/pull/195)
  `BLOCKED` 0.43 over scroll 0.09, fixture 3/3→6/6 with one prompt rule;
  [PR #137](https://github.com/browser-use/jev-ultrafast/pull/137) above.
  All n=3–6. They prove a missing input/offer can zero a task class;
  they say nothing about Mind2Web ranking quality.
- Negative measured result worth keeping:
  [PR #34](https://github.com/browser-use/jev-ultrafast/pull/34) joint
  operation/target selection: 102/120 vs 98/120, McNemar p=0.2891,
  “No statistically established improvement… Overall acceptance: FAIL”,
  including one false `DONE`. Joint-vs-speculative head restructuring
  is not a free win.

### 2.5 Two slogans, graded

**“Element choice, not operation choice, is the bottleneck” —
WEAKENED (true in replay, unproven live).** In replay the gap is
consistent (#248: element 62.3 vs operation 86.8; kev: 47.9 vs 82.7;
AWM's gain is element-driven). But replay “operation” is truncated:
value ignored (vs Mind2Web's Operation F1 *with* value), and
`SCROLL`/`WAIT`/`DONE`/`BLOCKED` have no gold. Live counter-evidence
is operation-choice failure: premature stops (Decido live failures;
[PR #99](https://github.com/browser-use/jev-ultrafast/pull/99)
`BLOCKED`×9 after a correct pick, false `DONE` at 0.40 confidence),
`BLOCKED`-over-scroll (PR #195). No primary decomposes *live* failures
into element-vs-operation. Budget accordingly: element ranking for
STEP, stopping/scroll policy for TASK — both, scored separately.

**“One-pass models fail on multi-update state tracking” — WEAKENED
(one demonstrated axis, not *the* cause).**
[“What Can't Jev Decide?”](https://arxiv.org/html/2610.24929v1)
(synthetic program-labelled tasks, 1k–20k tokens, 2–64 options, + 738
real items graded by Qwen thinking-token counts — *not* Mind2Web)
finds Jev error-free at find-and-select up to 20k tokens/64 options,
final-state accuracy dropping “as soon as k=2” updates to 53–80% at
k=16, stale summaries followed in 61–100% of cases, and 97.5% of
one-pass-solvable vs 74% of reasoning-needed real items. That
demonstrates a real one-pass limit. It does **not** attribute #248's
misses: the bench's own (thin) miss description is same-label
near-ties, candidate-count/shuffle sensitivity is called the main
weakness there, and TinyFish documents token-budget, handoff, and
settle failures that are not multi-update tracking. Treat
multi-update tracking as a *hypothesis for harness memory/planning*,
not as the established explanation of 59.9%.

## 3. Does this port inherit it? (Q2)

**Verdict (weakened form): the port inherits the decision *shape*
intact. “Inherits the ceiling *number* unchanged” is unsupported** —
no real decide model has ever run here (§3.5), the default model is
swapped (§3.4), and upstream-side byte parity is secondary evidence
(§0). The port's own framing is correct and should be kept: “Decision
accuracy is the ceiling… The loop faithfully executes a wrong choice”
with looping *spend* bounded but “the underlying wrong-choice accuracy
is not” ([jev-ultrafast-port.md](jev-ultrafast-port.md):68-75). What
follows is the file-level audit behind that line. Post-drift-review
commits (`48f69d3` onward: native fills, scroll state, CDP deadlines,
clickable rows, textarea naming, boundary exit) closed the drift
review's P0/P1.1/P1.9 items as *status*; none touched its P1.4 ceiling
analysis, which this section updates but does not overturn.

### 3.1 Faithful inheritance (the ranking path)

| Piece | Local location | Effect |
|---|---|---|
| `validate_choice` strictness (choice ∈ ids, probability keys == offered ids, finite in [0,1], sum within 0.02 of 1, argmax-consistent) | `src/account_vault/model.py:83-99` (“Unchanged from upstream”); pins `tests/test_agent.py:68-84`, `tests/test_agent.py:119-132` | Neutral: rejects malformed answers; cannot fix a well-formed wrong pick |
| One-request speculative fan-out (`operation` + `<op>_target` heads; only operation-selected head executes) | `build_choose_request` `src/account_vault/model.py:175-233`; `parse_choose_response` `src/account_vault/model.py:260-262`; pin `tests/test_agent.py:97-117` (one call, bogus head ignored) | Faithful: same one-pass, no-reasoning shape that produces the ceiling |
| State payload (`page` + elements + `history[-10:]`); text-helper `text[:6000]` / `history[-6:]` | `src/account_vault/model.py:195-229`, `field_context` `src/account_vault/model.py:340-345` | Faithful except scroll additions (§3.3); 10-step window + flattened text are inherited limits |
| 6,000-char flattening + 250-element cap | `words.join().slice(0,6000)` `src/account_vault/snapshot.js:250-260`; `offered.splice(250)` `src/account_vault/snapshot.js:283-285`; mirrored `src/account_vault/static_dom.py:695,710-711` | STEP-neutral inheritance: structure-dependent picks (#149) stay at the upstream ceiling |
| Consume-once decisions; `DONE`/`BLOCKED` trusted after freshness check | `decision=None` before mutation `src/account_vault/agent.py:371-372`; terminal handling `src/account_vault/agent.py:374-381` | Faithful — and see verification gap §3.4 |
| Budgets `MAX_ACTIONS=60` / `MAX_DECISIONS=120` (rename of `MAX_STEPS`/`×2`) | `src/account_vault/questions.py:41-47` | Neutral rename; bounds worst-case calls/run (§5.3) |
| Prompts `TARGET` / `TEXT_VALUE` (no DEVIATION); `NEXT_ACTION` + two scroll sentences only | `src/account_vault/questions.py:3-39`; [adr-002-scroll-prompt.md](adr-002-scroll-prompt.md):34-35; sent at `src/account_vault/model.py:196,209` | Locally verified text; *byte-parity vs upstream* secondary (§0). Jev-tuned prompts now front a different default model (§3.4) |

### 3.2 Divergences in the offered set and labels — TASK-unblocking

Each item makes previously unreachable controls pickable (or labels
honest). None changes ranking among already-offered elements; STEP
effects are inferred and unmeasured (more candidates also means more
distractors — Appendix C).

- **Native input types** (upstream
  [#215](https://github.com/browser-use/jev-ultrafast/issues/215) /
  [#227](https://github.com/browser-use/jev-ultrafast/issues/227)):
  date/time/range/color mapped to fillable roles with `input_type`
  preserved (`src/account_vault/snapshot.js:73-80`,
  `src/account_vault/model.py:120-125`), CDP fills via native setter
  with format authority in the browser
  (`src/account_vault/browser.py:489-540`), helper told the format
  (`src/account_vault/model.py:314-322`). TASK only.
- **Bare contenteditable** ([#218](https://github.com/browser-use/jev-ultrafast/issues/218)):
  `[contenteditable]` + `isContentEditable`
  (`src/account_vault/snapshot.js:49-59,66`). TASK only.
- **Clickable rows** ([#23](https://github.com/browser-use/jev-ultrafast/issues/23),
  largest widening): `[onclick]` as button plus a second pass for
  `addEventListener` rows (`cursor:pointer` + list/overlay context,
  row-collapsed, **+40 extras cap**, scan caps 5,000/80), icon-only
  controls, label proxies for concealed radio/checkbox
  (`src/account_vault/snapshot.js:53-59,82-83,151-249`, mirrored
  `src/account_vault/static_dom.py:582-680`). Upstream repro: 60
  `TYPE_TEXT` actions into one textbox because suggestion rows were
  invisible. TASK +; STEP unknown/plausibly negative on dense pages.
- **Textarea naming** (upstream
  [PR #246](https://github.com/browser-use/jev-ultrafast/pull/246)):
  `TEXTAREA` excluded from child-text naming like `INPUT`
  (`src/account_vault/snapshot.js:35-44`,
  `src/account_vault/static_dom.py:401-408`). Input hygiene; direction
  positive, magnitude unmeasured.
- **Execution fidelity** ([#216](https://github.com/browser-use/jev-ultrafast/issues/216)
  exact `option_index`/`selectedIndex`,
  `src/account_vault/snapshot.js:132-140`,
  `src/account_vault/browser.py:472-487`; covered-node omission after
  act-time refusal, upstream
  [PR #245](https://github.com/browser-use/jev-ultrafast/pull/245)
  shape, `src/account_vault/snapshot.js:267-282`,
  `src/account_vault/browser.py:463-471`; pin
  `tests/test_regressions.py:737-769`). A correct pick now lands on the
  intended control; the pick itself is unchanged. TASK/safety.

### 3.3 Divergences in decision inputs and operation space

- **Scroll state + `BLOCKED` suppression (ADR-002)** — *the only landed
  divergence that converts a systematic TASK failure class.* Requests
  now carry `page.scroll` (`src/account_vault/model.py:214-223`),
  history records `scroll_before`/`scroll_after`
  (`src/account_vault/agent.py:458-475`), `continuing_scroll` proves
  advancement (`src/account_vault/model.py:144-172`), and `BLOCKED` is
  **removed from the offered operations** while a proven scroll can
  continue (`src/account_vault/model.py:189-194`), with two
  `NEXT_ACTION` scroll sentences
  (`src/account_vault/questions.py:7-25`). Pins:
  `tests/test_regressions.py:652-716`. Judge: TASK + (below-fold
  reachable); STEP element ranking 0 by construction — it *forbids one
  answer* rather than improving element choice. The upstream 0/5→5/5
  magnitude (PR #212) is secondary here (PR body opened tracker-wide;
  local benefit mechanism-verified, size not locally measured).
- **Text-helper identity** ([#191](https://github.com/browser-use/jev-ultrafast/issues/191)/
  [#198](https://github.com/browser-use/jev-ultrafast/issues/198)/
  [#202](https://github.com/browser-use/jev-ultrafast/issues/202)):
  `field_context` carries `node/dom_id/name/placeholder/input_type/scope`,
  cache key includes `node` (`src/account_vault/model.py:325-339`,
  `src/account_vault/snapshot.js:114-126`,
  `src/account_vault/agent.py:407-417`; pins
  `tests/test_regressions.py:195-251`). Fixes the *second* model
  (wrong-field/wrong-text fills); `choose()` ranking untouched. TASK
  only.
- **Scroll-commit boundary early exit** (`src/account_vault/browser.py:180-195,326-363`):
  boundary observe p50 3,076 ms → 108 ms
  ([latency-results.md](latency-results.md)). Changes *when* the
  decider is asked, never *what* it picks. COST only.

### 3.4 Guards, the confidence half-gap, `DONE` trust, and the model swap

**Guards recover or stop *after* a wrong pick; first-pick STEP is
definitionally unchanged:**

- No-op drop + episode cap `MAX_NO_PROGRESS=12` (upstream
  [PR #209](https://github.com/browser-use/jev-ultrafast/pull/209)
  shape; [#94](https://github.com/browser-use/jev-ultrafast/issues/94)
  `page_changed=None` counts as no progress):
  `src/account_vault/agent.py:198-237,480-519`,
  `src/account_vault/questions.py:50-55`; pins
  `tests/test_regressions.py:270-283,506-565`. The runner-up gets a
  turn after one wasted step. TASK recovery + COST bound.
- Cycle guard (#247): identical transitions over window 12,
  suppress-at-2/block-at-3 (`src/account_vault/agent.py:66-83,520-550`,
  `src/account_vault/questions.py:56-60`; pins
  `tests/test_regressions.py:568-631,719-734`; hub-and-spoke explicitly
  *not* a cycle). COST/safety + one recovery turn.
- Stale/shape safety ([#132](https://github.com/browser-use/jev-ultrafast/issues/132)/
  [#133](https://github.com/browser-use/jev-ultrafast/issues/133)):
  id **and** node must match else `StalePage`
  (`src/account_vault/model.py:266-271`,
  `src/account_vault/agent.py:46-63,262-303,382-396`); 3 never-executed
  decisions block (was: 120 re-asks then crash). Pins
  `tests/test_regressions.py:257-264,385-441`. Safety/COST.
- Wire robustness (`post_json` error-in-200/non-JSON → `ModelError`,
  retry only `{429,529,503}`, `timeout=25`,
  `src/account_vault/model.py:50-80,251-252,366-433`;
  `MissingValueError`→`blocked` `src/account_vault/agent.py:419-428`).
  COST/safety; ranking untouched.

**Two correctness gaps found by the audit (cheap, load-bearing) —
both fixed 2026-11-22 (gate on both confidences by default at 0.5 with
`on_uncertain`; checker-owned completion via `Agent(done_when=…)`);
the text below is the pre-fix audit record:**

1. **Confidence gate is off, operation-only, and escalation-free.**
   `min_confidence=0.0` default (`src/account_vault/agent.py:98,126`);
   when enabled it gates *operation* confidence only
   (`src/account_vault/model.py:283`, `src/account_vault/agent.py:343-362`);
   `target_confidence` is recorded but never gated
   (`src/account_vault/model.py:285-287`; consumed only by inspector
   display); no escalation code exists (the ADR-001 “escalation hook”
   comment at `src/account_vault/agent.py:349` is a comment). ADR-001
   Decision 3 (“operation **or target** confidence… optionally re-ask
   Jev hosted”, [adr-001-model-choice.md](adr-001-model-choice.md):255-258)
   is therefore **half-implemented with shipped effect exactly zero**.
   Upstream [#120](https://github.com/browser-use/jev-ultrafast/issues/120)
   measures instruction influence on probabilities (0.999→0.907→0.761,
   still top 6/6), *not* gate accuracy; gate PRs
   ([#127](https://github.com/browser-use/jev-ultrafast/pull/127),
   [#99](https://github.com/browser-use/jev-ultrafast/pull/99)) report
   no accuracy %. Even completed, a gate is COST/safety (refuse/re-ask);
   STEP improves only via the fallback model's own accuracy — unmeasured,
   and `d1-2B` probability calibration is unknown.
2. **The agent trusts `DONE` on the real-decider path.** `Agent.act`
   accepts `DONE` after `fresh()` alone
   (`src/account_vault/agent.py:374-381`). Independent verification
   exists only outside the loop: `python -m account_vault`'s `verify()`
   (`src/account_vault/__main__.py`) and `HeuristicDecider`'s
   `done_when` (`src/account_vault/model.py:458-567`). With the default
   `SystemOneDecider` (`src/account_vault/agent.py:122`) neither
   applies — a confident false `DONE` (PR #99's 0.40 example is the
   upstream shape) would be reported as success by `Agent.run()`. Any
   future real-model TASK claim needs a checker wired in first (§5.3).

**Model swap — the only STEP lever, direction unknown.** Defaults are
local `d1-2B` at `http://127.0.0.1:8000/v1/systemone`
(`src/account_vault/settings.py:38-41,78-81`,
`src/account_vault/model.py:298-302`; `.env.example` matches), replacing
upstream's hardcoded `api.typesafe.ai` ([#241](https://github.com/browser-use/jev-ultrafast/issues/241)/
[#25](https://github.com/browser-use/jev-ultrafast/issues/25)/
[#125](https://github.com/browser-use/jev-ultrafast/issues/125) wire
configurability is neutral). Prompts tuned for hosted Jev now front
`d1-2B` (“Prompt/model mismatch, never tested”,
[jev-ultrafast-drift-review.md](jev-ultrafast-drift-review.md) §5.4).
There is **no d1 server/adapter code in the repo** (`transformers`/
`vllm`/`torch` grep over `src`/`scripts`: zero hits; heavy stack is
optional extras in `pyproject.toml`), so the default endpoint serves
nothing today.

### 3.5 What has never been exercised

Four independent in-repo statements agree, and the audit confirms them:

- “The System-One wire client is unit-tested with mocks but not yet
  exercised against a real decide model. The vault and eval harness are
  not yet implemented” (`README.md:32-35`).
- “Still unexercised: a real decide model behind the wire client (all
  live runs use the offline decider doubles) and the Flights task”
  ([jev-ultrafast-port.md](jev-ultrafast-port.md):230-231).
- “No real decide/text model was run… The ADR-001 gate remains open”
  ([latency-results.md](latency-results.md)); decide “latency” there is
  the ~0.05 ms double.
- ADR-002: scroll prompts validated “not yet by a local real-model run”
  ([adr-002-scroll-prompt.md](adr-002-scroll-prompt.md):42-44).

The doubles (`HeuristicDecider` + `MappingTextProvider`,
`src/account_vault/model.py:455-587`) emit confidence 1.0 — the
confidence gate can never fire under them — and the demo/latency
harnesses say so themselves (“a machinery demo, not a model
evaluation”, `src/account_vault/demo.py:65-88`; “machinery, not
hosted-model quality”, `scripts/latency_harness.py:31-37`). Live-CDP
tests drive real Chrome with scripted deciders
(`tests/test_live_cdp.py`). **Every green gate in this repo proves loop
mechanics, not decision quality.** The ADR-001 validation gate
(Flights/Wikipedia/hotel, ≥3 runs/backend, independent checkers, keep
`d1-2B` iff within 1 failure of Jev *and* p50 <100 ms on M4,
[adr-001-model-choice.md](adr-001-model-choice.md):317-331) has never
run; no eval harness, result file, or decision log exists in the tree.

## 4. Which changes would move it? (Q3)

Judge each lever on **STEP / TASK / COST** separately, with its evidence
grade. “+” needs a measurement; “?” means mechanism-plausible but
unmeasured; “0” means no effect by construction.

| Lever | STEP | TASK | COST | Evidence |
|---|---|---|---|---|
| Landed: offered-set widening (#23/native/contenteditable/textarea/option/covered, §3.2) | ? (may hurt: +40 distractors) | + (unblocks task classes) | ~ | Upstream n=3–6 fixture passes (PR #137/#212/#195) measure availability, not STEP; no local real-model STEP exists |
| Landed: scroll state + `BLOCKED` suppression (ADR-002, §3.3) | 0 (element ranking) | + (below-fold reachable) | + via boundary exit | Mechanism test-pinned locally; upstream magnitude secondary |
| Landed: guards (no-op/cycle/stale, §3.4) | 0 by construction | + recovery (runner-up turn) | + (bounded spend) | Test-pinned; port notes explicit: spend, not accuracy |
| Proposed: structured page text (#149) | ? | ? | − (larger payloads) | Proposal only; experiment designed, **no results** |
| Proposed: confidence gate + escalation (complete §3.4 gap 1) | 0 as shipped; ? via fallback | ? | + safety | #120 measures influence, not accuracy; PR #127/#99 unmeasured; calibration unknown |
| Proposed: planner (P1.5 branch) | N/A (never executes) | unproven | ~3× | Branch table: “does not establish a reliable improvement” |
| External-only: task fine-tuning | + (externally) | + (externally) | − (data/training) | Laya 0.10→0.66; Decido 42.5→78.6% within-harness; **not local evidence** |
| Model swap Jev↔`d1-2B`↔joey-1 (ADR-001) | ? (only lever that *can* move STEP) | ? | differs | No browser number for `d1-2B`/joey-1; ADR gate never run |

**Structured text (#149).** [#149](https://github.com/browser-use/jev-ultrafast/issues/149)
((c), body+comment opened): `words.join('\n').slice(0,6000)` loses
card/row grouping, so duplicate “[7] Add / [8] Add” labels are
ungrounded — the same-label near-tie failure #248's write-up names.
The proposed A/B (side-by-side `text_structured` field, offline payload
check first) has **no reported results**; locally P1.8 remains open
([jev-ultrafast-drift-review.md](jev-ultrafast-drift-review.md):298-307,
“No semantic tree… is sent”). This is the most plausible *input-side*
STEP lever, and it is exactly the kind of change Tier R can A/B for
cents (§5.2). Do not claim a gain before that run; do not break the
6,000-char budget without pricing tokens/latency (TinyFish already
averages 8.7k tokens live).

**Confidence/escalation.** Completing ADR-001 Decision 3 (gate on
operation **or target** confidence, re-observe/re-ask, optional hosted
escalation) is worth doing for an *account vault* on safety grounds
alone — page text is adversarial input, and existence proofs for
injection-via-page-text are enough to justify refusing low-confidence
mutations (see [jev-ultrafast-issue-analysis.md](jev-ultrafast-issue-analysis.md)
§6, HiddenLayer single-query 91%-vs-9% split: existence proof, not a
field rate). But sell it as COST/safety. Accuracy gains require (a)
calibrated probabilities from the *actual* backend (unmeasured for
`d1-2B`), and (b) a fallback that is itself more accurate on the
gated subset (unmeasured). #67's verify/retry fork is the cautionary
tale: plausible machinery, 0/20, 5.7× cost.

**Planner (drift P1.5).** Branch-only `codex/planner-loop` (15 ahead,
new `planner.py` “never executes browser actions”, **no PR**) reports a
frozen 20-task table: Previous 3/20 raw (1/20 supported) vs
State-fixes 4/20 (1/20) vs State+planner 3/20 (3/20), cost
$2.03/$2.21/$6.06, with the branch's own verdict “does not establish a
reliable improvement. General reliability remains poor” (prior BU 2.0
14/20 not rerun; full results docs unopened — Appendix C). Issue
[#187](https://github.com/browser-use/jev-ultrafast/issues/187)'s
multi-stage proposal (“72–80% estimated”, “benchmarking infrastructure
is not currently available”) carries no weight. Local sequencing is
right: **do not evaluate a planner before the real-model baseline
exists** — there would be no signal to attribute.

**Fine-tuning (strongest external lever — not local evidence).**
[Laya fine-tune write-up](https://nandhakishorm.github.io/laya/finetune_browser_agent/)
(421M, `/v1/systemone` shape, held-out n=2,734 at ~45 candidates):
element top-1 **0.10 (chance, zero-shot) → 0.66 fine-tuned**, operation
0.54→0.88, live 16 tasks 0%→50–62%; caveats that matter: Mind2Web alone
kills `DONE`/`TYPE_TEXT` (real DONE states had to be collected by
executing clicks), and an input-format fix alone moved Mind2Web click
top-1 0.44→0.51. Within #248's harness, Decido no-web 42.5% vs
web-trained 78.6% points the same way. Corollary: kev's Laya zero-shot
3.8% (≈random) measures *lack of task training*, not an architecture
ceiling — and by extension, a zero-shot `d1-2B` browser number (when
measured) must not be read as `d1-2B`'s ceiling either. Cost here is a
data/training pipeline; no figure in the bundle prices it, so none is
invented in §5.

**Model swap.** Candidates in evidence: hosted Jev (59.9% in #248's
harness, 43.5% in kev's — incomparable, §2.4); `d1-2B` (no browser
number; JDI v0.3 51.1 general only; MPS latency risk §5.4); joey-1
(JDI v0.4 63.30 general only; CUDA-only self-host, 16k
refuse-not-truncate, §5.2); Decido/Kev are harness-internal
comparators, not drop-in local defaults. ADR-001 already specifies the
decision procedure (within-1-failure + latency gate). What is missing
is not a better argument — it is the run.

**Recommended order (evidence per dollar):** (1) $0 offered-set recall
audit + `d1-2B` MPS smoke (§5.4 steps 1–3) — either can kill the
current plan cheaply; (2) Tier R paired replay Jev vs survivor(s),
with #149 structured-text and offered-set ablations as extra arms
(frozen bodies make arms ~free); (3) wire independent `DONE`
verification + complete the confidence gate *before* any live TASK
claim; (4) Tier L for the survivor, static tasks first; (5) planner
evaluation only against that baseline. Prompt tweaks without an arm
in (2) are how #67 happened.

## 5. What a real test here would cost (Q4)

Dollars below distinguish **VERIFIED** prices (primary fetched) from
**ASSUMPTION** anchors (labelled, with sensitivities). Engineering
estimates are 1-engineer assumptions for triage, not schedule
commitments. Unverified prices are flagged, never invented.

### 5.1 Cost anatomy (why dollars are noise)

- **One billed decide/step, nominally.** `choose()` issues exactly one
  `post_json` (`src/account_vault/model.py:296-307`, `timeout=25`);
  retries only on 429/529/503 (`src/account_vault/model.py:54-63`) —
  worst case 3 HTTP attempts/step (whether retries re-bill is
  unstated; worst case 3× the <$1 anchors below, still small).
  `field_text` (`src/account_vault/model.py:390-433`) is a separate
  call used only on live fills — Tier R needs zero text-helper calls
  (and therefore never tests typed-value correctness).
- **Hosted Jev price VERIFIED:** $0.042/Mtok input, output free;
  64k/request + 32k/question (262k opt-in); rate limits console-only
  per org ([docs.typesafe.ai/models](https://docs.typesafe.ai/models.md)).
  At the ADR-001 Flights anchor (~5,300 tok/request, ASSUMPTION for
  Mind2Web), 1,500 steps = **$0.33/arm**; sensitivity 2k→$0.13,
  8.7k→$0.55, 15k→$0.95. True tokens need a pilot reading
  `Decision.usage` (`src/account_vault/model.py:290`).
- **Joey-1 hosted VERIFIED (card):** $0.01/1M input on Baseten →
  ~$0.08/arm at the same anchor; 16,384-token max, over-length
  *refused not truncated* ([joey-1 card](https://huggingface.co/baselabs/joey-1)) —
  over-length rate must be pre-counted and scored separately. No
  latency/rate-limit table on the card (UNVERIFIED).
- **Live-run worst case is capped by budgets:** 120 decide calls/run
  (`MAX_DECISIONS`, `src/account_vault/questions.py:46-47`) × 5.3k tok
  × $0.042/Mtok ≈ **$0.027/run**; 54 capped runs ≈ $1.44. Text-helper
  $ is UNVERIFIED: the configured `inception/mercury-2.5` returned 404
  on OpenRouter at fetch time (existence/price to re-check before
  Tier L budgeting).

### 5.2 Tier R — replay (#248-style, tests STEP)

**Design:** fork [decido-ai/browser-agent-decision-bench](https://github.com/decido-ai/browser-agent-decision-bench)
(1,500 steps, 500/split; `run/score/compare`; mock-endpoint tests need
no key), but rebuild request bodies with **this port's**
`build_choose_request` (`src/account_vault/model.py:175-233`) and parse
with `parse_choose_response`/`validate_choice`
(`src/account_vault/model.py:83-99,236-293`) so invalid envelopes count
wrong instead of crashing. Freeze bodies once; send *identical* bodies
to each arm (Jev hosted, `d1-2B` in-process adapter, optionally joey-1
hosted; extra arms: #149 structured text, offered-set ablations).
Primary: STEP (op+element). Secondaries: element-only, op-only,
per-split, **offered-set recall** (gold present at all), over-context
refusal rate, per-decision `latency_ms`/`usage`. `DONE`/`BLOCKED`/
`SCROLL`/`WAIT` predictions count as wrong steps; **stopping remains
unscorable** (§2.1). Mind2Web has no scroll positions, so local
`BLOCKED` suppression never triggers in replay — document, don't “fix”.

**Hard part (not the API calls):** Mind2Web→snapshot gold-matching.
Dataset access VERIFIED: CC-BY-4.0, not gated
([HF card](https://huggingface.co/datasets/osunlp/Mind2Web)); test
splits as ~568 MB zip (password `mind2web`); `raw_html` required when
`pos_candidates` is empty in `cleaned_html`. But Mind2Web
`backend_node_id` vs snapshot WeakMap ids
(`src/account_vault/snapshot.js:7-14`) have **no stable join** (match on
tag+attributes+accessible name, hand-audit ~50 steps), and
visibility/geometry (`checkVisibility`/`getBoundingClientRect`) does
not exist in raw HTML — the Python snapshot port must approximate it.
Hence offered-set recall first: if gold is unoffered on >30% of steps,
the local ceiling is perception, not the model, and full replay is
moot. Op mapping: `TYPE`→`TYPE_TEXT` (element primary; value-match
secondary on the TYPE subset only), `SELECT` gold value→
`index:option_n` (`src/account_vault/model.py:137-140`); history
synthesised into `{action,kind,text,page_changed,scroll_*}` shape
(`src/account_vault/model.py:226-229`). Bench snapshot-port fidelity to
this repo's *deviated* `snapshot.js`/`questions.py` (scroll sentences
break upstream parity) must be adapted or results measure upstream,
not us. Bench `data/NOTICE.md` licence terms unopened (Appendix C).

**Sample size (computed, normal approx., α=.05, power .80):**
two-sample 60% vs 65% needs ~1,469 steps/arm; paired identical-body
design (as #248, bootstrap CIs) is strictly more efficient — **1,500
paired steps is correctly sized for ~5 pp**. Single-arm 95% CI
half-widths: n=300 ±5.5 pp, 500 ±4.2, 1,000 ±3.0. A 200–300-step pilot
detects only 15–20 pp gaps (pipeline validation, not a model decision).
Steps within a task are correlated: cluster/bootstrap by task, or CIs
are optimistic.

**Tier R totals (fork path):** engineering **4–8 days** (d1 adapter
1–3 d incl. `.[local]` install/download; builder adapt + 50-step audit
2–4 d; runner/scoring 0.5–1 d; greenfield 10–15 d); API **$0.13–$0.95**
Jev (+$0.03–$0.23 joey-hosted sensitivity band); wall-clock Jev ~4.5 min
sequential at 178 ms anchor (1–2 min at `--workers 4`), `d1-2B` MPS
~19 min at 753 ms (M5-Pro basis; M4 slower/unverified) — **machine
<1 h total**. Cheapest slice: offered-set recall + token audit on 200
steps, 1–2 days, **$0**.

### 5.3 Tier L — live tasks (ADR-001-gate style, tests TASK)

**Design:** ADR-001 tasks (Google Flights Zürich→London, Wikipedia,
local hotel filter; [adr-001-model-choice.md](adr-001-model-choice.md):317-328)
each with an **independent outcome checker** (final URL/title/visible
text/form state — never model `DONE`; patterns exist in
`tests/test_live_cdp.py` and `scripts/latency_harness.py` done_when
checks). Order static→live: `examples/hotel_search.html`,
`src/account_vault/static/catalog.html` / `fixture.html`, MDN #129
exact-path (cheap deterministic checker), #247 as a *local catalog-sort
surrogate* (originals #67/#247 have no saved traces — live originals
exploratory only), Flights/Wikipedia last. Reuse the dedicated-Chrome
live pattern + `TimedDecider` stage timing
(`scripts/latency_harness.py:250-260`); per-step local machinery is
verified ~33 ms p50 ([latency-results.md](latency-results.md)), decide
adds 178 ms hosted anchor or ~753 ms MPS 5K-state, plus unmeasured
live settle (TinyFish field figure 1.25 s/step end-to-end is direction
only). **Precondition:** wire independent `DONE` verification into the
run path (§3.4 gap 2) or TASK numbers are self-reported.

**Runs and why the ADR minimum under-decides:** ADR minimum
3 tasks×3 runs×2 backends = **18 runs** (~$0.07 Jev decide at 17
calls/run anchor) gives **±52 pp** per-task CIs — statistically
near-useless as a keep/drop decision (keep it only as a smoke gate).
Decision-relevant: **10 runs/task/backend = 60 runs** (~$0.11 anchor,
worst-cap ~$1.44/54). Engineering: **3–6 days build** (checkers
0.5–1 d each, Flights hardest — dynamic dates/prices; harness 1–2 d)
**+ 0.5–1 day/round flake triage**. UNVERIFIED: Flights
anti-bot/consent success from this network, site drift, date gold,
text-helper price (§5.1), joey latency. Pre-register checkers and
freeze goal text (dates relative to run day); keep `record_dir` traces
(`src/account_vault/agent.py:131-176`).

### 5.4 Cheapest decision-relevant sequence

1. **$0, 1–2 d:** offered-set recall + token histogram + over-16k rate
   on 200–500 Mind2Web steps through *this* snapshot/builder.
2. **<$0.10, 0.5 d:** 300-step Jev pilot for true `usage` tokens +
   latency.
3. **$0, 1–3 d build + ~1 min run:** `d1-2B` MPS smoke on 50 frozen
   bodies. Decision-relevant fact (VERIFIED card,
   [LiquidAI/d1-2B](https://huggingface.co/LiquidAI/d1-2B)): speed table
   is **753 ms at 5K-token state on M5 Pro MPS** (H100 52 ms; headline
   5.7 ms is small-state H100) vs ADR-001's **<100 ms p50 on M4** —
   753 ms-class on M4 fails the gate before any full run. Also verify
   `system_one` output reshapes cleanly into `validate_choice`
   (`src/account_vault/model.py:83-99`); vLLM `/v1/systemone` serving
   is UNVERIFIED (fetched Liquid vLLM doc shows CUDA chat serving
   only) — the in-process adapter route avoids that dependency.
4. Full 1,500 paired Tier R (~$0.33 Jev anchor, <1 h).
5. Tier L for the survivor only, static first.

**Bottom line: <$2 worst-case API anchors; 1–2 engineering weeks across
both tiers plus triage.** Anything quoting a larger dollar figure for
the *decide* side is pricing engineering or GPU rental (excluded here
rather than invented) — say which.

> **Addendum (2026-11-22): step 3 ran.** The MPS smoke is recorded in
> [d1-mps-smoke.md](d1-mps-smoke.md): on this M4 the card's 753 ms
> (M5 Pro) became **12,023 ms p50 at 4,990 tokens** compiled, and
> in-loop decide p50 is **8,056 ms** — the ADR-001 <100 ms gate
> **failed**, worse than this section's "753 ms-class" prediction
> by ~16× at matched size (torch-MPS eager/compiled path, 16 GB Air
> swap/thermals; see smoke §1/§5). `system_one` output does reshape
> cleanly into `validate_choice` (adapter + tests landed with the
> smoke). Steps 1–2 and 4–5 above are unchanged; per this doc's own
> sequencing, Tier R now prices the *successor* candidates, with
> the harness's `--decider` wiring ready to measure each.

## 6. Claims to avoid

Each of these is unsupported or falsified by the evidence above. Do not
state them in docs, PRs, or issue triage:

- “59.9% is the live task-success rate” / “replay predicts live
  completion” — falsified in-thread (74% vs 75% live, §2.2).
- “59.9% and 43.5% measure the same thing” / “Jev improved between
  them” — different harnesses (§2.4).
- “#67 proves looping / quantifies inaccuracy” — looping is #247; #67
  has no metric and its fix scored 0/20 (§2.3).
- “Verify/retry fixes accuracy” — the only test disconfirmed it (§2.3).
- “Element-not-operation is *the live* ceiling” — replay-only; live
  failures include stopping/scroll operation errors (§2.5).
- “Multi-update tracking is *the* cause of Mind2Web misses” — one
  synthetic axis; no miss taxonomy supports exclusivity (§2.5).
- “JDI 60.7/63.3/51.1 is browser accuracy” — general decisions,
  mixed versions (§2.4).
- “Decido 78.6% is independently reproduced / live-superior” —
  vendor-reported, not reproduced; live parity (§2.2).
- “This port inherits the ceiling *number*” / “landed divergences raise
  STEP” — no local real-model measurement exists (§3).
- “Offered-set widening helps ranking” — may hurt; unmeasured (§3.2).
- “Scroll 0/5→5/5 was locally measured” — upstream fixture, secondary
  magnitude (§3.3).
- “Confidence gate / escalation is active” — off, operation-only,
  escalation absent (§3.4).
- “Real-model TASK is verified” — `DONE` trusted; doubles only (§3.4–3.5).
- “Prompts are byte-identical (independently verified)” — secondary
  AST claim; upstream bodies not re-opened (§0).
- “`d1-2B` meets <100 ms on M4 / 5.7 ms at 5K state” — card says
  753 ms M5-Pro MPS at 5K (§5.4).
- “Planner improves reliability” — branch verdict: not established (§4).
- “Structured text (#149) helps” — no results (§4).
- “18-run ADR minimum decides keep/drop” — ±52 pp/task (§5.3).
- “No other accuracy numbers exist upstream” (without qualification) —
  pagination/unopened-body limits apply (§7.1, Appendix C).
- Headline “59.9%” without *offline / true-history / k=50 / quirk /
  live-parity* qualifiers (§2.2, §7.2).

## 7. Method, limits, and corrections to local docs

### 7.1 Method and coverage limits

- **Threads:** #67/#248 via `gh issue view --json …` (body+comments) +
  `gh api …/timeline`; every URL/issue/PR/commit they reference opened
  (fork metadata, PR #74, #145, #247, bench README/`scores.txt`,
  write-up). Images verified reachable (302→S3 PNG) but **not
  OCR/vision-read**; bench code/data/weights not executed/downloaded.
- **Tracker:** `gh issue list --state all` (57 issues, all OPEN, 0
  closed) + `gh pr list --state all` (182 PRs, 153 OPEN), all titles
  skimmed; keyword searches via `gh api search/issues` (first page of
  30 only — totals: confidence 143, loop 86, choose 77, BLOCKED 76,
  decision+model 72, DONE 52, wrong 44 were **not fully paginated**).
  PR bodies opened only for #34/#92/#99/#127/#137/#151/#195/#209/#212/#245
  (+ local #10/#15/#20). Hidden numbers in unopened bodies/comments are
  *unexcluded*, mitigated only by full title lists. Local repo:
  **0 issues ever**, 22 PRs; no local measured decide-accuracy benchmark
  in opened bodies.
- **Local audit:** full reads of `src/account_vault/{agent,model,questions,browser,snapshot.js,static_dom,settings,demo}.py|js`,
  `scripts/{analyse_jev_issues,latency_harness}.py`,
  `tests/{test_regressions,test_agent}.py`, all `docs/*.md`; `git log`
  at `eaabc44` (clean tree at audit time); site-knowledge DB listed/
  counted (§0). Upstream source at `cf3c406` and planner branches
  (beyond the branch README table) **not diffed**.
- **External:** primaries fetched (Mind2Web, SeeAct, AWM, kev,
  TinyFish, arXiv one-pass paper, Laya, joey-1/d1-2B cards, TypeSafe
  docs, Visual-Jev, Mind2Web HF/GitHub, Decido bench). Not opened:
  current Mind2Web leaderboard SOTA, full JDI board, planner full
  results docs, bench NOTICE, several per-step artifacts (Appendix C).
- **Adversarial pass:** the six load-bearing claims (§2.5 + comparability,
  inheritance, STEP-movement, replay→live) were each given a
  falsification target against the collected bundle; verdicts in §2.5/§6
  (1 unsupported-as-stated, 3 weakened, 2 falsified) reflect what
  survived. Execution completion of the research workflow is not
  research completeness — Appendix C is the completeness boundary.

### 7.2 Corrections this assessment makes to local docs

Numerically, local one-liners are right on “59.9%”; as evidence
summaries they mislead in three specific ways. This document supersedes
those passages (the older docs are left in place as history; do not
quote their #67/#248 lines without §2.2–§2.3):

1. [jev-ultrafast-drift-review.md](jev-ultrafast-drift-review.md) P1.4
   (“#67 covers looping/wrong choices”) **misattributes looping to
   #67** — that is #247. Drift §10 honestly discloses comment threads
   were fetched only for #247; the #67 line was written without #67's
   comments and misses the 0/20 disconfirmation.
2. [jev-ultrafast-port.md](jev-ultrafast-port.md):68-75 (“only the
   independent verifier catches it”) imports a verifier framing that
   neither #248 nor its links discuss — and #67's verifier scored 0/20.
   Keep the “spend bounded, accuracy not” sentence; drop the verifier
   implication until §3.4 gap 2 is closed.
3. All three docs omit #248's **offline/true-history/k=50 method, the
   61.5% quirk-excluded figure, and the 74%-vs-75% live parity** in
   their headline passages. ([jev-ultrafast-issue-analysis.md](jev-ultrafast-issue-analysis.md)
   is the most careful: it correctly warns kev-vs-#248
   incomparability and that replay cannot validate stopping — §6 there
   remains a good pointer list, re-verified here at primaries.)

**Queue implication:** #67 should drop as an *evidence* item (keep it
as a “simple tasks fail” symptom pointer to #247/#129 eval tasks);
#248 should stay at the top but be rewritten in the queue from
“59.9% accurate” to “STEP 59.9% offline (61.5% quirk-excl.), live TASK
ordering unvalidated — run Tier R/L (§5) before changing prompts,
planner, or model”. Drift P1.4's recommended pickup (metered Jev vs
`d1-2B` head-to-head, STEP and TASK scored separately) is endorsed
unchanged; §5 prices it.

## Appendix A. Source map

Upstream threads & tracker (opened): [#67](https://github.com/browser-use/jev-ultrafast/issues/67) (+ fork [yonikremer/jev-ultrafast](https://github.com/yonikremer/jev-ultrafast) metadata, [PR #74](https://github.com/browser-use/jev-ultrafast/pull/74)), [#248](https://github.com/browser-use/jev-ultrafast/issues/248) (+ [bench](https://github.com/decido-ai/browser-agent-decision-bench), [write-up](https://getdecido.ai/research/decido-1-web)), [#23](https://github.com/browser-use/jev-ultrafast/issues/23), [#26](https://github.com/browser-use/jev-ultrafast/issues/26), [#85](https://github.com/browser-use/jev-ultrafast/issues/85), [#120](https://github.com/browser-use/jev-ultrafast/issues/120), [#129](https://github.com/browser-use/jev-ultrafast/issues/129), [#132](https://github.com/browser-use/jev-ultrafast/issues/132), [#145](https://github.com/browser-use/jev-ultrafast/issues/145), [#149](https://github.com/browser-use/jev-ultrafast/issues/149), [#187](https://github.com/browser-use/jev-ultrafast/issues/187), [#224](https://github.com/browser-use/jev-ultrafast/issues/224)/[#225](https://github.com/browser-use/jev-ultrafast/issues/225) (dupes), [#247](https://github.com/browser-use/jev-ultrafast/issues/247); PRs [#34](https://github.com/browser-use/jev-ultrafast/pull/34), [#92](https://github.com/browser-use/jev-ultrafast/pull/92), [#99](https://github.com/browser-use/jev-ultrafast/pull/99), [#127](https://github.com/browser-use/jev-ultrafast/pull/127), [#137](https://github.com/browser-use/jev-ultrafast/pull/137), [#151](https://github.com/browser-use/jev-ultrafast/pull/151), [#195](https://github.com/browser-use/jev-ultrafast/pull/195), [#209](https://github.com/browser-use/jev-ultrafast/pull/209), [#212](https://github.com/browser-use/jev-ultrafast/pull/212), [#245](https://github.com/browser-use/jev-ultrafast/pull/245); branches `codex/planner-loop` (+ siblings listed, README table only). Local: [rabdomstran/account-vault](https://github.com/rabdomstran/account-vault) PRs [#10](https://github.com/rabdomstran/account-vault/pull/10)/[#15](https://github.com/rabdomstran/account-vault/pull/15)/[#20](https://github.com/rabdomstran/account-vault/pull/20); `src/account_vault/{agent,model,questions,browser,settings,demo,static_dom,__main__}.py`, `snapshot.js`; `tests/{test_agent,test_regressions,test_live_cdp}.py`; `scripts/{analyse_jev_issues,latency_harness}.py`; `docs/{adr-001-model-choice,adr-002-scroll-prompt,jev-ultrafast-port,jev-ultrafast-drift-review,jev-ultrafast-issue-analysis,latency-results}.md`; `README.md`, `pyproject.toml`, `.env.example`.

External primaries (opened): [Mind2Web paper](https://arxiv.org/html/2306.06070v3) + [HF card](https://huggingface.co/datasets/osunlp/Mind2Web) + [OSU GitHub](https://github.com/OSU-NLP-Group/Mind2Web); [SeeAct](https://arxiv.org/html/2401.01614v2); [AWM](https://arxiv.org/html/2409.07429v1); [kev-browser-use](https://www.arbazsiddiqui.me/kev-browser-use/); [TinyFish](https://www.tinyfish.ai/blog/jev-web-agent-speed-failures); [What Can't Jev Decide?](https://arxiv.org/html/2610.24929v1); [Laya fine-tune](https://nandhakishorm.github.io/laya/finetune_browser_agent/); [joey-1](https://huggingface.co/baselabs/joey-1); [d1-2B](https://huggingface.co/LiquidAI/d1-2B) + [Liquid vLLM doc](https://docs.liquid.ai/deployment/gpu-inference/vllm.md); [TypeSafe models](https://docs.typesafe.ai/models.md); [Visual-Jev status](https://github.com/guanxuyu-sv/visual-jev-browser-use/blob/HEAD/docs/status.md); [Multimodal-Mind2Web](https://huggingface.co/datasets/osunlp/Multimodal-Mind2Web).

## Appendix B. Tracker inventory (accuracy-relevant)

Class (a)/(b) only below carries weight; (c) recorded to prevent
re-triage. Full-corpus clustering remains in
[jev-ultrafast-issue-analysis.md](jev-ultrafast-issue-analysis.md).

- **(a) benchmarks:** #248 (§2.2); planner branch table (§4); PR #34
  negative (§2.4); fixture A/Bs PR #137/#212/#195 (§2.4, availability
  not STEP); #67 BU Bench 0/20 comment (undefined metric, §2.3).
- **(b) repros:** #247 loop 44 actions (§2.3); #23 invisible rows (60
  `TYPE_TEXT`, 120 decisions); #132 stale-id wrong-control (sealed
  artifacts quoted, not independently opened); #129 task-only (thin);
  PR #99 `BLOCKED`×9/false `DONE` (anecdotal); PR #209/#245 repeat-offer
  loops (fixture-verified, no %).
- **(c) proposals:** #187 multi-stage (asserted %); #149 structured text
  (no results); #120/PR #127/PR #99 confidence gates (influence
  measured, accuracy not); #26 boundary question; #145 speed/cost;
  #85 latency; PR #92 harness without results in body.
- **(d) dupes/noise for this question:** #224/#225 (HTTP 451, count
  once); #177 and showcase posts.

## Appendix C. Unresolved register

Preserved so a future reader knows exactly where this assessment stops.
Grouped; wording condensed only where researchers duplicated the same
gap (originals: 32 researcher items + critic consolidations).

*Threads:* #67 two images not OCR/vision-read (any trace/table inside
unverified); #67 fork code/prompts/confidence logic not line-inspected;
BU Bench definition/tasks/cost method undefined; “15 times in a row”
unverifiable. Decido bench code/`steps-final.zip`/`*.jsonl`/HF
weights/commit `8251e27`/live task list not executed — numbers are
consistently *reported*, not reproduced (vendor-affiliated builder;
56 quirk steps in headline).

*Tracker:* search pagination first-page-only (totals in §7.1); 153 open
PR titles skimmed, bodies opened for 10 upstream + 3 local only (hidden
numbers unexcluded, e.g. PR #35/#49/#68/#128/#141/#142/#182); planner
full results docs + `bu-bench-eval*` contents unopened; PR #34 eval doc
+ PR #92 comments unopened; #132 sealed artifacts unopened; local PR
#12/#16/#18 bodies unopened.

*Local:* upstream bodies at `cf3c406` not re-opened (byte-parity
secondary); ceiling number not re-derived in the local audit (verified
in §2.2 from primaries instead); PR #212 magnitude secondary locally;
`d1-2B` wire/calibration/long-context behaviour unknown (no serving
code/weights/endpoint in repo; vendor figures card-verified only in
§5.4 scope); planner branches not diffed; STEP direction of offered-set
widening inferred, not measured; **no local accuracy measurement exists
at all** — every local STEP/TASK direction in §3–§4 is Inferred.

*External:* SeeAct automatic absolutes not fully extractable (deltas +
oracle verified; commonly quoted 40.2/32.4/36.8 *not* claimed); 2025–26
Mind2Web SOTA leaderboard unopened (band rests on 2023–24 primaries);
kev split mix + Jev version unpinned; full JDI board unopened (v0.3 vs
v0.4 incomparable); Auto-Intent PDF 404 (numbers sourced from originals
instead, no loss).

*Cost/test:* TypeSafe numeric rate limits console-only; retry billing
unstated; text-helper model 404 at fetch time; `d1-2B` M4 latency/memory/
output-shape unrun (753 ms is M5-Pro card); vLLM `/v1/systemone`
undemonstrated in fetched primaries; true Mind2Web tok/step needs pilot
`usage`; bench NOTICE/fidelity unopened; joey hosted latency/limits
unknown, GPU rental deliberately excluded; Tier-L live-site feasibility
(Flights anti-bot, drift, roazon.nl structure) unmeasured;
Mind2Web→snapshot join/visibility approximation + within-task
correlation (§5.2); operation-accuracy definition drift across sources
(F1-with-value macro vs accuracy micro vs live op set, §2.5).

