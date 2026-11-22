# Latency results: what one loop step costs here, stage by stage

- **Date:** 2026-11-22
- **Measured tree:** `main` @ `6fe9604` plus the harness wrappers in
  [scripts/latency_harness.py](../scripts/latency_harness.py) (no product
  code changes; timing wraps the injected browser/decider and taps the
  `account_vault.browser.cdp` seam)
- **Raw data:** [latency-measurement.json](latency-measurement.json)
  (every run, every tick; summaries below are computed from it);
  settle-paths follow-up in
  [latency-measurement-settle.json](latency-measurement-settle.json);
  boundary-exit follow-up in
  [latency-measurement-boundary.json](latency-measurement-boundary.json)
- **Compared against:** upstream
  [docs/performance.md](https://github.com/browser-use/jev-ultrafast/blob/main/docs/performance.md)
  with its `flights-measurement.json` / `full-speed-measurement.json`,
  and the TinyFish field report cited in
  [jev-ultrafast-issue-analysis.md §6](jev-ultrafast-issue-analysis.md)
  ([When Jev Breaks](https://www.tinyfish.ai/blog/jev-web-agent-speed-failures),
  142 runs / 1,395 decisions: decide averaged 102 ms = 9% of a 1.25 s
  step; "acting on the page and waiting for it to settle took 64%")

## Verdict first

- **"Ultrafast" is now a measured property of the machinery, with a
  caveat.** On local pages one executed step costs **33–34 ms p50**
  of browser-side machinery over CDP (1.1–2.9 ms on the static
  backend). A complete 5–6-action task runs in **176–209 ms p50**
  of step time. Nothing in this port adds measurable overhead to
  the upstream design: protocol shape matches upstream's optimized
  arm almost exactly (≈7.5 CDP calls/step here vs ≈7.8 upstream).
- **The TinyFish 64% does not hold here as a loop invariant — in
  both directions.** As measured (offline decider doubles, so
  decide ≈ 0.05 ms), act+settle is **82–87%** of CDP step time,
  and settle alone is 74–76% — already above 64%. But substitute
  TinyFish's *own* decide average (102 ms) into the same steps and
  act+settle falls to **21–22%** of a 136 ms step — because their
  1.25 s step is dominated by waiting on real sites, which local
  fixtures do not have. The 64% is a property of their harness,
  sites, and decide latency combined, not of this loop design.
- **What does transfer is the ordering.** Browser settle is the
  largest *local* cost by an order of magnitude (≈26 ms/step vs
  ≈0.1 ms of decide plumbing). Once a real decide model is in the
  loop, its latency dominates everything local — which keeps
  ADR-001's <200 ms p50 decide budget the first lever, settle
  policy second, and snapshot cost third.
- **One structural surprise, inherited from upstream:** a
  freshness check is not cheap. `fresh()` re-executes the entire
  17 KB `snapshot.js` (CDP `MARKER` wraps `READ_STATE`;
  `StaticBrowser.fresh()` calls `_build()`), so the snapshot runs
  **3–4 times per executed step** (4 on fills) and twice even on
  the terminal DONE tick. That is 12–17% of CDP step time and
  93–96% of static step time. It is correct-by-design (semantic
  freshness before input), but it is the largest *reducible*
  local cost after settle.

## Method

One **step** = one `tick` (predict + act) that executed an action.
Terminal DONE ticks are recorded separately and excluded from the
headline shares (they have no act/settle); all-step shares are in
the raw JSON and differ by <2 points. Setup (browser construction,
navigation, the constructor's first observe) is excluded from step
time and reported separately, matching upstream's convention
("initial navigation is excluded in both arms").

Stage definitions are disjoint and sum to the tick wall time;
`other` is the unaccounted residual (Agent bookkeeping, check
framing) and stayed at 0.3% on CDP, 2–4% static:

| Stage | What is timed |
|---|---|
| snapshot | Executing `snapshot.js` — CDP: the `Runtime.evaluate(READ_STATE)` calls, *including* the full re-execution inside every `MARKER` freshness check; static: `StaticBrowser._build()` |
| settle | Post-input wait inside the next `observe()`: the capped 2-rAF promise (50 ms cap, 200 ms for combobox fills) + wheel-scroll commit poll. Zero offline by construction |
| observe | Rest of `observe()`: CDP/IPC framing, fingerprint hash, retries (screenshots off — the loop default, as upstream) |
| decide | `decider.choose()` wall. Offline `HeuristicDecider` = local CPU only; see the wire-shape micro-benchmark for request-building cost |
| act | `browser.act()` dispatch: pre-input freshness guard, in-page target resolver, `Input.*` calls |
| text | TYPE_TEXT helper (`MappingTextProvider` offline ≈ 0) |
| other | Residual to the tick wall |

Configs (fresh `Agent` + browser per run, 2 warmups discarded;
all 90 recorded runs ended `done` with the usual independent
`done_when` evidence):

| Config | Page | Backend | Runs | Acted steps |
|---|---|---|---|---|
| static-hotel | `examples/hotel_search.html` (8 elements) | StaticBrowser | 30 | 180 |
| static-catalog | `static/catalog.html` (22 elements) | StaticBrowser | 30 | 240 |
| cdp-catalog | same catalog page, `file://` in Chrome (15 elements live) | CdpBrowser | 15 | 90 |
| cdp-fixture | `static/fixture.html?scenario=travel`, JS-rendered (10 elements live) | CdpBrowser | 15 | 75 |

Environment: Apple M4, macOS 27, Python 3.12.16, Chrome
156.0.8078.82 headless with a dedicated throwaway profile on a
loopback debug port (same arrangement as `tests/test_live_cdp.py`;
the everyday profile is never touched). Decider/text are the
offline doubles throughout — **this measures the port's machinery,
not any model's latency or quality.** The modelled rows below add
published model latencies back arithmetically; they are labelled
as models, not measurements.

Reproduce: `uv run python scripts/latency_harness.py` (static) and,
with a dedicated Chrome pinned as in the live tests,
`... --cdp`. CDP call classification was cross-checked against
upstream's per-method accounting (below).

## Distributions (ms per acted step)

### CDP — the real-browser loop

cdp-catalog (n=90) and cdp-fixture (n=75) agree closely despite
different pages; the step is settle-shaped on both.

| Stage | Catalog p50 | Catalog p95 | Catalog share | Fixture p50 | Fixture p95 | Fixture share |
|---|---:|---:|---:|---:|---:|---:|
| snapshot | 5.68 | 8.97 | 17.4% | 3.24 | 8.44 | 12.0% |
| settle | 25.92 | 28.33 | 73.6% | 26.20 | 29.01 | 76.1% |
| observe | 0.09 | 0.11 | 0.3% | 0.07 | 0.08 | 0.2% |
| decide | 0.05 | 0.06 | 0.1% | 0.04 | 0.05 | 0.1% |
| act | 2.46 | 5.25 | 8.2% | 4.04 | 6.17 | 11.3% |
| text | 0.00 | 0.00 | 0.0% | 0.00 | 0.00 | 0.0% |
| other | 0.11 | 0.15 | 0.3% | 0.08 | 0.15 | 0.3% |
| **step** | **33.47** | **37.69** | 100% | **33.36** | **41.33** | 100% |
| act+settle | | | **81.9%** | | | **87.4%** |

Full runs: catalog 206 ms p50 (6 actions + DONE; setup 40 ms p50),
fixture 176 ms p50 (5 actions + DONE; setup 35 ms p50). By action
kind, step p50 is flat (click/fill/select all 33–34 ms on catalog;
fixture fills 40 ms) because settle dominates all three; fills
carry a higher snapshot share (20–22% vs 10–13%) — the fill path
freshness-checks twice more. Terminal DONE ticks cost 2.4–3.6 ms
mean, 96–97% of it two full snapshot executions. No scroll or
wait steps occur in these tasks, so the wheel-scroll commit poll
(`SCROLL_SETTLE_TIMEOUT`) and the 200 ms combobox settle cap are
*not* exercised here — see Limits.

Settle's shape is worth noting: p50 ≈ 26 ms against a 50 ms cap.
The wait resolves on the 2-rAF condition, not the timeout, so its
cost tracks the display/headless frame cadence (≈2 frames + IPC
here). On a 60 Hz pipeline expect the same code to sit nearer the
cap. Either way it is a *deliberate* wait for paint/autocomplete
state, not busy work — upstream introduced it so Jev never chooses
from an incomplete popup.

### Static — loop overhead with no browser at all

| Stage | Hotel p50 (n=180) | Hotel share | Catalog p50 (n=240) | Catalog share |
|---|---:|---:|---:|---:|
| snapshot (`_build()`) | 1.01 | 93.3% | 2.47 | 95.6% |
| decide | 0.02 | 2.0% | 0.04 | 1.5% |
| act | 0.01 | 0.8% | 0.01 | 0.6% |
| other | 0.04 | 3.8% | 0.06 | 2.3% |
| **step** | **1.09** | 100% | **2.62** | 100% |

Settle/observe are 0 by construction. Same catalog page, same
task: a static step is 2.6 ms p50 vs 33.5 ms over CDP — the ~13×
gap is entirely browser, IPC, and the settle wait; the Python
loop itself (decide double, bookkeeping, act logic) is ≈0.1 ms
per step. Snapshot cost scales with page size (8 vs 22 elements:
0.28 vs 0.82 ms per `_build()`, micro table below) and is paid
3–4× per step on both backends for the same reason: predict's
`fresh()`, act's internal `fresh()`, and the post-action
`observe()` each rebuild the full element table.

### Micro-benchmarks (n=100 each, settled page)

| Mechanism | Hotel (static) | Catalog (static) | Catalog (CDP) | Fixture (CDP) |
|---|---:|---:|---:|---:|
| one `observe()` p50/p95 | 0.28 / 0.53 | 0.82 / 1.34 | 1.76 / 3.38 | 1.36 / 1.77 |
| one `fresh()` p50 | 0.28 | 0.82 | 1.29 | 1.09 |
| one `snapshot.js` eval p50/p95 | — | — | 1.53 / 2.46 | 1.21 / 1.46 |
| fingerprint p50 | 0.018 | 0.038 | — | — |
| `build_choose_request` p50 | 0.06 | 0.09 | — | — |
| decide request JSON | 10,789 chars | 15,449 chars | — | — |

Three readings: (1) `fresh()` ≈ `observe()` — the freshness check
is a snapshot, on both backends, confirmed two independent ways
(micro here; 3–4 snapshot evals/step in the loop runs).
(2) Fingerprinting and observe framing are noise (≤0.04 ms) —
"observe overhead" is not a thing worth optimizing.
(3) The local CPU part of a real decide call (building the
speculative-fan-out request) is 0.06–0.09 ms; everything else in
upstream's 178 ms is network + model. Our requests
(10.8–15.4 k chars ≈ low-thousands of tokens) are smaller than
upstream's Flights average (90,558 input tokens / 17 requests
≈ 5.3 k/request) and TinyFish's ≈8,700, as expected on small
local DOMs.

## Against upstream performance.md

Upstream publishes task-level numbers plus per-run JSON; the
stage decomposition below is derived from their
`full-speed-measurement.json` candidate arm (same Google Flights
task, real Jev 1.13.0 + Mercury, live Chrome 152) so the shapes
can be compared honestly. Their text and decision totals are
model-call wall times; their CDP total covers every protocol
call (snapshot evaluates, settle promises, resolvers, input).

| Per-run share of elapsed | Upstream candidate (3 runs) |
|---|---|
| decide (16–18 requests, median 169–183 ms each) | 42–47% |
| text helper (2 calls) | 11–20% |
| all CDP calls (97–103 calls) | 31–40% |
| residual | 3–5% |

Corroborations and contrasts:

- **Protocol shape matches.** Upstream's optimized arm makes
  97–103 CDP calls per 13-action run (≈7.8/step); we make
  7.4–7.8 calls per acted step. The port did not re-inflate the
  1,092 → 101 improvement upstream credits for the speedup.
- **Runtime.evaluate dominates their CDP time too** (71–77 calls,
  2.1–2.5 s of the 2.4–2.8 s CDP total) — the same bucket our
  snapshot+settle+resolver split decomposes. On live Flights
  those evaluates are far slower per call than ours (bigger DOM,
  a page mutating under network load, settle waits hitting real
  autocomplete latency): their recorded post-action observe gaps
  average 67 ms vs our ≈30 ms (settle+snapshot p50) on files.
- **Their decide median (178 ms) is 5× our entire CDP step.**
  Their step economics are decide-dominated (42–47%) with text
  second; ours, with doubles, are settle-dominated. Neither
  contradicts the other — they are the same loop at opposite
  ends of the decide-latency axis. ADR-001's premise follows
  directly: below ~200 ms p50 decide, the browser wait is the
  largest *browser-side* term; above it, the model is the term.
- **End-to-end anchors:** upstream's local hotel-filter task took
  1.896 s with real models; our hotel task takes 7.2 ms of
  machinery (static) — the difference is 7 decide + 3 text calls,
  i.e. essentially all model/network, which is exactly the budget
  ADR-001 allocates. Screenshots were off in both loops; turning
  them on adds a capture per observe (upstream caps it at 5 s and
  degrades to none; unmeasured here).

## Against the TinyFish 64% (issue-analysis §6)

First, a correction to our own shorthand: TinyFish claim
*acting + settle* = 64% (decide 9% at 102 ms average; the
remaining ≈27% is not itemized in the post). Testing that exact
claim three ways:

| Test | Step mean | act+settle | settle alone | decide |
|---|---:|---:|---:|---:|
| TinyFish field (real sites, real Jev) | 1,250 ms | **64%** | — | 9% |
| Here, as measured, CDP catalog (doubles) | 34.2 ms | 81.9% | 73.6% | 0.1% |
| Here, as measured, CDP fixture (doubles) | 34.9 ms | 87.4% | 76.1% | 0.1% |
| Here + TinyFish's 102 ms decide substituted | 136 ms | 20.6–22.3% | ≈19% | ≈75% |
| Here + upstream's 178 ms decide (+463 ms text on fills, the Flights recording's average) | 306–444 ms | 6.3–10.0% | ≈6–9% | 89–92% |

So: **the direction holds, the number does not.** With no model
in the loop, act+settle exceeds 64% here — settle alone clears
it. With any published decide latency substituted, the share
collapses to 6–22% because our absolute act+settle (≈28–31 ms)
is ~25× smaller than the ≈800 ms implied by 64% of their 1.25 s
step. Their settle plainly includes real-site behaviour our
fixtures cannot exhibit: network-bound rendering, late
autocomplete, bot-mitigation friction (their fourth failure
mode is literally "settle timing / bot visibility once decisions
are fast"). Anyone quoting 64% for *this* loop should quote the
absolute terms instead: **≈26 ms settle + 3–4 ms act + 4–6 ms
snapshot per step on local pages, plus whatever the decider
costs.**

The practical consequence is unchanged and matches §6's bearing
note ("settle waits, not model latency, dominate" — once the
decider is fast): if ADR-001's local decider lands anywhere near
its <50 ms compute goal, the settle budget becomes the largest
single term in the step and the right target (it is a correctness
wait, so the lever is *when* to pay it, e.g. skipping the rAF
wait after actions that cannot spawn popups — not deleting it).
Second target if local latency ever matters at this scale: the
3–4× snapshot re-execution per step (a cheaper freshness marker
that does not rebuild the element table would save 7–10% of CDP
step time and ~60% of static step time; upstream has the same
shape, so this would be a deliberate DEVIATION needing its own
freshness argument, not a silent optimization). **Attempted the
same day — see the Follow-up: the static half landed, the CDP
half measured flat and was reverted.**

## Settle paths: scroll, wait, combobox (follow-up, 2026-11-22)

The first measurement's Limits noted these paths were never
exercised. The harness now has one CDP loop config each plus
direct mechanism micro-benchmarks
([latency-measurement-settle.json](latency-measurement-settle.json);
same setup, same machine — the original configs re-ran within
noise of the table above: catalog step p50 33.38 vs 33.47,
fixture 33.51 vs 33.36; static rows reflect the cheap-fresh
cache). The suspicion was correct: this is where the remaining
cost hides, and one case dwarfs everything else in the system.

| Path (per acted step) | n | Step p50 | Settle p50 | Act p50 | Snapshot p50 |
|---|---:|---:|---:|---:|---:|
| click/fill baseline (catalog/fixture) | 165 | 33.4–33.5 | 25.6–26.6 | 2.5–3.9 | 3.3–6.5 |
| scroll steps (tall page) | 60 | 49.8 | 34.0 | 11.7 | 4.1 |
| wait steps (700 ms reveal) | 60 | 112.6 | 0.0 | 106.9 | 5.3 |
| combobox fill (150 ms suggestion) | 15 | 181.4 | 169.9 | 5.6 | 5.7 |
| combobox option click | 15 | 31.8 | 24.8 | 4.0 | 2.9 |

Mechanisms isolated (direct browser drive, no Agent):

| Mechanism | Distribution |
|---|---|
| scroll act+observe (n=24) | act p50 15.0, settle p50 34.2 / p95 51.0, observe wall p50 35.6 |
| **scroll dispatched at the page boundary (n=5)** | **observe wall p50 3,076 ms** — the full `SCROLL_SETTLE_TIMEOUT` (3 s) commit poll, paid inside one observe |
| wait act (n=20) | act p50 108.9 (the fixed 100 ms sleep + freshness/dispatch), settle after = 0.0 exactly |
| combobox settle, suggestion at 0 / 120 / 300 ms (n=15 each) | settle p50 25.4 / 138.8 / 207.8; suggestion offered 15/15, 15/15, **0/15** |

Findings, in cost order:

1. **A boundary scroll is the most expensive single operation
   in the loop: ≈3.08 s inside one observe** — ~90 baseline
   steps' worth. It is the deliberate commit deadline from the
   #212/#209 interaction fix (a landed-but-unread scroll must
   not be recorded as a no-op), and in a normal run it is paid
   at most once per unchanged page: the unchanged observation
   then drops `scroll_down` from the next offer while the
   fingerprint holds. But any task that legitimately probes a
   bottom/top boundary on several distinct pages pays 3 s per
   probe, and nothing in the step distribution warns about it
   unless settle is timed separately — which is the argument
   for this harness existing. Ordinary (moving) scrolls exit
   the poll as soon as `scrollY` moves: settle p50 34 ms,
   p95 51 ms, i.e. the 2-rAF wait plus one or two 50 ms polls.
   Scroll acts themselves (p50 12–15 ms) cost 3–5× a click act.
2. **Combobox settle does exactly what its cap says.** Settle
   tracks suggestion arrival inside the cap (0 ms → 25 ms,
   the 2-rAF floor; 120 ms → 139 ms; loop fill at 150 ms →
   170 ms, 93.5% of that step) and gives up at ≈208 ms: at a
   300 ms suggestion delay the option was offered **0/15** times
   on the post-fill observation. Past-cap autocomplete is not
   an error — it costs one extra decide+wait cycle (~110 ms
   polling step) before the option becomes clickable — but a
   decider that re-fills instead of waiting would retype
   forever (upstream #23's failure shape). Which behaviour a
   real model shows here is unmeasured.
3. **WAIT is honest but coarse:** 107–109 ms act (100 ms fixed
   sleep), zero settle after it (`after_input` stays unset, as
   designed), so async-reveal polling costs ≈112 ms/step with
   ~5 ms of snapshot work per poll. The 700 ms reveal took
   4 waits + 1 click (493 ms of steps; the reveal timer also
   ran during the ≈321 ms setup — of which ≈290 ms is the
   constructor's bounded re-observe on an initially
   element-empty page, the #1 empty-first-observation retry;
   the scroll page's setup shows the same ≈325 ms for the same
   reason: below-fold/undisclosed controls are not elements
   yet). Setup remains excluded from step time, as upstream,
   but on initially-empty pages it is not free.

Net effect on the earlier verdicts: none change. Settle-class
waiting dominates every new path even harder than the baseline
(act+settle 92–96% as measured with doubles), and the absolute
terms to quote per step are now: click/fill ≈33 ms, scroll
≈50 ms (3,076 ms at a boundary, once), wait ≈112 ms, combobox
fill ≈ suggestion delay + ~30 ms up to the 208 ms cap — plus
whatever the decider costs.

### Boundary early exit, implemented (same day)

Follow-up research before implementing (upstream tracker +
external projects + CDP/Chromium primaries) narrowed the shape:
#212/#209 (both still unmerged) prove scroll progress only
post-hoc and contain no commit-timing discussion at all; an
80-item tracker search found boundary logic only at offer-time
or decision-time, never in an executor wait (closest: withdrawn
#196's remaining-distance test); every surveyed automation
project's wheel is fire-and-forget (Playwright documents it
verbatim) with "finished" proven element-centrically, never by
offset polling; and CDP `dispatchMouseEvent` resolves on ACK
before DOM dispatch, so an at-limit exit can only ever claim
"cannot move further that way", never "this wheel committed" —
which is sufficient, because at the limit no scroll/scrollend
event will ever arrive to prove more.

Implemented in that narrowed form (`SCROLL_POLL` in
browser.py): each poll atomically reads
`[scrollY, scrollHeight − innerHeight, visualViewport.scale]`;
two consecutive polls at the directional limit (snapshot.js's
own 2 px offer gate; `y ≤ 0` upward) end the wait early.
Pinch-zoom (scale ≠ 1), a growing page between polls, and every
not-at-limit state keep the full 3 s deadline, and movement
exits exactly as before. Pinned at the fake-CDP seam (hit,
direction, two-poll stability, growth revocation, zoom
fall-through) and live (boundary observe well under the
deadline; deep-scroll loop unchanged).

Same-setup harness, before → after
([latency-measurement-boundary.json](latency-measurement-boundary.json);
before = unmodified `main` re-run the same day):

| Metric | Before | After |
|---|---:|---:|
| boundary scroll observe wall p50 (n=5) | 3,076 ms | **108 ms** |
| moving scroll settle p50 (n=24) | 34.0 ms | 34.1 ms |
| cdp-scroll loop step p50 (n=75) | 49.51 ms | 49.85 ms |
| cdp-catalog / fixture step p50 | 33.4 / 33.3 ms | 33.5 / 33.4 ms |

The loop rows are expectedly flat: the snapshot never offers a
boundary scroll, so ordinary runs never paid the deadline; the
saving lands exactly where the cost was — boundary probes and
unoffered scroll dispatches now cost ~108 ms (settle promise +
two polls + snapshot) instead of ~3.08 s. What remains at the
full deadline, deliberately: a wheel swallowed by a nested
scroller while the root viewport could still move (unprovable
from root offsets), and any commit later than 3 s.

### Nested-swallow detection (follow-up, same day)

The remaining case above was researched against the
Playwright, Puppeteer and Stagehand trackers plus Chromium's
own before anything was written:

- **Playwright** — `mouse.wheel` is the same fire-and-forget
  CDP dispatch used here (`crInput.ts: wheel()` sends
  `Input.dispatchMouseEvent` and returns); no commit wait, no
  swallow detection. Its only nested-scroller issue of note
  (#41421) is click auto-scroll (`scrollIntoViewIfNeeded`),
  a different mechanism.
- **Puppeteer** — identical shape; wheel tracker items cover
  adding `mouseWheel` support (#4119) and a `wheelDelta` value
  bug (#7265), nothing about consumption or completion.
- **Stagehand** — issue #276 ("Múltiple scrollable elements")
  is the inverse problem: pages whose main scroller is an
  inner div are invisible to it because it assumes
  `window.scrollTo`. The thread's workaround is Playwright's
  `page.mouse.wheel` + a DOM-settle wait; nobody there attempts
  to prove which scroller consumed a wheel.
- **Chromium** — a wheel is hit-tested to the innermost
  scroller under the point and *latched* to it; the delta
  chains to ancestors only when that scroller reaches its
  limit and `overscroll-behavior` permits bubbling (crbug
  41378182, "Respect overscroll-behavior when determining
  scroll bubbling"; 41371072 extends it to all scroll
  containers). No CDP or DOM surface reports which scroller
  consumed a given wheel — the dispatch ACK still precedes
  DOM handling.

**Conclusion: nobody has a general signal; one sub-case is
provable, the mirror is not.** If a scroller in the ancestor
chain under the fixed wheel point (550, 650) actually moves in
the scrolled direction, the wheel *was* processed — the same
strength of evidence root movement already gives. If that
scroller is already at its own limit under
`overscroll-behavior: contain`/`none` (or a non-passive
`wheel` listener calls `preventDefault`, or the scroller lives
in an iframe/shadow tree invisible from the top document),
nothing moves anywhere, ever — observationally identical to a
root commit that is merely late, which is precisely the state
the 3 s wait exists to protect (#212/#209). Any "proof" there
would have to re-derive Chromium's compositor latch decision
from DOM state (`elementFromPoint` ancestry + per-scroller
limits + computed overscroll-behavior), an approximation a
`preventDefault` handler or a smooth-scroll animation
silently falsifies; a false positive reintroduces the
mid-page scroll drop the wait was built to prevent. So the
undecidable remainder keeps the deadline, by design.

Implemented (`SCROLL_CHAIN_PROBE` in browser.py): immediately
before dispatch, a read-only evaluate records the scrollable
ancestor chain under the wheel point with its offsets;
`SCROLL_POLL` now also returns the chain's largest down/up
movement since, and movement in the scrolled direction ends
the wait. The snapshot still records the root as unmoved, so
#209's no-op drop proceeds exactly as after a full wait —
only the 3 s is saved. Pinned at the fake-CDP seam (chain
hit, direction guard, silent-chain fall-through, probe
failure still dispatching) and live (swallow exits fast with
the root unmoved; at-limit-under-`contain` pays the deadline
in full). Counterfactual: with browser.py reverted,
the live swallow test fails at the deadline (≈4 s wall).

Same-setup harness, mechanisms only (nested scenario added to
`micro_cdp_mechanisms`; boundary row re-measured as a control):

| Metric | Before | After |
|---|---:|---:|
| nested-swallow observe wall p50 (n=5) | ≈3,076 ms (deadline) | **35 ms** |
| boundary scroll observe wall p50 (n=5) | 108 ms | 108 ms |
| moving scroll observe wall p50 (n=24) | — | 36 ms |

What still pays the full deadline, deliberately: the
undecidable mirror above, pinch-zoom, growing pages, and any
commit later than 3 s.

### Deadline audit: was anything shortened, and is 3 s the right order? (2026-11-22)

Audit trigger: the subsection above first shipped saying the
`contain` live test pays "a shortened deadline" — against
this port's own bar (never shorten on macOS numbers; the bar
is a Linux CI commit-lag distribution). Findings:

- **The product was never shortened.** `SCROLL_SETTLE_TIMEOUT`
  is 3.0 s at every commit; the only shortening that ever
  existed was a 1.0 s monkeypatch inside that one live test,
  which proved merely "no early exit within 1 s" and read
  like a product change. It does not meet the bar as evidence
  for anything, and is removed: the test now pays the real
  3 s (asserts ≥2,900 ms).
- **The Linux distribution now exists.** A new live test
  (`test_live_scroll_commit_lag_stays_well_under_the_deadline`)
  measures dispatch → first visible `scrollY` change over 40
  wheels and prints `[scroll-commit-lag]` into every CI live
  log; the product wait starts after the post-input settle,
  so the numbers overstate what the deadline must absorb.
  First Linux sample (CI run 45021449949, ubuntu-latest,
  Chrome 157.0.8103.78 — the version from the original
  failure): **n=40, p50 47.4 ms, p95 998.9 ms, max
  1,015.7 ms**. Corroborated by the next two Linux runs the
  same day (45021780010, 45021785838): p50 32.8/33.8 ms,
  p95 998.9 ms in both, max 998.9/999.1 ms — the ≈1 s value
  repeats to the millisecond across runs, consistent with a
  throttled ~1 Hz frame cadence on the owned background tab
  (inferred from the quantization, not independently
  traced), and in any case a stable property of the gate
  that produced the original failure, not runner noise. Same-test macOS sample: p50 32.7 ms, max
  33.6 ms — a 30× smaller tail, which is the whole point of
  the bar: macOS numbers would have "justified" a 100 ms
  deadline that Linux violates on ~5% of wheels, and the
  floated 0.75–1 s shortening sits *inside* the observed
  Linux distribution. **Shortening is rejected on evidence,
  not caution.**
- **Is 3 s even the right order of magnitude?** Chromium
  bounds nothing here. A wheel over a non-passive handler
  region "might be blocked on the main thread waiting for
  non-passive event handlers to process the wheel/touch
  events" (`cc/input/main_thread_scrolling_reason.h`,
  `kWheelEventHandlerRegion`); the compositor's offset
  reaches `window.scrollY` only on a main-thread commit; and
  a hidden page may receive no BeginFrame at all, deferring
  that commit indefinitely. So legitimate lag spans ~10¹ ms
  (a frame or two, typical), ~10³ ms (observed idle-headless
  Linux tail), and unbounded under a busy main thread (any
  long task delays both the handler verdict and the commit).
  No finite deadline is a proof — it is headroom against a
  misrecorded no-op. At 3× the worst lag Linux CI has
  produced and ~60× its p50, 3 s is the right order
  (10⁰ s): 0.1 s would misfire routinely on Linux, 30 s
  would make every genuine no-op cost a third of a minute
  for no added safety against an unbounded tail. **Keep
  3.0 s.** If this is ever revisited, the re-opener is the
  `[scroll-commit-lag]` line accumulating in CI logs — many
  runs, not one — showing max ≪ a proposed value, plus a
  loaded-main-thread sample, which the idle gate does not
  measure.

## Limits — what this does not show

- **No real decide/text model was run for the tables above.** All
  decide/text rows there are offline doubles (≈0 ms) plus
  arithmetic substitution of published latencies. **Closed
  2026-11-22:** the harness gained `--decider local-d1` and the
  ADR-001 gate ran end to end — decide p50 8,056 ms over acted
  steps, gate **FAILED**; see the follow-up at the end of this
  document and [d1-mps-smoke.md](d1-mps-smoke.md). The text helper
  is still a double in every run (real-model TYPE_TEXT latency
  remains unmeasured).
- **Local files only.** No DNS/TLS/network, no server latency, no
  bot mitigation, tiny DOMs (8–22 elements vs real pages). Settle
  on the live web will be larger and more variable; upstream's
  67 ms average post-action gap on Flights is the nearest anchor.
- **Scroll/wait/combobox paths: measured since** (see Settle
  paths below), but on single-purpose inline pages — one tall
  page, one 700 ms reveal, suggestion delays of 0/120/150/300 ms.
  Compound real-page behaviour (scroll + late autocomplete +
  network at once) is still unmeasured.
- **One machine, headless, screenshots off.** Settle tracks frame
  cadence; a 60 Hz or throttled-background tab will differ.
  Screenshot-on observe cost is unmeasured.
- **Sample sizes:** 15 runs/config on CDP (75–90 acted steps) is
  enough for stable p50/p95 on these tight distributions
  (settle p95−p50 ≈ 2–3 ms) but p99/max are indicative only.
  Static: 30 runs (180–240 steps), micro n=100.

## Follow-up: cheap `fresh()` — static kept, CDP probe reverted (2026-11-22)

Both halves of the candidate DEVIATION above were built and
measured with this same harness, back to back on the same machine
(before = unmodified `main`, re-run that day; it reproduces the
committed table above within noise):

- **Static:** `_build()` leaves its (marker, page_key, guards)
  behind; `act()`/`_load()` — the only mutation paths — clear
  them; `fresh()` answers from the cache and rebuilds only when
  it is empty. `observe()` still rebuilds always.
- **CDP (interim, reverted):** `snapshot.js` stored its marker +
  field-state JSON and installed a `MutationObserver` dirty flag;
  `fresh()` first ran a cheap probe (stored marker returned only
  if DOM/scroll/viewport/title/href/fields all still matched)
  and fell back to the full MARKER recompute on any doubt.
  Verdicts were pinned identical at the fake-CDP seam and live
  (DOM mutation, property-only field write, scroll) before
  measuring.

Step time per acted step, before → interim (both halves) → final
shipped (static half only; CDP probe reverted). Final raw data:
[latency-measurement-cheap-fresh.json](latency-measurement-cheap-fresh.json).

| Config | Before p50 / mean | Interim p50 / mean | Final p50 / mean |
|---|---:|---:|---:|
| static-hotel | 1.19 / 1.10 | 0.35 / 0.35 | 0.35 / 0.36 |
| static-catalog | 2.62 / 2.89 | 0.93 / 0.95 | 0.93 / 0.96 |
| cdp-catalog | 33.40 / 34.88 | 33.65 / 35.97 | 33.40 / 34.70 |
| cdp-fixture | 33.41 / 35.25 | 33.73 / 35.69 | 33.35 / 34.60 |

Reading: on the static backend the prediction held and then
some — step p50 fell **65–71%** (snapshot builds/step 3.0–3.5 →
1.0; micro `fresh()` 0.29/0.82 ms → ~0.001–0.002 ms), because a
cache hit costs no rebuild at all. On CDP the mechanism also
worked — snapshot executions/step 3.0 → 1.0, snapshot-stage p50
6.51 → 2.62 ms (catalog), micro `fresh()` 1.24 → 0.47 ms — yet
**step p50 did not move** (+0.7%/+1.0%): each probe is still a
`Runtime.evaluate` IPC round-trip returning the full marker by
value (~0.5 ms, 2–3 per step, landing in `other`), and settle
drifted ≈1 ms against the saving. Freshness got cheaper; the
step did not get faster. Per the standing rule for this change
(no p50 movement → revert), the CDP probe was reverted in full:
upstream's freshness shape is unchanged there, and the 3–4×
snapshot executions per CDP step documented above remain the
current behaviour. The static cache shipped (DEVIATION comment
in `static_dom.py`, pinned in `tests/test_static.py`).

What would actually move the CDP step is not a cheaper probe but
fewer of them — e.g. skipping predict's freshness check when the
page was just observed in the same tick — which changes loop
semantics upstream chose deliberately, and wants its own
argument (and ideally a real-site page where the saving is not
swamped by a 26 ms settle floor). Not attempted here.

## Follow-up: a real decider in the loop — ADR-001 gate FAILS (2026-11-22)

The `--decider`-equivalent wiring the Limits section asked for now
exists (`--decider local-d1`: one shared in-process
`LocalD1Decider`, per-config independent checkers kept), and the
gate ran. Full record: [d1-mps-smoke.md](d1-mps-smoke.md); raw
data [latency-measurement-d1.json](latency-measurement-d1.json)
(static backend, compiled d1-2B on MPS, 2 runs/config, same
machine and harness, tree `perf/d1-mps-smoke` on `691fe6e`).

| Config | Status | Acted steps | Decide p50 / p95 (ms) | Step p50 (ms) | Decide share |
|---|---|---:|---:|---:|---:|
| static-hotel | blocked ×2 (0.5 confidence gate) | 12 | 7,688 / 12,426 | 7,693 | 99.9% |
| static-catalog | done ×2 (checker-verified) | 6 | 15,039 / 22,319 | 15,043 | 100.0% |

**Decide over all 18 acted steps: p50 8,056 ms, p95 22,268 ms —
the ADR-001 < 100 ms p50 gate fails by ~80×** (harness prints the
verdict as `adr001_gate` in every results file now). Every
machinery stage this document measured (≈33 ms CDP step, 26 ms
settle, 3–6 ms snapshot) is below rounding next to it: with this
backend a step *is* its decide call, and the earlier modelled
rows ("here + 102/178 ms substituted") are retired — the
substitution is no longer needed or flattering. Two run notes:
identical 3-action catalog completions took 29 s and 64 s of step
time (decide variance under swap/thermals on the 16 GB Air; see
smoke §5), and the hotel runs are the first real-model firing of
the both-confidences gate. Text remained a double, so TYPE_TEXT
steps still understate a fully-real loop — pessimism the verdict
does not need.
