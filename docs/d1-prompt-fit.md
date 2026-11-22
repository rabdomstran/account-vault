# d1 prompt fit: Jev's prompts on LiquidAI/d1-2B, measured

- **Date:** 2026-11-22
- **Question:** drift review §5 item 4 — prompts byte-identical to
  Jev's (hosted frontier System-One), now fronting local d1-2B, and
  never tested for fit. Latency was measured in
  [d1-mps-smoke.md](d1-mps-smoke.md); prompt suitability was not.
- **Machine / model:** Apple M4 MacBook Air 16 GB, MPS bf16 eager,
  [LiquidAI/d1-2B](https://huggingface.co/LiquidAI/d1-2B) @
  `a6bce77`, torch 2.15.0 / transformers 5.23.0 (the smoke setup).
- **Raw data:** [d1-prompt-fit.json](d1-prompt-fit.json) — 42 replay
  calls + 4 stopping calls + card sanity + rendered prompt excerpts.

## Verdict first

**The suspected mismatch is not there; two different ones are.**

1. **The JSON-stringified questions do not confuse d1.** Production
   rendering (compact-JSON `instructions`/`criteria` strings from
   `local_decider.translate_request`) and a plain-text rendering of
   the same words pick the same operation+target on 13/14 gold
   states, at identical strict step accuracy (8/14 each; §3 defines
   the scoring). If anything, production is marginally better behaved
   at the 0.5 confidence gate (passes 14/14 vs 11/14). Rewriting the
   translation to "card style" buys ~6% fewer tokens and nothing
   else measured here.
2. **Jev's rule text is load-bearing for d1, not dead weight.**
   Deleting `NEXT_ACTION`/`TARGET` (goal-only instructions, the
   shape of every card example) drops strict accuracy 8/14 → 5/14
   and produces the only two not-required picks in 42 calls: d1
   jumps to the goal's final target ("View Casa Flora") before
   fills/filters are set. Byte-parity is doing sequencing work.
3. **The real misfits: stopping and sequencing taste.** On
   completed pages d1 never picks `DONE` first (P(DONE) 0.09–0.35
   vs re-CLICK 0.50–0.82, both renderings) — the loop finishes only
   because completion is code-owned (`done_when`). And on the gold
   path it systematically prefers the salient SELECT matching a goal
   keyword over still-empty required fields (P(TYPE_TEXT) 0.03–0.04
   at catalog start). Every production-rendering pick was still a
   required subtask, so an order-insensitive page completes; a form
   where a select resets dependent fields is where this bites.
   Confidence barely separates right from other picks (mean 0.76 vs
   0.72), so the 0.5 floor is not a quality signal for this model —
   ADR-001's "probabilities are not interchangeable" warning,
   now with numbers.

Nothing here rescues d1-2B on this machine: replay calls cost the
same seconds the smoke measured (production median 7.3 s eager).
What changes is where the ADR-001 successor budget should go:
prompt re-authoring for its own sake drops down the list; stopping
verification and per-model confidence calibration move up.

## 1. What d1 expects (card + checkpoint source)

Read at the pinned revision, not just the card: the checkpoint's
`prompt.py` builds everything the model sees. One decision renders
as ChatML — state as pretty JSON (`json_only` style), then
`QUESTION:`, then the question block, answer read as a softmax over
single-token option codes (A…Z, then AA…; `d1_codes: 2`) at the
final position, temperature-calibrated (default 1.337 in
`calibration.json`). For a choice, the block is the `instructions`
text, then `Options:` lines of `code + description` — **option
names are not shown** in the default `desc` style — then
"Reply with the option code only." `check_question` describes
`instructions` as "the question as text" and `criteria` as
`{name: description}`. The card's examples are uniformly short,
direct questions ("Which team should handle this?") with 2–4 word
option descriptions ("Charges, refunds, invoices") over 3 options;
Ollama's and mistral.rs's d1 guides use the same shapes, and
Liquid's training notes say answer options were shuffled in
training. The card also warns answers get less reliable past
~64K tokens of state — not a factor at our 2.8–4.7K.

## 2. What we send

`build_choose_request` emits Jev's wire shape: `instructions` as a
dict (`{goal, rules}` for the operation head, `{goal, operation,
rules: [NEXT_ACTION, TARGET]}` per target head) and target
`criteria` values as dicts (`{element, current_value, role, …}`).
`translate_request` renders each dict as one compact-JSON string
(lossless, test-pinned). So d1's "question" is literally a JSON
blob with escaped newlines, and each element option line is a JSON
object string — on its face, far from the card's distribution.
Rendered excerpts for both renderings of the same state are in the
raw JSON (`rendered_operation_state0`). Sizes: production median
4,226 input tokens per decide (4 questions packed), plain-text
3,950, goal-only 2,594 — the rule text repeated per question is
~40% of the production prompt, as the smoke probe priced.

## 3. Method

Gold states: hotel (`examples/hotel_search.html`, `__main__`
DEFAULT_GOAL) and catalog (`static/catalog.html`, harness
TRAVEL_GOAL) driven with the scripted `HeuristicDecider` to
checker-verified `done`, recording each pre-decision page state +
history (hotel 6 + catalog 8 = 14 states, the same configs the
smoke loop ran). Each state was replayed through real d1 under
three renderings of the same wire body — **A** production,
**B** plain-text (same words, JSON wrappers removed), **C**
goal-only instructions with B's criteria — answers parsed through
production `parse_choose_response`/`validate_choice`. Scoring:
*strict step* = gold operation and target; *progress-valid* = the
pick is any subtask the checker still requires from that state
(gold order is one arbitrary valid order, so strict alone would
punish harmless reordering). Two extra calls per rendering (A/B)
on the completed pages test stopping, where DONE is gold. The
card's quick-start was run first as a sanity control and
reproduces the card's answers (`billing`, medium urgency,
noul 0.997).

Limits: n=14 hand-built fixture states, true (gold) history —
replay measures on-path picks, not recovery under the model's own
history; static backend only; one forward pass per cell (no
sampling exists to average). This prices prompt *rendering*, not
Mind2Web-class accuracy ([decide-accuracy-ceiling.md](decide-accuracy-ceiling.md)).

## 4. Results

| Rendering | Step (strict) | Operation | Progress-valid | Gate ≥0.5 | Median tokens | Median ms |
|---|---:|---:|---:|---:|---:|---:|
| A production (JSON strings) | 8/14 | 10/14 | 14/14 | 14/14 | 4,226 | 7,255 |
| B plain-text, same words | 8/14 | 10/14 | 14/14 | 11/14 | 3,950 | 7,200 |
| C goal-only (no rules) | 5/14 | 8/14 | 12/14 | 10/14 | 2,594 | 4,560 |

Disagreement taxonomy (all 21 non-strict calls): 19 are reorders
(the pick is a later required subtask taken early) — d1 takes the
promo/Design SELECT before the remaining fills at 4 states in
every rendering, picks the duplicate-label "Free cancellation"
twin once, or opens Casa Flora early (goal-only from mid-task;
production/plain-text only at the last-but-one state, at
0.88–0.92 confidence — the fixture's checker accepts it there,
as the smoke's 3-action live completion showed). The only 2 not-required picks are both goal-only:
re-selecting an already-set dropdown, and clicking a "Design
stays" nav link, both visibly hedged (0.42–0.45 confidence). No
call in any rendering produced BLOCKED, a premature DONE, or an
envelope our validator rejected.

Stopping probe (completed pages, gold DONE):

| Page | Rendering | Pick | P(pick) | P(DONE) |
|---|---|---|---:|---:|
| hotel | A / B | CLICK Search (again) | 0.69 / 0.82 | 0.20 / 0.09 |
| catalog | A / B | CLICK View Casa Flora (again) | 0.50 / 0.66 | 0.35 / 0.26 |

`NEXT_ACTION`'s "DONE requires visible evidence that ALL
requirements are satisfied" is present in both renderings and
does not move d1 to stop. Consistent with the smoke anecdote
(catalog finished via the checker, hotel died at the gate — the
model's own DONE decided neither) and with the known one-pass
final-state-tracking weakness: DONE as one choice line among
action lines asks the model to verify every requirement in the
same softmax that picks actions.

## 5. Implications

- **Do not re-author prompts per model on spec.** The measurable
  prompt lever here was *removing* rules, and it hurt. For the
  ADR-001 successor, A/B any prompt change on replayed states
  before adopting it — including token compression in the style of
  upstream PR #49, whose ~40% saving this experiment prices at a
  real sequencing cost if done by deletion rather than sharing.
- **Keep completion code-owned.** A successor evaluated on "did
  the model say DONE" will mislead in d1's direction (never) or
  Jev's (premature). If model-side stopping evidence is wanted,
  a separate noul verification question ("Is every requirement
  visibly satisfied?") fits d1's trained shapes better than DONE
  as a choice option; untested here.
- **Tune the confidence floor per model or not at all.** At 0.5,
  production rendering passes every replay pick including wrong
  orders, while B blocks three progress-valid picks. The floor
  currently measures rendering luck, not correctness.
- **Sequencing taste is the live risk to test next** — SELECT-first
  and final-target gravity under the model's *own* history
  (the smoke's hotel block happened off the gold path). A small
  live-suite extension with dependent-field fixtures would price
  it; replay cannot.
