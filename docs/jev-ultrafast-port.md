# Porting notes: browser-use/jev-ultrafast → `src/account_vault/`

- **Date:** 2026-11-21
- **Upstream:** [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (MIT), `main` at clone time
- **Issues reviewed:** all 57 open issues (210 open items including PRs) via `gh issue list`
- **Rule used throughout:** copy the design where it is sound; where this port
  does it differently, the code carries a `DEVIATION` comment naming the
  upstream issue, and `tests/test_regressions.py` pins the new behaviour.

## What was ported (design kept intact)

| Piece | Upstream | Here |
|---|---|---|
| Loop | `jev_ultrafast/agent.py` (`predict`/`act`/`tick`/`run`) | [agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py) |
| Decide | `jev_ultrafast/model.py` (`action_space`, `choose`, `field_text`) | [model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py) |
| Prompts | `jev_ultrafast/questions.py` | [questions.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/questions.py) |
| Perceive (live) | `jev_ultrafast/snapshot.js` + `browser.py` (CDP) | [snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js) + [browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py) |
| Tests | `tests/test_agent.py` | [test_agent.py](/Users/jagadesh/Documents/Projects/account-vault/tests/test_agent.py) (adapted) |
| Inspector | `jev_ultrafast/demo.py` + `static/` | [demo.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/demo.py) + `static/` (ported 2026-11-21, second step; live mode = upstream, plus offline mode) |

Kept deliberately unchanged: the indexed action space (model emits only an
operation + element index, never selectors/code), one speculative-fan-out
request per cycle with only the selected operation's target head consumed,
strict `validate_choice`, consume-once decisions, semantic freshness checks
immediately before input, no screenshots in the loop by default, no retry
of any browser mutation, and independent verification instead of trusting
`DONE`.

Not ported: the Google Flights example (its hardcoded 2026-09-20 goal
date is itself broken — issues #93/#156; the inspector's copy of that
goal now computes a future date) and the recording/render scripts.
The inspector web demo was ported in the second step (see table).

## Known-broken upstream design: what the open issues say, and what we did

### Fixed in this port (each with a `DEVIATION` comment + regression test)

| Issue | Upstream problem | This port |
|---|---|---|
| [#132](https://github.com/browser-use/jev-ultrafast/issues/132) | Decisions resolved by positional id (`e3`) only; a different control inheriting the id gets clicked with no error | Decision carries the stable `node`; `act` requires id **and** node to match, else `StalePage` |
| [#216](https://github.com/browser-use/jev-ultrafast/issues/216) | `SELECT` executes `e.value = …`; the setter picks the first matching option (disabled included), so duplicate values pick the wrong row | Snapshot records `option_index`; execution sets `selectedIndex` on that exact option |
| [#215](https://github.com/browser-use/jev-ultrafast/issues/215), [#227](https://github.com/browser-use/jev-ultrafast/issues/227) | `date/time/datetime-local/month/week/range/color` inputs map to `null` role and never reach the element table — unfillable; once enumerated, `Input.insertText` still commits nothing into segmented native controls, so fills silently submit empty (drift P0.1, the #227 fill half) | Mapped to fillable roles with `input_type` preserved (both backends); fills for `NATIVE_FILL_TYPES` commit through the native value setter in the act resolver (browser stays format authority; `color`/`range` shape-checked first; a rejected value restores the old one and raises a loud `RuntimeError`, never a retryable stale). Static mirror returns identical verdicts via `valid_native_value`. Pinned at the fake-CDP seam, live in `test_live_cdp.py`, and offline in `test_static.py` |
| [#218](https://github.com/browser-use/jev-ultrafast/issues/218) | Selector matches only `[contenteditable="true"]`; bare/`""` editors never enumerated | Selector matches `[contenteditable]`; `isContentEditable` confirms |
| [#23](https://github.com/browser-use/jev-ultrafast/issues/23) | Bare `li/div/span` suggestion rows with click handlers are invisible; loop retypes forever | `[onclick]` elements enumerated as `button`; second pass (drift P1.9, closed 2026-11-22) adds `addEventListener` rows (`cursor:pointer` + overlay/list context, row-collapsed, capped at 40), icon-only in-flow controls named from svg evidence, and label proxies keeping a concealed radio/checkbox's role + `checked` — both backends, pinned offline (`test_static.py`) and live (`test_live_cdp.py`) |
| [#191](https://github.com/browser-use/jev-ultrafast/issues/191), [#198](https://github.com/browser-use/jev-ultrafast/issues/198), [#202](https://github.com/browser-use/jev-ultrafast/issues/202) | Text-helper context has no element identity/scope; same-labeled fields are indistinguishable and the stale-retry cache can type a value into the wrong field | Context carries `node/dom_id/name/placeholder/input_type/scope`; cache key includes `node` (cloned `main` still keys only on context equality) |
| [#203](https://github.com/browser-use/jev-ultrafast/issues/203) | Documented `{"text": null}` ("value missing") rejected as generic malformed payload | Distinct `MissingValueError`; agent stops as `blocked` with the reason kept |
| [#157](https://github.com/browser-use/jev-ultrafast/issues/157) | One transient text-model failure (e.g. markdown-fenced JSON) aborts the whole run | Fence stripping + bounded retry with backoff (helper calls only, never mutations) |
| [#158](https://github.com/browser-use/jev-ultrafast/issues/158), [#87](https://github.com/browser-use/jev-ultrafast/issues/87), [#133](https://github.com/browser-use/jev-ultrafast/issues/133) | `post_json` trusts status 200: error-body-in-200 passes as success; non-JSON leaks `JSONDecodeError`; off-envelope bodies leak `KeyError` | All three become named `ModelError` ("no action executed"); envelope checked in `parse_choose_response` |
| [#241](https://github.com/browser-use/jev-ultrafast/issues/241), [#25](https://github.com/browser-use/jev-ultrafast/issues/25), [#125](https://github.com/browser-use/jev-ultrafast/issues/125) | Decisions endpoint hardcoded to `api.typesafe.ai`; gateway/local users locked out (401) | `SYSTEMONE_BASE_URL` + `SYSTEMONE_PATH` + decider object (ADR-001: local d1-2B default, hosted Jev by env) |
| [#36](https://github.com/browser-use/jev-ultrafast/issues/36) | Missing `TEXT_MODEL_API_KEY` crashes mid-run; code default (DeepSeek) ≠ `.env.example` (OpenRouter) | Single `Settings` source matching `.env.example`; startup validation before any mutation |
| [#217](https://github.com/browser-use/jev-ultrafast/issues/217) | Hand-rolled `.env` parser keeps quotes/comments/`export ` → misleading provider 401s | `python-dotenv` (`load_settings`), regression-tested with a quoted key |
| [#94](https://github.com/browser-use/jev-ultrafast/issues/94) | Failed post-action observation leaves `page_changed=None`; `is False` check disables the no-progress stop and the run burns model calls | `None` counts as no observed progress toward the 3-repeat stop |
| [#94](https://github.com/browser-use/jev-ultrafast/issues/94)/[#55](https://github.com/browser-use/jev-ultrafast/issues/55) (loop level, found by probing this port) | A decision whose target is missing raises `StalePage`, records no history, so no stuck-run guard can fire: the loop re-asked 120 times then crashed out of `run()` with the budget `ValueError` | 3 consecutive never-executed decisions end the run `blocked` (+`blocked_reason`); budget stops set `blocked` before raising so `run()` finishes cleanly; decision shape validated pre-mutation ([#133](https://github.com/browser-use/jev-ultrafast/issues/133)) |
| [#1](https://github.com/browser-use/jev-ultrafast/issues/1) | First observation can be empty (readyState race) → instant `BLOCKED`, no retry | Bounded re-observe while the element table is empty, before ever deciding |
| [#120](https://github.com/browser-use/jev-ultrafast/issues/120) (completed 2026-11-22; first cut gated operation confidence only and defaulted off — shipped effect zero, see [decide-accuracy-ceiling.md](decide-accuracy-ceiling.md) §3.4) | Confidence recorded but never gates ordinary actions | `min_confidence` gate on **both** operation and target confidence (upstream [PR #127](https://github.com/browser-use/jev-ultrafast/pull/127) gates both the same way), default **0.5** (TypeSafe routing-example floor, to tune per model per ADR-001 Decision 3; `0.0` disables): re-observe once, then `blocked` with the failed side(s) named + `on_uncertain` event (`below_confidence_floor`). ADR-001's optional hosted re-ask is the caller's hook, never an automatic second call |
| PR [#99](https://github.com/browser-use/jev-ultrafast/pull/99) (no issue; adopted 2026-11-22 in stronger form) | `DONE` accepted after a freshness check alone, so a confident false `DONE` (PR's 0.40 example) is reported as success on the real-decider path; independent verification lived only outside the loop (`__main__.verify`, the double's `done_when`) | Code owns completion: `Agent(done_when=...)` (or the `HeuristicDecider`'s own checker, adopted automatically) is asked in `predict` and after every action (`done_by="code"`); a model `DONE` it does not confirm — or any `DONE` with no checker configured — ends `blocked` as unverified, never `done`. Stronger than PR #99 (which still trusts a confident `DONE`, double-vote below 0.7); checker exceptions propagate instead of PR #99's swallow-as-not-done. Not taken: PR #99's first-`BLOCKED` wait/re-observe, stale-terminal repeats, navigation state, and run tooling |
| [#55](https://github.com/browser-use/jev-ultrafast/issues/55) | `MAX_STEPS * 2` model-call budget undocumented; error names no number | `MAX_ACTIONS=60` / `MAX_DECISIONS=120`, both named in errors and tested |
| [#16](https://github.com/browser-use/jev-ultrafast/issues/16) (part) | `httpx.Client` built at import time crashes with SOCKS env vars; browser import requires harness; harness attaches to the user's everyday Chrome by default | Lazy HTTP client and lazy `browser_harness` import; offline path imports cleanly anywhere. Attach refuses by default without a pinned dedicated endpoint (`BU_CDP_URL`/`BU_CDP_WS`) — everyday-profile driving needs explicit `ACCOUNT_VAULT_ALLOW_REAL_BROWSER=1` and warns (drift P0.2). Residual: the first model call still raises `ImportError` under a SOCKS proxy without `socksio` (`httpx[http2]` ships no `socks` extra; drift P1.2) |
| [#11](https://github.com/browser-use/jev-ultrafast/issues/11) | Text helper always sends a `reasoning` field some providers reject (HTTP 400) | Reasoning driven only by config; `TEXT_MODEL_REASONING=omit` sends nothing |
| [#247](https://github.com/browser-use/jev-ultrafast/issues/247) | Page-changing navigation cycle (`sort → index → Coffees`, 44 actions): every step changes the page so repeat/unchanged guards never fire. **No upstream fix to adopt** — issue open at HEAD `cf3c406`, no linked PR (adjacent [#245](https://github.com/browser-use/jev-ultrafast/pull/245)/[#209](https://github.com/browser-use/jev-ultrafast/pull/209)/[#212](https://github.com/browser-use/jev-ultrafast/pull/212) address cousins, not cycles) | The comment thread's drop-then-block, source-aware: identical transition (source URL, kind, label, text, landing URL) 2× in the last 12 eligible actions → control dropped for one decision on that source page; at `max_cycle_repeats` (3) → `blocked` + reason. Same rework adopts [#209](https://github.com/browser-use/jev-ultrafast/pull/209)'s no-op drop (fingerprint-scoped) + episode cap `MAX_NO_PROGRESS=12`. Earlier cut keyed only on (label, landing URL) and blocked with no recovery turn — it also blocked legitimate hub-and-spoke browsing (pinned in `tests/test_regressions.py`) |
| PR [#245](https://github.com/browser-use/jev-ultrafast/pull/245) (no issue) | A control covered at act time is refused before input, records no history, and is reoffered identically on re-observe — decision-layer drops (which learn only from executed actions) can never fire; locally the run died as `blocked` on the stale-streak stop while a hittable runner-up sat unchosen | Snapshot remembers the rejected node and omits it until its centre is hittable again (marker unchanged, only rejected nodes rechecked); distinct `CoveredTarget` (a covered SELECT re-observes instead of reading as an unconfirmed mutation); cached page reads unfresh until re-observe (the manual predict/act hole in #245's review); covered learnings that change the offer do not spend stale strikes. Not taken: PR [#137](https://github.com/browser-use/jev-ultrafast/pull/137)'s hit-test-every-candidate at every snapshot (+fragment fallback for wrapped links) — #245 learns only from actual refusals |
| PR [#212](https://github.com/browser-use/jev-ultrafast/pull/212) + PR [#209](https://github.com/browser-use/jev-ultrafast/pull/209) interaction (found by the CI live gate probing this port) | A dispatched `mouseWheel` commits its offset asynchronously; on an idle headless/background tab the post-input observe (2 rAF / 50ms settle) read `scrollY` unchanged, so a landed scroll recorded as a no-op, #209's drop removed `scroll_down` mid-page, and #212's proven-progress rule could never re-offer it — the deep-scroll live test blocked on Linux Chrome 157 (reads `0→560`, `560→560`; a later live poll showed `1120`) while passing on macOS | Observe after a scroll polls `scrollY` until it moves in the scrolled direction (3s deadline, `SCROLL_SETTLE_TIMEOUT`) before snapshotting; a genuine boundary no-op pays the deadline once and the existing no-op machinery proceeds unchanged. Pinned offline at the fake-CDP seam and live by the CI `live (headless Chrome)` gate. Its boundary cost, measured later at 3.08 s per probe, and the costed options for reducing it are recorded under *Measured costs of this port's own fixes* below |
| PR [#246](https://github.com/browser-use/jev-ultrafast/pull/246) (no issue; unmerged at adoption) | `name()` falls back to child text for everything but `INPUT`; a `<textarea>`'s child text is its *default value*, so an unlabeled prefilled textarea is named by stale (possibly template-sized) content, `title`/`placeholder` never apply, and the label disagrees with the value after any edit | Upstream's one-line exclusion (`INPUT`+`TEXTAREA`) verbatim in `snapshot.js`; static mirror excludes `textarea` in `_name` and computes `<label>` text without the field's own subtree (snapshot.js gets that from its seen set), so wrapping-label textareas — and wrapped selects, previously `"Category All Design"` where the CDP backend said `"Category"` — name identically on both backends. Pinned offline (`test_static.py`) and live (`test_live_cdp.py`, upstream's guard-check shape) |
| [#5](https://github.com/browser-use/jev-ultrafast/issues/5) | Inspector reads static assets without encoding → Windows UTF-8 failure | Explicit `encoding="utf-8"` in [demo.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/demo.py) (same fix as upstream PR [#208](https://github.com/browser-use/jev-ultrafast/pull/208)) |
| [#14](https://github.com/browser-use/jev-ultrafast/issues/14) (drift P0.4, with PRs [#97](https://github.com/browser-use/jev-ultrafast/pull/97)/[#19](https://github.com/browser-use/jev-ultrafast/pull/19)) | `Page.captureScreenshot` runs unguarded inside observe on the harness's 5s default: one stalled capture (`timed out after 5s waiting for the daemon`) kills the run at startup, and no CDP call carries an explicit deadline | Explicit IPC deadlines (`_deadline`): session calls get 30s (`CDP_RESPONSE_TIMEOUT` — a deadline, never a retry reason; a timed-out mutation propagates for inspection), screenshots keep 5s (`SCREENSHOT_RESPONSE_TIMEOUT`) and a capture timeout degrades that observation to `screenshot=None`, preserving the structured read; non-timeout failures still propagate. Branch `codex/bu-bench-eval-v2` (`a2676ee`/`6cf62ca`) diffed before adopting. Pinned at the fake-CDP seam in `test_browser_cdp.py` |

### Known upstream problems deliberately NOT fixed here (out of scope, still true)

- **Decision accuracy is the ceiling.** [#248](https://github.com/browser-use/jev-ultrafast/issues/248)
  replays 1,500 Mind2Web steps through unmodified `choose()`: Jev 1.14 gets
  59.9% step accuracy. The loop faithfully executes a wrong choice; only
  the independent verifier catches it. [#247](https://github.com/browser-use/jev-ultrafast/issues/247)
  and [#67](https://github.com/browser-use/jev-ultrafast/issues/67) are live
  looping/simple-task failures. Our ADR-001 validation gate exists for
  exactly this reason. (The *spend* from looping is now bounded here by
  the cycle stop above; the underlying wrong-choice accuracy is not.)
- **Windows background-tab painting** ([#51](https://github.com/browser-use/jev-ultrafast/issues/51)):
  `Page.bringToFront` reportedly fixes modal occlusion; not applied — the
  CDP backend is untested on Windows here.
- **Tab lifecycle** ([#223](https://github.com/browser-use/jev-ultrafast/issues/223)
  `keep_open`): not implemented — the owned tab still closes with the
  run. (The [#16](https://github.com/browser-use/jev-ultrafast/issues/16)
  attach default and [#14](https://github.com/browser-use/jev-ultrafast/issues/14)
  screenshot deadlines formerly listed here are fixed above.)
- **Hosted-API realities**: HTTP 451 region blocks
  ([#224](https://github.com/browser-use/jev-ultrafast/issues/224)/[#225](https://github.com/browser-use/jev-ultrafast/issues/225)),
  client-link latency from Japan ([#85](https://github.com/browser-use/jev-ultrafast/issues/85)).
  The local-backend option mitigates; nothing in the loop can fix them.
- **Perception limits inherited from `snapshot.js`**: shadow roots, frames,
  canvas, uploads and full accessible-name computation remain unsupported
  (upstream `docs/design.md` Boundaries); [#149](https://github.com/browser-use/jev-ultrafast/issues/149)
  (flattened 6,000-char visible text loses structure) is still open design
  territory. ADR-001 reserves Mirage for this fallback.
- Demo-UX, docs and community issues ([#159](https://github.com/browser-use/jev-ultrafast/issues/159),
  [#54](https://github.com/browser-use/jev-ultrafast/issues/54), [#53](https://github.com/browser-use/jev-ultrafast/issues/53),
  [#115](https://github.com/browser-use/jev-ultrafast/issues/115), [#26](https://github.com/browser-use/jev-ultrafast/issues/26),
  [#187](https://github.com/browser-use/jev-ultrafast/issues/187), showcase
  posts) do not affect the core loop and were not acted on.

## New in this port (no upstream equivalent)

- **`StaticBrowser`** ([static_dom.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/static_dom.py)):
  the same observe/fresh/act contract over a parsed static HTML file
  (stdlib only, no JavaScript execution). Fixtures declare submit
  behaviour with `data-reveals`/`data-results`/`data-template`.
- **`HeuristicDecider` + `MappingTextProvider`**
  ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py)):
  offline doubles with the real decide/text contracts, so the full loop
  runs in tests/CI with no network or API key. `DONE` still requires the
  caller's independent `done_when` evidence.
- **`scripts/latency_harness.py`** + [latency-results.md](latency-results.md):
  per-stage loop timing (snapshot/settle/observe/decide/act) against
  upstream's published performance numbers and the TinyFish field
  report, so speed claims here are measured, not inherited.
- **`python -m account_vault`** ([__main__.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/__main__.py)):
  runs the loop against [examples/hotel_search.html](/Users/jagadesh/Documents/Projects/account-vault/examples/hotel_search.html)
  (which exercises the date/contenteditable/duplicate-option fixes) and
  verifies the outcome independently. `--decider systemone` drives the
  same file with the real backend from `.env`.

## Measured costs of this port's own fixes

### Boundary scroll: one observe can cost 3.08 s (the #212/#209 scroll-commit wait)

The latency harness (`scripts/latency_harness.py`,
[latency-results.md](latency-results.md)) priced the scroll-commit
wait in the Fixed table above
(`SCROLL_SETTLE_TIMEOUT = 3.0` in
[browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py)):
a wheel dispatched at the page boundary makes the following
`observe()` take **p50 3,076 ms** (n=5, direct browser drive) —
roughly 90 ordinary steps (33 ms p50) inside a single
observation. Moving scrolls are fine: settle p50 34 ms / p95
51 ms (n=84 across loop + micro runs), because the poll exits as
soon as `scrollY` moves. Provenance matters for what may change:
the 3 s value is *this port's* choice, not upstream text.
Upstream [PR #212](https://github.com/browser-use/jev-ultrafast/pull/212)
contributes proven-progress scroll state and
[PR #209](https://github.com/browser-use/jev-ultrafast/pull/209)
the no-op drop; their interaction on idle headless tabs (landed
scroll misread as no-op → dropped mid-page → blocked) is what
forced a commit wait here, and 3 s was picked as
deadline-not-delay headroom over the observed hundreds-of-ms
commit lag (Linux headless Chrome 157 evidence in the Fixed row),
with no measured upper bound on that lag.

When the full deadline is actually paid: the decider can only
pick `scroll_down`/`scroll_up` when the snapshot offered it
(`scrollY + innerHeight < scrollHeight − 2`, resp.
`scrollY > 0`), and a scroll that moves at all — including one
that lands exactly on the boundary — exits the poll at once.
The 3 s therefore needs a wheel that never moves the main
viewport despite the offer: content height changed between
observe and act; the fixed wheel point (550, 650) lands on a
nested scroller or modal that consumes the wheel; or the commit
is swallowed/later than the deadline — the failure the wait
exists for, indistinguishable from a boundary until the deadline
passes. Direct `act()` callers dispatching an unoffered scroll
pay it too. After payment, the unchanged observation drops the
scroll from the next offer while the fingerprint holds
(#209), so one unchanged page pays once per direction — but any
page change re-arms the offer and can re-pay.

Options, cheapest first. None is adopted yet; this entry exists
so the choice is made from the measurements above, not by
re-litigating the CI failure that created the wait.

| Option | Change | Saves | Costs / risks |
|---|---|---|---|
| Keep 3 s (status quo) | — | — | 3.08 s per boundary discovery (once per unchanged page/direction); spike is invisible unless settle is timed per stage. Correct on every tab speed observed so far |
| **Early exit on provable boundary** | The existing poll evaluate also returns the max scroll offset; exit as soon as the viewport is *at* the limit in the scrolled direction, keep the full 3 s only when movement is still possible but unobserved | The full ~3 s whenever the boundary is provable at poll time (the common genuine-boundary case) | Small: one evaluate shape + fake-CDP/live pins. Does not help the swallowed-wheel case (viewport scrollable, wheel eaten by a nested scroller) — deliberately: that is the case the deadline protects. Needs care with growing pages (sample the limit every poll, as `scrollY` already is) |
| Shorten the deadline (e.g. 3 s → 0.75–1 s) | One constant | ~2–2.3 s per boundary/swallowed probe | Reopens the exact race if real commit lag exceeds the new value; failure mode is an intermittent mid-page `blocked`, platform-specific (the lag evidence is Linux headless; all latency distributions so far are macOS, where moving settles finish ≤51 ms p95). Cheap to write, expensive to justify: needs a commit-lag distribution from Linux CI Chrome before any number is defensible |
| Frame/scroll-event quiescence | Replace the poll with an in-page promise: resolve on `scroll` event + quiet frames, cap ~500 ms | Most of the boundary cost and some moving-scroll cost | `requestAnimationFrame` is throttled or paused on exactly the idle headless/background tabs whose lag forced polling; still needs a timeout fallback to tell boundary from late commit, so the deadline question returns in new dress. Medium complexity in the settle path |
| Move the proof to the agent layer | Observe never waits; a scroll counts as no-op for #209/#212 only after two consecutive unchanged observations | All poll cost, moving scrolls included | Largest blast radius: touches mirrored history/no-progress semantics on both backends, and for one step the decider sees pre-commit scroll state — the window in which upstream's model emitted premature `BLOCKED` (#212's original bug) unless the proven-progress rule also learns an "unconfirmed" state. Regression surface is the cycle/no-progress suite, not one function |
| Programmatic `scrollBy` instead of wheel dispatch | Act evaluates `window.scrollBy(0, delta)`; commit is readable synchronously (forced layout), poll deleted or vestigial | All commit-poll cost | Changes input semantics upstream chose deliberately: `wheel` listeners and scroll-hijacking UIs never fire, the nested scroller under the cursor is bypassed, `scroll-behavior: smooth` makes it async again (poll returns for those pages), and it is the most bot-visible option (TinyFish's settle/bot-visibility failure mode, issue-analysis §6). Needs wheel-listener live fixtures before it is even testable |
| Remember boundaries across fingerprints | Cache `(url, direction, scrollHeight)` boundary hits (browser→agent state); suppress re-probing until height changes | Repeat payments on pages that mutate trivially between probes | New cross-layer state + invalidation rules (infinite-scroll growth must re-arm); helps only repeats, never the first probe. Complementary to the early exit, not a substitute |

Recommendation when this is picked up: the provable-boundary
early exit first — it removes the measured cost where the
boundary is provable and keeps the full deadline exactly where
the evidence needs it (unprovable = possibly just late). Do not
shorten the constant on macOS numbers alone; if a shorter
deadline is ever proposed, the evidence bar is a scroll
commit-lag distribution from the Linux CI Chrome that produced
the original failure (the harness's scroll micro-benchmark under
the live gate would produce it). Whatever lands must keep the
deep-scroll live test green on Linux — that test is the wait's
reason to exist.

**Outcome (2026-11-22): the early exit landed** in the narrowed
form a wider research pass supported — direction-aware,
root-scroller-only, snapshot's 2 px gate, two consecutive stable
at-limit polls, pinch-zoom and growing pages fall through to
the full 3 s, movement exits unchanged (`SCROLL_POLL` in
browser.py, DEVIATION comment there; that research also
established that neither #212 nor #209 — both still unmerged —
discusses commit timing at all, that no tracker item proposes
an executor-side boundary exit, and that every surveyed
automation project's wheel is fire-and-forget, so this remains
port-local ground). Measured on the same harness setup:
boundary observe p50 **3,076 → 108 ms**; moving-scroll and
ordinary loop step p50s unchanged, as predicted (the snapshot
never offers a boundary scroll). Remaining full-deadline case:
a wheel swallowed by a nested scroller while the root viewport
could still move — unprovable from root offsets, kept
deliberately. Numbers and method in
[latency-results.md](latency-results.md).

**Nested follow-up (2026-11-22): the movable-nested half is
now detected; the at-limit half is undecidable and stays.**
A tracker pass (Playwright, Puppeteer, Stagehand, Chromium —
detail in [latency-results.md](latency-results.md)) found no
consumption signal anywhere: every wheel is the same
fire-and-forget dispatch, Stagehand's nested issue (#276) is
the inverse problem, and Chromium latches a wheel to the
innermost scroller under the point, chaining only at its
limit when `overscroll-behavior` allows (crbug 41378182),
with no surface reporting which scroller consumed a wheel.
Provable sub-case: if a scroller in the chain under the fixed
wheel point moves in the scrolled direction, the wheel was
processed — `SCROLL_CHAIN_PROBE` records that chain and its
offsets just before dispatch, and `SCROLL_POLL`'s chain
movement now ends the wait (root still recorded unmoved, so
#209 proceeds identically). Harness: nested-swallow observe
p50 **≈3,076 → 35 ms** (n=5); boundary re-measured 108 ms as
a control. Genuinely undecidable, kept on the full deadline:
a nested scroller at its own limit under `contain`/`none`, a
non-passive `wheel` `preventDefault`, or a scroller inside an
iframe/shadow tree — nothing moves anywhere, identical to a
late root commit, and guessing there reopens the #212/#209
mid-page drop. What we do instead is wait, exactly as before;
the snapshot then records the no-op and the offer drops.

## Verification (2026-11-21)

```bash
.venv/bin/python -m pytest -q        # 109 passed, 4 skipped (live-CDP opt-in; standalone live run: 4 passed)
.venv/bin/ruff check src tests       # clean
node --check src/account_vault/snapshot.js
node --check src/account_vault/static/app.js
.venv/bin/python -m account_vault    # DONE in 6 actions, independent checks pass
uv run account-vault-demo            # inspector; offline mode drives the
                                     # messy static catalog to DONE
ACCOUNT_VAULT_LIVE=1 .venv/bin/python -m pytest tests/test_live_cdp.py -q
                                     # 2 passed: real Chrome (dedicated
                                     # profile) perceives + drives fixture
```

The CDP (live Chrome) path is exercised end to end: a dedicated-profile
Chrome (`BU_CDP_URL`, isolated harness daemon — the user's profile is
never touched) perceives the JS-rendered fixture in full (10 elements,
real JPEG screenshot, vs 3 header links statically — both sides pinned)
and the loop drives it to DONE with independent verification
(`tests/test_live_cdp.py`, opt-in). Inspector HTTP tests drive the
handler in-process, so they pass under sandboxes that forbid socket
binds. Still unexercised: a real decide model behind the wire client
(all live runs use the offline decider doubles) and the Flights task.
