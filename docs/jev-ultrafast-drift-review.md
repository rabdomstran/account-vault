# Port drift review — account-vault vs browser-use/jev-ultrafast

Date: 2026-11-21. Local HEAD `e39201d`. Upstream HEAD `cf3c406` (2026-11-10).
Scope: review only. No code was changed for this document.

This review covers the Python port in `src/account_vault/` against
[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast):
what upstream merged or left open that this port has not already handled,
what upstream changed since that this port should pick up, where local
deviations have aged badly, and what people outside the repo are saying
about this agent loop. Already-handled work is listed only to define the
exclusion set (§8).

## Contents

1. [TL;DR](#1-tldr)
2. [P0 — must pick up](#2-p0--must-pick-up)
3. [P1 — should pick up](#3-p1--should-pick-up)
4. [P2 — evaluate or ignore](#4-p2--evaluate-or-ignore)
5. [Deviations that have aged badly](#5-deviations-that-have-aged-badly)
6. [Deviations to keep](#6-deviations-to-keep)
7. [What outsiders are saying](#7-what-outsiders-are-saying)
8. [Already handled (excluded above)](#8-already-handled-excluded-above)
9. [Recommended pickup order](#9-recommended-pickup-order)
10. [Method, limits, and open gaps](#10-method-limits-and-open-gaps)

## How to read the evidence labels

- **Verified** — a researcher opened and read the local (and, for deviation
  claims, the upstream) code body cited. Search output alone was never
  treated as evidence.
- **Inferred** — code bodies were read, but no live reproduction was run
  (no live Chrome/CDP run, no real decide model, no Windows machine).
  Every inferred claim is flagged inline.
- Effort is a guess for triage only: S (hours), M (a day or two),
  L (multi-day). It is not an estimate to schedule against.
- File citations are `path:line` at local HEAD `e39201d` unless marked
  upstream. Upstream citations are at `cf3c406` unless marked as a branch.

---

## 1. TL;DR

1. **There is no merged-code drift to merge.** Upstream `main` code froze
   at `452c1ad` (2026-09-16). Upstream has exactly 3 merged PRs ever —
   [#30](https://github.com/browser-use/jev-ultrafast/pull/30),
   [#238](https://github.com/browser-use/jev-ultrafast/pull/238),
   [#244](https://github.com/browser-use/jev-ultrafast/pull/244) — all
   README-only, all by `gregpr07`. This port was cloned at upstream HEAD
   `cf3c406`, which *is* the [#244](https://github.com/browser-use/jev-ultrafast/pull/244)
   squash commit, so there is nothing new on `main` to pick up as code.
   Local HEAD `e39201d` (2026-11-21) is **ahead** of upstream `main`: it
   adopted the shapes of still-open upstream PRs
   [#209](https://github.com/browser-use/jev-ultrafast/pull/209) and
   [#245](https://github.com/browser-use/jev-ultrafast/pull/245)
   (commits `e2116a6`, `49b3c35`) plus ~25 open-issue fixes. Upstream
   totals (independently re-checked): 57 open issues + 153 open PRs =
   210 open items, 182 PRs total, 0 closed issues.
2. **The real drift is strategic, not code.** Via
   [#244](https://github.com/browser-use/jev-ultrafast/pull/244), the
   upstream README now declares Jev superseded by Browser Use ultrafast
   mode (`pip install browser-use`, `Agent(mode='ultrafast')`,
   `bu-ultrafast`). The repo is **not** GitHub-archived
   (`isArchived: false`, re-checked) — it is deprecated by README and
   "stays up as the original experiment". Locally,
   [pyproject.toml](/Users/jagadesh/Documents/Projects/account-vault/pyproject.toml:12)
   already depends on `browser-use>=0.8`, but there are zero
   `browser_use` imports in `src/`, `tests/`, or `scripts/`. This port
   hand-maintains the frozen experiment. Whether to delegate to the
   successor or keep maintaining the loop is now the single biggest
   decision; §9 argues it should be made *after* a real-model baseline
   exists, not before.
3. **Deviations are overwhelmingly intentional and healthy.** They are
   fixes for open upstream issues, each marked with a `DEVIATION`
   comment and pinned by a regression test, plus testability/config
   adaptations (`Settings`, `StaticBrowser`, offline doubles). The loop
   invariants from upstream are kept: indexed `op+index` action space,
   one speculative request per cycle, strict `validate_choice`,
   consume-once, freshness + fingerprint check before input, no
   screenshots by default, no mutation retry, independent verification.
   Prompts are byte-identical to upstream (AST-compared), and retry
   parity holds (httpx `timeout=25`, retry only `{429, 529, 503}`).
4. **Residual risk is concentrated in four verified gaps** (§2): CDP
   date-fill silently failing
   ([#227](https://github.com/browser-use/jev-ultrafast/issues/227), fill half),
   attaching to the real Chrome profile by default
   ([#16](https://github.com/browser-use/jev-ultrafast/issues/16)),
   scroll-state blindness
   ([#212](https://github.com/browser-use/jev-ultrafast/pull/212) is
   citation-only locally; upstream shows 0/5 scroll tasks passing on
   `main`), and no CDP/screenshot deadline
   ([#14](https://github.com/browser-use/jev-ultrafast/issues/14),
   [#97](https://github.com/browser-use/jev-ultrafast/pull/97),
   [#229](https://github.com/browser-use/jev-ultrafast/pull/229)).
   On top of those, the real decide model has never been exercised:
   prompts were tuned for hosted Jev, the local default is `d1-2B`, and
   every verified run used offline doubles.
5. **Biggest blind spot: upstream non-main branches were never diffed.**
   `codex/planner-loop` (15 commits ahead of `main`, by `gregpr07`,
   adds `jev_ultrafast/planner.py` ~14.7 KB + `tests/test_planner.py`,
   a bounded subgoal planner with evidence-checked completion, no PR),
   `codex/planner-state-final` (9 ahead), `codex/state-action-hillclimb`
   (7 ahead), and `codex/bu-bench-eval-v2` (4 ahead, including
   `6cf62ca` "keep optional screenshot capture on its original short
   deadline" and `a2676ee` "allow slow remote responses without
   retrying browser input") may overlap or supersede local fixes and
   the P0 timeout item. Treat everything branch-related below as
   evaluate-first.

Headline counts worth remembering while reading: upstream `agent.py` is
174 lines; local [agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:1)
is 568. Upstream has 23 lock packages; local has 128. Upstream has 1 test
file (320 lines); local has 7 files, 113 tests collected (109 passed,
4 skipped as claimed in the port notes). This is a faithful,
well-documented port that has outgrown its upstream — the question is no
longer "what did we miss on `main`?" but "which open PRs, branches, and
successor-API moves are worth owning now that there is no upstream to
rebase onto?".

---

## 2. P0 — must pick up

Four items. Each is verified present in local code today. None is fixed
by merging upstream `main`; each needs a local change (in a future
session — none was made here).

### P0.1 Date inputs do not commit over CDP — [#227](https://github.com/browser-use/jev-ultrafast/issues/227) (fill half)

- Upstream: [issue #227](https://github.com/browser-use/jev-ultrafast/issues/227), 2026-10-21.
- Enumeration is handled: date/time/month/week/range/color are offered
  as fillable in
  [snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js:61)
  and [static_dom.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/static_dom.py:284).
- **Verified gap:** the live fill path uses `Input.insertText` for every
  fill with no special case for `date` / `datetime-local` / `time` /
  `month` — see
  [browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:280)
  (parent spot-check confirmed `call("Input.insertText", ...)` is the
  only text-commit call). The issue states `insertText` does not commit
  native date controls. The offline static path sets the value directly
  ([static_dom.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/static_dom.py:495)),
  so the gap is CDP-only — and therefore invisible to the offline suite.
- Why P0: silent failure. The agent believes it filled a date and
  submits an empty value. Priority HIGH.
- Effort: M — native value-set plus events keyed by `input_type`, with
  a *live CDP* regression (an offline test would pass today and prove
  nothing).

### P0.2 Real-browser attach by default — [#16](https://github.com/browser-use/jev-ultrafast/issues/16) (part a)

- Upstream: [issue #16](https://github.com/browser-use/jev-ultrafast/issues/16), 2026-09-18. Status locally: partial
  (lazy imports landed; attach behaviour did not change).
- **Verified:** [browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:68)
  calls `ensure_daemon()` verbatim, then `Target.createTarget`, with no
  warning or guard (parent spot-check confirmed). That attaches to the
  everyday Chrome and its logged-in profile by default. The dedicated
  profile used under `tests/test_live_cdp.py` is operational practice,
  not code enforcement.
- Why P0: this project is an *account vault*. Driving real accounts in
  the user's daily profile is the highest safety risk in the review.
  External testers flag the same default plus per-step page-text
  exfiltration and opt-out telemetry (§7). Priority HIGH (safety).
- Effort: M — dedicated-profile default or explicit opt-in, plus a
  startup warning when attaching to a non-dedicated profile.

### P0.3 Scroll state for the decider — [PR #212](https://github.com/browser-use/jev-ultrafast/pull/212)

- Upstream: [PR #212](https://github.com/browser-use/jev-ultrafast/pull/212), still open. It adds scroll
  position/directions to the Jev state, `scroll_before`/`scroll_after`
  history, `BLOCKED` suppression while scrolling can continue, and
  prompt text. Upstream evidence in the PR: 0/5 scroll tasks pass on
  `main` because the model returns `BLOCKED` after one scroll.
- **Verified absent locally:** [model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:174)
  sends only `url`/`title`/`text` plus `history[-10:]` — no scroll
  fields. `NEXT_ACTION` is byte-identical to upstream, so the PR's
  prompt lines are absent too. The only local trace of
  [#212](https://github.com/browser-use/jev-ultrafast/pull/212) is a
  `_cycle_key` comment exempting scroll from cycle keys
  ([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:66)).
  One research angle initially filed this PR as handled; the critic and
  a direct code read prove it is **citation-only**. It is counted here,
  not in §8 — do not double-count it.
- Why P0: below-fold targets are systematically unachievable. The loop
  cannot tell "scrolled and making progress" from "stuck".
- Effort: M — snapshot state + history + a prompt delta. Note this is
  the first deliberate break of prompt byte-parity with upstream; it
  needs an ADR note and a real-model check (§9, step 4).

### P0.4 No CDP / screenshot deadline — [#14](https://github.com/browser-use/jev-ultrafast/issues/14), [PR #97](https://github.com/browser-use/jev-ultrafast/pull/97), [PR #229](https://github.com/browser-use/jev-ultrafast/pull/229)

- Upstream: [issue #14](https://github.com/browser-use/jev-ultrafast/issues/14), 2026-09-17;
  [PR #97](https://github.com/browser-use/jev-ultrafast/pull/97) and
  [PR #229](https://github.com/browser-use/jev-ultrafast/pull/229), both open.
- **Verified:** `Page.captureScreenshot` runs unguarded inside
  `observe` ([browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:287);
  parent spot-check confirmed), with `screenshot=True` by default on
  that path. Only `StalePage` is retried
  ([browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:129));
  other `RuntimeError`s propagate. Neither side sets a CDP IPC timeout.
- Why P0: one hung capture hangs or kills the run. Verified code path,
  MED severity on its own, P0 because it is cheap and because an
  upstream-originated fix may already exist (next bullet).
- Effort: S–M — short deadline, degrade to a no-screenshot observe,
  never retry a mutation. **Diff branch `codex/bu-bench-eval-v2`
  commits `6cf62ca` / `a2676ee` before inventing a fix**, so the local
  change does not diverge from the upstream-originated one.

---

## 3. P1 — should pick up

Ordered roughly by payoff. [PR #246](https://github.com/browser-use/jev-ultrafast/pull/246)
is the quick win; [#248](https://github.com/browser-use/jev-ultrafast/issues/248) /
[#67](https://github.com/browser-use/jev-ultrafast/issues/67) gate everything smarter.

### P1.1 Textarea naming — [PR #246](https://github.com/browser-use/jev-ultrafast/pull/246) (do first)

[PR #246](https://github.com/browser-use/jev-ultrafast/pull/246), open, 2026-11-12.
Local [snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js:31)
is identical to upstream `snapshot.js:20`: only `INPUT` is excluded from
child-text naming, so an unlabeled `textarea` is named by its default
value (parent spot-check confirmed the `e.tagName==='INPUT'` line).
Prefilled textareas therefore inject stale, oversized labels into the
decide payload. Effort S — exclude `TEXTAREA` like `INPUT`, mirror in
`static_dom.py`, add a regression.

### P1.2 SOCKS proxy crash (residual) — [#16](https://github.com/browser-use/jev-ultrafast/issues/16) (part b)

The lazy client ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:44))
only defers the crash: it still builds `httpx.Client(http2=True)` and
[pyproject.toml](/Users/jagadesh/Documents/Projects/account-vault/pyproject.toml)
depends on `httpx[http2]` without the `socks` extra, so the first model
call still raises `ImportError` under `ALL_PROXY=socks5` without
`socksio`. MED. Effort S — add `httpx[socks]` or raise a clear
`ConfigError` naming the fix.

### P1.3 Windows background-tab `BLOCKED` — [#51](https://github.com/browser-use/jev-ultrafast/issues/51)

[Issue #51](https://github.com/browser-use/jev-ultrafast/issues/51), 2026-09-19. **Verified:** local creates the
target with `background=True` ([browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:73))
and enables focus emulation, but `Page.bringToFront` appears nowhere.
The port notes admit the fix was not applied and Windows is untested
([jev-ultrafast-port.md](/Users/jagadesh/Documents/Projects/account-vault/docs/jev-ultrafast-port.md:73)).
MED-HIGH overall, effectively P0 for Windows users. Effort S
(`bringToFront` on the Windows path) — but the verdict is **inferred**
from code; it needs a Windows live check before anyone calls it fixed.

### P1.4 Decide-model accuracy ceiling — [#248](https://github.com/browser-use/jev-ultrafast/issues/248) + [#67](https://github.com/browser-use/jev-ultrafast/issues/67)

[Issue #248](https://github.com/browser-use/jev-ultrafast/issues/248) (2026-11-17) reports Jev 1.14 at 59.9% Mind2Web
step accuracy through the unmodified `choose()`;
[issue #67](https://github.com/browser-use/jev-ultrafast/issues/67) (2026-09-20) covers looping/wrong choices.
Locally, `choose` is intact ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:248))
and the [#247](https://github.com/browser-use/jev-ultrafast/issues/247) /
[#209](https://github.com/browser-use/jev-ultrafast/pull/209) guards
([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:472))
bound the *cost* of wrong choices without fixing them. The real model
has never run end-to-end here
([jev-ultrafast-port.md](/Users/jagadesh/Documents/Projects/account-vault/docs/jev-ultrafast-port.md:134)).
There is no upstream code to pick up; the pickup is a metered head-to-head
(hosted Jev vs local `d1-2B`, per ADR-001) on a fixed suite. HIGH as a
capability question. Effort L. Everything in P1.5–P1.8 should be judged
against that baseline, not before it.

### P1.5 Planner branches — evaluate before building anything smarter

No PR, no issue. Branches `codex/planner-loop`,
`codex/planner-state-final`, `codex/state-action-hillclimb` carry the
only upstream-originated reasoning layer (bounded subgoals +
evidence-checked completion), aimed squarely at the P1.4 weakness and
at the external 1/20 long-horizon result (§7). They were **not diffed**
in this review — that is the review's largest known gap (§10). Effort M
to diff/triage, L to adopt. Do not adopt before the P1.4 baseline
exists; there would be no way to tell whether the planner helps.

### P1.6 `keep_open` lifecycle — [#223](https://github.com/browser-use/jev-ultrafast/issues/223)

[Issue #223](https://github.com/browser-use/jev-ultrafast/issues/223), 2026-10-18. **Verified absent:** no
`keep_open` / `--keep-open` anywhere; `Agent.__exit__` always closes
([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:561))
via `Target.closeTarget`
([browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:176)).
MED (debugging/handoff UX). Effort S.

### P1.7 Demo invisible failures — [#159](https://github.com/browser-use/jev-ultrafast/issues/159)

[Issue #159](https://github.com/browser-use/jev-ultrafast/issues/159), 2026-09-28. **Verified largely unhandled:**
"Start demo" only resets, it does not auto-run (`static/app.js:158-166`);
a stopped run raises a bare "This run has stopped"
([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:312));
[demo.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/demo.py:182)
passes raw error strings with no CDP-hint translation, preflight, or
doctor. Partial mitigations exist (`blocked_reason` is shown;
offline mode in [demo.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/demo.py:65)).
MED — the first-run failure is currently undiagnosable. Effort M.

### P1.8 Flattened page text loses structure — [#149](https://github.com/browser-use/jev-ultrafast/issues/149)

[Issue #149](https://github.com/browser-use/jev-ultrafast/issues/149), 2026-09-26. **Verified:**
[snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js:127)
still does `words.join('\n').slice(0, 6000)` over a `TreeWalker`, and
[static_dom.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/static_dom.py:413)
mirrors it. No semantic tree or `ActionRef` structure is sent. MED
(decision quality on structured pages). Effort L — this is a payload
shape change that breaks prompt parity; benchmark it against the
6000-character baseline or do not do it.

### P1.9 Non-`onclick` clickable rows (residual) — [#23](https://github.com/browser-use/jev-ultrafast/issues/23)

[Issue #23](https://github.com/browser-use/jev-ultrafast/issues/23), 2026-09-18, partial. The local fix covers
`[onclick]` only ([snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js:46)).
The issue's suggested narrow second pass (`cursor: pointer` /
positioned layer) for `addEventListener` rows without inline handlers
is not implemented. **Inferred** (no live repro). MED. Effort M — the
pass must stay narrow or the 250-action cap and label noise regress.
Note [#137](https://github.com/browser-use/jev-ultrafast/pull/137)
(hit-test every candidate) was already considered and rejected with
measurement; do not relitigate it as part of this item.

### P1.10 MDN wrong-waypoint repro — [#129](https://github.com/browser-use/jev-ultrafast/issues/129)

[Issue #129](https://github.com/browser-use/jev-ultrafast/issues/129), 2026-09-23. The body is task JSON only, no
trace. Same single-pass failure class as
[#67](https://github.com/browser-use/jev-ultrafast/issues/67);
**inferred** still present. LOW-MED on its own. Effort S to add as an
eval task once the P1.4 harness exists; there is no standalone fix.

---

## 4. P2 — evaluate or ignore

Nothing here needs scheduling. Each entry records a deliberate verdict
so a future reader does not re-triage it from scratch.

### Merged PRs (the whole upstream merged history)

- [PR #244](https://github.com/browser-use/jev-ultrafast/pull/244) —
  "docs: point to Browser Use ultrafast mode", merged 2026-11-11,
  `README.md +9/-2`, commit `9b97bab`. **Evaluate (docs only).** Worth
  one line in the port notes/README recording that upstream is
  superseded and naming the successor API. Zero code: the banner and
  the renamed "original demo/library" headings have no local file to
  patch. Partial awareness already exists
  ([pyproject.toml](/Users/jagadesh/Documents/Projects/account-vault/pyproject.toml:11)
  `browser-use>=0.8` "(ultrafast mode)"; ADR-001 lists `bu-ultrafast`
  as a revisit trigger). Effort S.
- [PR #238](https://github.com/browser-use/jev-ultrafast/pull/238) —
  "docs: replace the Cloud waitlist callout with BU Ultrafast GA",
  merged 2026-10-31, `README.md +2/-2`. **Ignore.** Hosted-Cloud
  marketing, superseded by [#244](https://github.com/browser-use/jev-ultrafast/pull/244),
  irrelevant to a local-first port.
- [PR #30](https://github.com/browser-use/jev-ultrafast/pull/30) —
  "docs: announce the Cloud waitlist in the README", merged
  2026-09-18, `README.md +4/-0`. **Ignore.** Obsolete waitlist, fully
  superseded by the chain
  [#30](https://github.com/browser-use/jev-ultrafast/pull/30) →
  [#238](https://github.com/browser-use/jev-ultrafast/pull/238) →
  [#244](https://github.com/browser-use/jev-ultrafast/pull/244).
  None of the three is recorded as handled anywhere locally (grep:
  zero hits for the numbers outside `uv.lock` hashes, zero "waitlist"
  hits), and none needs applying — only the never-ported upstream
  README ever changed.

### Open issues — evaluate

- [#224](https://github.com/browser-use/jev-ultrafast/issues/224) /
  [#225](https://github.com/browser-use/jev-ultrafast/issues/225)
  (2026-10-19) — HTTP 451 region block on the hosted path. **Evaluate,
  LOW.** Locally the hosted path surfaces a generic `ModelError`
  ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:64))
  with no region hint; the local-default backend mitigates only if the
  user switches. Optional S: a hint in the error text, only if the
  hosted path is retained.
- [#125](https://github.com/browser-use/jev-ultrafast/issues/125)
  (2026-09-23, partial) — local backend + MCP server. Backend half is
  done ([settings.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/settings.py:38));
  no MCP stdio server exists in `src/`. **Evaluate, LOW-MED.**
  [#25](https://github.com/browser-use/jev-ultrafast/issues/25)
  (2026-09-18, partial) — backend abstraction: the decider object
  exists ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:370)),
  the wire shape is still `/v1/systemone`. **Evaluate, LOW.** Decide
  both together with the successor question (§1 item 2), not separately.
- [#187](https://github.com/browser-use/jev-ultrafast/issues/187)
  (2026-10-01) — multi-stage architecture idea. **Evaluate** via the
  planner-branch item (P1.5) instead of as a standalone design.

### Open issues — ignore

- [#115](https://github.com/browser-use/jev-ultrafast/issues/115)
  (2026-09-22) theme switch — **ignore**, trivial, verified absent.
- [#85](https://github.com/browser-use/jev-ultrafast/issues/85)
  (2026-09-21) Japan latency report — **ignore**, informational
  (client link, not a region defect); local backend mitigates.
- [#26](https://github.com/browser-use/jev-ultrafast/issues/26)
  (2026-09-18) Runtime/Jev boundary question — **ignore**, question only.
- [#21](https://github.com/browser-use/jev-ultrafast/issues/21)
  (2026-09-18) how to get a `TYPESAFE_API_KEY` — **LOW docs**, only if
  the hosted path stays documented.
- Noise/showcase, no action (all 2026-09-19…2026-11-10):
  [#242](https://github.com/browser-use/jev-ultrafast/issues/242) opencode plugin,
  [#235](https://github.com/browser-use/jev-ultrafast/issues/235) empty,
  [#233](https://github.com/browser-use/jev-ultrafast/issues/233) garbled praise + selector screenshot
  (root cause undeterminable from the text; inferred),
  [#177](https://github.com/browser-use/jev-ultrafast/issues/177) third-party report,
  [#152](https://github.com/browser-use/jev-ultrafast/issues/152) Jev Social,
  [#145](https://github.com/browser-use/jev-ultrafast/issues/145) jev-browse (ideas partly adopted independently),
  [#140](https://github.com/browser-use/jev-ultrafast/issues/140) /
  [#100](https://github.com/browser-use/jev-ultrafast/issues/100) empty,
  [#77](https://github.com/browser-use/jev-ultrafast/issues/77) workflow spam,
  [#47](https://github.com/browser-use/jev-ultrafast/issues/47) /
  [#46](https://github.com/browser-use/jev-ultrafast/issues/46) relay-diagram spam.
- [#54](https://github.com/browser-use/jev-ultrafast/issues/54)
  upstream README clone-URL error — verified **not** present locally.
  [#53](https://github.com/browser-use/jev-ultrafast/issues/53)
  "minified `agent.py`" — effectively fixed by the 568-line expansion
  plus ruff-format commit `5e63de2`. (One angle cited a ">120 chars"
  line metric for this; the critic showed both sides score 0, so that
  metric proves nothing and is not relied on here.)
  [#93](https://github.com/browser-use/jev-ultrafast/issues/93) /
  [#156](https://github.com/browser-use/jev-ultrafast/issues/156)
  Flights hardcoded 2026-09-20 date — N/A: the example was not ported,
  and the demo computes +30 days (`static/app.js:9-12`).

### Perception boundaries — strategic evaluate

Shadow roots, cross-origin frames, canvas, and file uploads are
unsupported on both sides (no shadow/frame traversal in `snapshot.js`;
`safe()` excludes `file` inputs). Candidate upstream PRs were named but
**not individually assessed** in this review:
[#234](https://github.com/browser-use/jev-ultrafast/pull/234) iframes,
[#189](https://github.com/browser-use/jev-ultrafast/pull/189) `UPLOAD_FILE`,
[#211](https://github.com/browser-use/jev-ultrafast/pull/211) Enter-submit,
[#231](https://github.com/browser-use/jev-ultrafast/pull/231) label-click,
[#199](https://github.com/browser-use/jev-ultrafast/pull/199) /
[#206](https://github.com/browser-use/jev-ultrafast/pull/206) popups.
Each needs its own triage before any pickup. Effort L each. Do not
start any of them before the successor decision (§9 step 4–5).

---

## 5. Deviations that have aged badly

"**Aged**" means: faithful or reasonable at port time, known-wrong or
costly now. Items already ranked in §2–§3 are listed briefly; the new
material is the strategic/dependency set.

1. **Dead successor dependency.** `browser-use>=0.8` is declared
   ([pyproject.toml](/Users/jagadesh/Documents/Projects/account-vault/pyproject.toml:12))
   and never imported. Upstream's README (§1 item 2) makes this a
   standing question: delegate to `Agent(mode='ultrafast')` or drop
   the dependency and own the frozen loop explicitly. The worst option
   is the current silent middle.
2. **Unused heavy stack, missing mission code.** Local dependencies add
   `pydantic`, `cryptography`, `argon2-cffi` (plus optional
   `torch`/`transformers`/`cactus-compute`) — 128 lock packages vs
   upstream's 23. Grep finds zero imports of `browser_use`, `pydantic`,
   `cryptography`, `argon2`, `torch`, `transformers`, or `cactus` in
   `src/`, `tests/`, or `scripts/` (critic-verified). No vault source
   exists despite the project description and the vault-CLI intent of
   initial commit `6abcdb1`. Either implement the vault or prune the
   stack; carrying both the weight and the gap is the aged part.
3. **Password exclusion vs the vault mission.** Local
   [snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js:20)
   and [static_dom.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/static_dom.py:48)
   faithfully inherit upstream's exclusion of password/file inputs
   (upstream `snapshot.js:9`). An account-vault agent that cannot fill
   passwords needs an explicit decision — a secure fill path, or an
   explicit scope-out — not silent inheritance.
4. **Prompt/model mismatch, never tested.** Prompts are byte-identical
   to upstream (tuned for hosted Jev at
   `https://api.typesafe.ai/v1/systemone`) while the local default is
   `d1-2B` at `http://127.0.0.1:8000`
   ([settings.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/settings.py:40)).
   No real decide model has been exercised, so decision-accuracy drift
   is unknown (see P1.4). Byte-parity was a virtue at port time; kept
   untested against a different model, it becomes an assumption.
5. **Inherited bugs that now have known fixes.** Scroll state (P0.3),
   textarea naming (P1.1), and the missing CDP timeout (P0.4) were
   faithful copies at clone time. Once upstream fixes existed as open
   PRs/branches, the faithful copies aged into known-wrong code.
6. **Version caps.** Local `requires-python >=3.12,<3.14` refuses
   Python 3.14 (upstream is uncapped `>=3.12`). Both sides pin
   `pytest>=8.4,<9` while upstream Dependabot
   [#144](https://github.com/browser-use/jev-ultrafast/pull/144)
   already proposes pytest 9.0.3. The shared exact pin
   `browser-harness==0.1.13` is inherited on both sides. Otherwise
   shared lock versions are local ≥ upstream (certifi 2026.10.23 vs
   2026.7.22; ruff 0.17.6 vs 0.16.8) — **no missed upstream security
   bump** flows this way; the pending upstream certifi branch is behind
   local.
7. **Permanent ownership, no rebase target.** ~70 `DEVIATION` comments
   cite upstream issues/PRs that remain open (spot-verified via `gh`:
   [#209](https://github.com/browser-use/jev-ultrafast/pull/209),
   [#212](https://github.com/browser-use/jev-ultrafast/pull/212),
   [#220](https://github.com/browser-use/jev-ultrafast/pull/220),
   [#221](https://github.com/browser-use/jev-ultrafast/pull/221),
   [#245](https://github.com/browser-use/jev-ultrafast/pull/245),
   [#197](https://github.com/browser-use/jev-ultrafast/pull/197),
   [#200](https://github.com/browser-use/jev-ultrafast/pull/200),
   [#207](https://github.com/browser-use/jev-ultrafast/pull/207),
   [#179](https://github.com/browser-use/jev-ultrafast/pull/179),
   [#180](https://github.com/browser-use/jev-ultrafast/pull/180) all
   open). With only 3 PRs ever merged upstream (all docs), there is no
   upstream to rebase onto; those comments *are* the debt ledger.
   Relatedly, upstream `scripts/` (`check_guards`, measure/record/
   render, smoke) was deliberately not ported, so there is no local
   guard/measurement harness to re-verify 7.073s-class claims.

## 6. Deviations to keep

Do not "re-sync" these away. Each fixes an open upstream issue or adds
testability upstream never had; upstream equivalents, where they exist,
are still unmerged.

- **Loop hardening** (upstream 174 lines → local 568):
  `_check_decision` pre-mutation guard
  ([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:46),
  [#133](https://github.com/browser-use/jev-ultrafast/issues/133));
  bounded empty-observe retry `max_empty_observes=5`
  ([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:178),
  [#1](https://github.com/browser-use/jev-ultrafast/issues/1));
  stale-streak bound
  ([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:262),
  [#55](https://github.com/browser-use/jev-ultrafast/issues/55) /
  [#94](https://github.com/browser-use/jev-ultrafast/issues/94));
  optional `min_confidence` gate, default `0.0` = upstream behaviour
  ([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:350),
  [#120](https://github.com/browser-use/jev-ultrafast/issues/120));
  named budgets `MAX_ACTIONS=60` / `MAX_DECISIONS=120` /
  `MAX_NO_PROGRESS=12` / `CYCLE_WINDOW=12`
  ([questions.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/questions.py:39)).
- **Adopted open-PR shapes, ahead of upstream:**
  [#209](https://github.com/browser-use/jev-ultrafast/pull/209)
  fingerprint-scoped no-op drop + episode cap
  ([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:198),
  commit `e2116a6`);
  [#247](https://github.com/browser-use/jev-ultrafast/issues/247)
  source-aware drop-then-block on full transitions (2×/12 drop, 3×
  block, [agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:511)) —
  no upstream fix exists;
  [#245](https://github.com/browser-use/jev-ultrafast/pull/245)
  covered-node memory + `CoveredTarget`
  ([snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js:144),
  [browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:52),
  commit `49b3c35`);
  [#208](https://github.com/browser-use/jev-ultrafast/pull/208)-via-
  [#5](https://github.com/browser-use/jev-ultrafast/issues/5)
  `encoding=utf-8`
  ([demo.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/demo.py:160)).
  Also keep the *rejection* of
  [#137](https://github.com/browser-use/jev-ultrafast/pull/137)
  (hit-test every candidate) — refused with measurement (118 refused
  picks / 33 controls).
- **Decide/act identity and field context:** stable node carried with
  the decision, act requires `id+node`
  ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:218),
  [agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:382),
  [#132](https://github.com/browser-use/jev-ultrafast/issues/132),
  vs upstream id-only lookup); `field_context` with
  `node`/`dom_id`/`name`/`placeholder`/`input_type`/`scope`
  ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:262),
  [#191](https://github.com/browser-use/jev-ultrafast/issues/191) /
  [#198](https://github.com/browser-use/jev-ultrafast/issues/198) /
  [#202](https://github.com/browser-use/jev-ultrafast/issues/202),
  vs upstream `{label, role, value}` only).
- **Snapshot/browser coverage:** `SELECT` via exact
  `option_index`/`selectedIndex`
  ([snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js:109),
  [browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:232),
  [#216](https://github.com/browser-use/jev-ultrafast/issues/216));
  date/time/month/week/range/color fillable + `input_type`
  ([#215](https://github.com/browser-use/jev-ultrafast/issues/215) /
  [#227](https://github.com/browser-use/jev-ultrafast/issues/227) enumeration);
  `[contenteditable]` ([#218](https://github.com/browser-use/jev-ultrafast/issues/218));
  `[onclick]` as button ([#23](https://github.com/browser-use/jev-ultrafast/issues/23));
  slider role; lazy `browser_harness` import
  ([browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:18),
  [#16](https://github.com/browser-use/jev-ultrafast/issues/16) part).
- **Model/text robustness** (upstream
  [#179](https://github.com/browser-use/jev-ultrafast/pull/179) /
  [#207](https://github.com/browser-use/jev-ultrafast/pull/207) still
  open): `ModelError` / `ConfigError` / `MissingValueError` with
  error-in-200, non-JSON, and off-envelope checks
  ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:32),
  [#158](https://github.com/browser-use/jev-ultrafast/issues/158) /
  [#87](https://github.com/browser-use/jev-ultrafast/issues/87) /
  [#133](https://github.com/browser-use/jev-ultrafast/issues/133));
  fence-strip + `{text: null}` → `MissingValueError` → blocked
  ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:300),
  [#157](https://github.com/browser-use/jev-ultrafast/issues/157) /
  [#203](https://github.com/browser-use/jev-ultrafast/issues/203));
  text-helper retry 3× with `0.3*2^n` backoff (upstream: single
  attempt). `post_json` parity is kept deliberately (3 attempts,
  `{429, 529, 503}`, `0.5*2^n`, timeout 25) — parity here is a feature.
- **Config done right (a breaking rename worth keeping):** one
  `Settings` surface for `SYSTEMONE_BASE_URL` / `PATH` / `MODEL` with
  startup validation
  ([settings.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/settings.py:33),
  [#241](https://github.com/browser-use/jev-ultrafast/issues/241) /
  [#36](https://github.com/browser-use/jev-ultrafast/issues/36));
  `python-dotenv` loading
  ([#217](https://github.com/browser-use/jev-ultrafast/issues/217),
  vs upstream's quote-keeping hand parser); config-driven reasoning
  field with omit mode + exact DeepSeek hostname match
  ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:282),
  [#11](https://github.com/browser-use/jev-ultrafast/issues/11),
  commit `b76d54a`); lazy httpx client.
- **Local-only testability (no upstream equivalent):**
  `StaticBrowser` offline stdlib perceive/act mirroring snapshot
  semantics ([static_dom.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/static_dom.py:1));
  `HeuristicDecider` + `MappingTextProvider`
  ([model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:389));
  `python -m account_vault` offline runner; demo offline mode
  (upstream demo is always-live); injectable `Protocol`s
  ([agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:26));
  7 test files / 113 collected vs upstream's single 320-line file.
  The tree-wide ruff-format divergence (`5e63de2`) is acknowledged in
  its commit body — cosmetic drift, deliberately accepted.

---

## 7. What outsiders are saying

Coverage outside the repo is dense but shallow: 11 distinct web
searches (`jev-ultrafast`, `browser-use jev ultrafast`, `ultrafast
agent loop browser-use`, `jev-ultrafast benchmark`, `browser-use agent
loop`, Hacker News / X / fork variants, criticism variants) each
returned a full page of results, yet most pieces recycle the README /
`performance.md` launch numbers. As of 2026-09-22,
[Latenode](https://latenode.com/ai-trends/jev-ultrafast-browser-agent)
correctly noted that no critique had been published and the only
public measurements were the author's own. The repo README itself was
excluded as evidence below except where outsiders quote or test it.
Treat anything marked *opinion* as sentiment, not measurement.

### Independent or semi-independent measurements (read these first)

- **Closest same-task replication — Go port `phanngoc/browser-ai`**
  ([shipwithjev.com/builds/browser-ai-go](https://www.shipwithjev.com/builds/browser-ai-go),
  runs 2026-09-20, headless Chrome 153, verified from the final page,
  not from `DONE`): Flights with Jev 3/3 at a 9.6 s median, 330 ms per
  decision — vs `gemini-2.5-flash` 3/3 at 23.0 s, `gpt-5.4-mini` (low)
  2/3 at 29.4 s, `flash-lite` 0/3 with a false `DONE`. That is ~36%
  slower than the 7.073 s launch claim and 330 ms vs the claimed
  178 ms median latency. *Verified as reported by that source; the gap's
  cause (harness, network, Chrome version, timing boundary) is
  unresolved (§10).*
- **Broad-suite counterpoint — `fastbrowse`**
  ([github.com/agent-labs-dev/fastbrowse](https://github.com/agent-labs-dev/fastbrowse),
  campaign `release-0.10.0`, 2026-11-09, 53 live tasks ×3): Jev
  Ultrafast passed 4.6% of 129 attempts at $0.0017/run ("returns no
  answer and cannot be given login credentials") vs hosted Browser Use
  94.1% and `fastbrowse` 89.9%. Caveat: the suite measures
  answer-returning tasks this loop was not designed for, so the score
  partly reflects task/agent mismatch. *Single-source in this review —
  the critic re-fetched only HiddenLayer's primary, not this one.*
- **Long-horizon counterpoint — founder-reported, not neutral:**
  experimental Jev agent 1/20 complex long-horizon workflows vs
  BrowserCode+Luna 17/20, per `@gregpr07` 2026-09-19 via
  [field notes, 2026-10-17](https://x.yrzhe.top/en/articles/what-is-jev-use-cases)
  ("a model with 0 reasoning simply can't think hard / go back").
  Directionally consistent with P1.4/P1.5; weight accordingly.
- **Hands-on test — MrJev**
  ([mrjev.com/projects/browser-use-jev-ultrafast](https://mrjev.com/projects/browser-use-jev-ultrafast/),
  2026-09-19, at commit `1231850`, v0.1.0): 31 offline tests pass,
  guards 21/21, Wikipedia task end-to-end with a stub text helper.
  Verdict: promising MVP, not a hands-off agent for real accounts. It
  publishes **no** speed numbers of its own. The same write-up is the
  best external list of the privacy defaults: real Chrome by default,
  visible page text to TypeSafe every step plus up to 6,000 characters
  to a second LLM on typing, DeepSeek default when
  `TEXT_MODEL_BASE_URL` is unset, opt-out PostHog telemetry
  (`BH_TELEMETRY=0`), and a missing `TEXT_MODEL_API_KEY` crashing with
  a traceback at the first `TYPE_TEXT`. (Locally, the key/startup and
  defaults issues are largely fixed via `Settings` — §6; the attach
  default is not — P0.2.)
- **Security demo — HiddenLayer**
  ([hiddenlayer.com/insight/jev-security-risks-ai-decision-models](https://www.hiddenlayer.com/insight/jev-security-risks-ai-decision-models),
  2026-10-14, simulated checkout): on **one** API call, a sponsored
  "Confirm Price" button received 91% choice probability vs 9% for
  "Place order". This is a single-call probability split, **not** a
  91% attack-success or trial rate; no trial count is stated
  (critic-verified against the primary body). HiddenLayer sells AI-
  security products, so read it as an existence proof for
  prompt-injection-via-page-text, not a field rate. For an account
  vault, existence is enough to take seriously.

### Praise (opinion — architecture, not new measurement)

The consensus praise is consistent: indexed DOM action space, one
speculative request per cycle, no screenshots/selectors/coordinates/
code execution, a readable ~6-file loop, deterministic revalidation,
and genuine speed/cost wins on short structured form/search tasks.
Sources:
[Matt Cullerton, 2026-09-18](https://mattcullerton.com/tokdocs/jev-ultrafast-browser-use-s-dom-indexed-agent-that-picks-an-action-and-a-target/),
[FrontierNow, 2026-10-12](https://frontiernow.dev/en/github/jev-ultrafast/),
[DEV / TerminalChai, 2026-10-04](https://dev.to/terminalchai/jev-ultrafast-the-sub-10-second-web-agent-architecture-1728).
The [Hacker News thread](https://news.ycombinator.com/item?id=49735979)
(93 points, 14 comments) adds "worked as described" alongside
"broken / demo error", a timing-after-first-observation challenge,
demand for a local model, and a PostHog credential-leak concern.

### Criticism of the headline numbers (methodology)

- 7.073 s is a single Flights task, timed *after* the first
  observation, n = 3 matched pairs, p = 0.25 — an architecture lesson,
  not a benchmark:
  [SMF Clearinghouse, 2026-09-18](https://www.smfclearinghouse.com/blog/jev-ultrafast-indexed-action-space/)
  (cloned `1231850`, did not run it),
  [Matt Cullerton](https://mattcullerton.com/tokdocs/jev-ultrafast-browser-use-s-dom-indexed-agent-that-picks-an-action-and-a-target/),
  [Goldie](https://goldie.agency/jev-ultrafast-github/) ("self-run,
  conditions stated").
- −90.8% protocol calls bought only −25% wall-clock (a decomposition,
  not a rerun):
  [CreativeAINews, 2026-09-16](https://www.creativeainews.com/articles/jev-ultrafast-browser-agent-latency-cost-2026/).
- $0.0039/run is author-reported, hard-wired to a closed pay-per-call
  TypeSafe API plus a second LLM key, and possibly subsidised /
  early-access priced:
  [Latenode, 2026-09-22](https://latenode.com/ai-trends/jev-ultrafast-browser-agent),
  [HoangYell, 2026-09-21](https://hoangyell.com/jev-ultrafast-explained/)
  (which also flags the silent 250-element truncation — and itself
  inflates latency to "sub-20 ms" against the 178 ms median others
  cite; outsider inflation cuts both ways).
- Scope limits repeated everywhere: 250-element cap, shadow roots,
  frames/iframes, canvas, uploads, pop-up tabs, nested scrolling,
  arbitrary keyboard widgets, non-native clickables; no reasoning,
  planning, or backtracking:
  [Heatdrop, 2026-10-07](https://heatdrop.ai/repo/browser-use/jev-ultrafast),
  [Latenode](https://latenode.com/ai-trends/jev-ultrafast-browser-agent).
  Latenode's sharpest line is worth keeping in front of this project:
  "zero hallucination" is a schema guarantee, not correctness — a
  wrong `DONE` looks exactly like a right `DONE`. Locally, independent
  verification (never the model's word) is already the stated
  contract; keep it that way.

### Frequently conflated — not evidence about this loop

- **Model-only benchmarks:** Jev 79.3% macro / 381 ms vs ModernBERT
  78.7% ([Hugging Face, 2026-09-21](https://huggingface.co/blog/dylantom2012/i-benchmarked-jev-against-open-cpu-only-stacks-it));
  791 labelled decisions, 2.0–3.6× faster and 4.7–7.5× cheaper than
  small LLMs ([AYAutomate, 2026-09-20](https://www.ayautomate.com/blog/jev-vs-llm-benchmark)).
  These measure the Jev *model*, not the agent loop.
- **Jev + LLM-planner hybrids:** the 13-task $0.0053 / 13.9 s figure
  via [PrecioFishbone, 2026-09-30](https://www.preciofishbone.com/knowledge-hub/jev-browser-agent-security/)
  is `fastbrowse` (Jev picks clicks, an LLM plans), not the ultrafast
  loop alone.
- **Derivative explainers and directories** recycle launch numbers
  without reruns, including
  [Activepieces, 2026-11-06](https://www.activepieces.com/blog/what-is-jev-ultrafast-agent-browser-automation-in-2026)
  ("8 Jev tests in the time of 1 Selenium test") and the vendor
  comparison [Denker, 2026-11-02](https://www.denker.ai/blog/denker-vs-jev-ultrafast).
  Adoption signals (3.2M X views via Denker; stars 873 at 15 h → ~25k
  across dated snapshots) are secondary aggregators only — `x.com` was
  not fetched directly, and the
  [Syntax YouTube video](https://www.youtube.com/watch?v=QbYBRjOaGOo)
  yielded a title only, no transcript. Report them as buzz, not proof.
- **Ports/forks worth knowing:** the Go port above;
  [chy4pro/jev-for-chrome](https://github.com/chy4pro/jev-for-chrome)
  (TypeScript, Manifest V3, "not affiliated with TypeSafe");
  `fastbrowse` as a derivative. The wider fork population (~1.7k forks
  reported by directories) was not audited per-fork (§10).

**Net external read for this port:** the architecture is genuinely
admired and plausibly fast on short structured tasks; the speed/cost
headlines have no independent metered replication on the upstream
Python harness; reliability collapses outside the MVP envelope; and the
privacy/security defaults (real profile, page-text exfiltration,
injection via page content) are exactly the wrong defaults for an
account vault unless deliberately overridden — which is why P0.2
outranks its effort.

---

## 8. Already handled (excluded above)

So readers can see what §2–§4 deliberately left out. Source of truth:
[jev-ultrafast-port.md](/Users/jagadesh/Documents/Projects/account-vault/docs/jev-ultrafast-port.md:38)
tables + `DEVIATION` comments + `tests/test_regressions.py`, each
verified in code during this review (not taken from the table alone).

**Issues fixed and regression-pinned:**
[#132](https://github.com/browser-use/jev-ultrafast/issues/132) node+id act,
[#216](https://github.com/browser-use/jev-ultrafast/issues/216) `option_index`,
[#215](https://github.com/browser-use/jev-ultrafast/issues/215) /
[#227](https://github.com/browser-use/jev-ultrafast/issues/227)-enumeration input types,
[#218](https://github.com/browser-use/jev-ultrafast/issues/218) contenteditable,
[#23](https://github.com/browser-use/jev-ultrafast/issues/23) `[onclick]` (residual in P1.9),
[#191](https://github.com/browser-use/jev-ultrafast/issues/191) /
[#198](https://github.com/browser-use/jev-ultrafast/issues/198) /
[#202](https://github.com/browser-use/jev-ultrafast/issues/202) field identity,
[#203](https://github.com/browser-use/jev-ultrafast/issues/203) null-text,
[#157](https://github.com/browser-use/jev-ultrafast/issues/157) fence+retry (helper only),
[#158](https://github.com/browser-use/jev-ultrafast/issues/158) /
[#87](https://github.com/browser-use/jev-ultrafast/issues/87) /
[#133](https://github.com/browser-use/jev-ultrafast/issues/133) envelope errors,
[#241](https://github.com/browser-use/jev-ultrafast/issues/241) /
[#25](https://github.com/browser-use/jev-ultrafast/issues/25)-part /
[#125](https://github.com/browser-use/jev-ultrafast/issues/125)-part base URL + decider object,
[#36](https://github.com/browser-use/jev-ultrafast/issues/36) `Settings` validation,
[#217](https://github.com/browser-use/jev-ultrafast/issues/217) dotenv,
[#94](https://github.com/browser-use/jev-ultrafast/issues/94) /
[#55](https://github.com/browser-use/jev-ultrafast/issues/55) /
[#1](https://github.com/browser-use/jev-ultrafast/issues/1) progress/stale/empty bounds,
[#120](https://github.com/browser-use/jev-ultrafast/issues/120) confidence gate,
[#16](https://github.com/browser-use/jev-ultrafast/issues/16)-part lazy imports,
[#11](https://github.com/browser-use/jev-ultrafast/issues/11) reasoning omit,
[#5](https://github.com/browser-use/jev-ultrafast/issues/5) utf-8,
[#247](https://github.com/browser-use/jev-ultrafast/issues/247) spend guard
(accuracy itself is P1.4). Each lives at
`https://github.com/browser-use/jev-ultrafast/issues/N`.

**Open PRs adopted or rejected with reasons:**
[#209](https://github.com/browser-use/jev-ultrafast/pull/209) adopted (`e2116a6`),
[#245](https://github.com/browser-use/jev-ultrafast/pull/245) adopted (`49b3c35`),
[#208](https://github.com/browser-use/jev-ultrafast/pull/208) same fix via
[#5](https://github.com/browser-use/jev-ultrafast/issues/5),
[#137](https://github.com/browser-use/jev-ultrafast/pull/137) rejected with measurement.
[#212](https://github.com/browser-use/jev-ultrafast/pull/212) was listed as
handled by one research angle; the critic and a direct code read prove
it citation-only, so it sits in P0.3 — do not double-count it here.

**Deliberately not ported:** the Google Flights example (hardcoded
2026-09-20 date, [#93](https://github.com/browser-use/jev-ultrafast/issues/93) /
[#156](https://github.com/browser-use/jev-ultrafast/issues/156)),
recording/render/measurement scripts, and the upstream README itself —
which is why merged
[#30](https://github.com/browser-use/jev-ultrafast/pull/30) /
[#238](https://github.com/browser-use/jev-ultrafast/pull/238) /
[#244](https://github.com/browser-use/jev-ultrafast/pull/244) have no local
file to patch.

**Confirmed nonexistent upstream (so "missed fixes" cannot hide there):**
zero closed issues (`gh issue list --state closed` returns `[]`); only
5 commits on `main`; the diff `452c1ad..cf3c406` touches only
`README.md (+13/-2)`. The unrecorded exact clone SHA is therefore
immaterial for code drift — every post-`452c1ad` `main` SHA has
identical code. There is also no local `CHANGELOG`, no `TODO`/`FIXME`/
`XXX` in `src`/`tests`/`docs` on either side, and no upstream git
remote configured locally (origin is `rabdomstran/account-vault` only).

---

## 9. Recommended pickup order

Sequencing matters more than the P-labels here, because two items
(baseline, branch diff) change the value of everything after them.

1. **S quick wins first (all offline-testable except the Windows check):**
   [PR #246](https://github.com/browser-use/jev-ultrafast/pull/246) textarea naming (P1.1),
   [#16](https://github.com/browser-use/jev-ultrafast/issues/16)b SOCKS (P1.2),
   [PR #244](https://github.com/browser-use/jev-ultrafast/pull/244) one-line docs note (§4),
   [#51](https://github.com/browser-use/jev-ultrafast/issues/51) `bringToFront` (P1.3, needs a Windows verifier),
   [#223](https://github.com/browser-use/jev-ultrafast/issues/223) `keep_open` (P1.6).
2. **Safety and hangs:**
   [#16](https://github.com/browser-use/jev-ultrafast/issues/16)a dedicated-profile default (P0.2)
   before any real-account or vault work; screenshot/CDP deadline
   (P0.4) *after* diffing `codex/bu-bench-eval-v2` (`6cf62ca`,
   `a2676ee`) so the local fix tracks the upstream-originated one.
3. **Live-CDP correctness:**
   [#227](https://github.com/browser-use/jev-ultrafast/issues/227) native date commit (P0.1) with a live-Chrome
   regression — the offline path already passes, so the test must be
   CDP. Then [PR #212](https://github.com/browser-use/jev-ultrafast/pull/212)
   scroll state (P0.3) as the first deliberate prompt-parity break,
   gated on step 4's baseline.
4. **Baseline before intelligence:**
   [#248](https://github.com/browser-use/jev-ultrafast/issues/248) /
   [#67](https://github.com/browser-use/jev-ultrafast/issues/67) real-model eval (P1.4) — metered hosted-Jev vs
   `d1-2B` on a fixed suite including the
   [#129](https://github.com/browser-use/jev-ultrafast/issues/129) MDN task, scroll tasks, and date tasks.
   Only then triage the planner branches (P1.5, M to diff) and judge
   [#149](https://github.com/browser-use/jev-ultrafast/issues/149) structure (P1.8) /
   [#23](https://github.com/browser-use/jev-ultrafast/issues/23) second pass (P1.9) against measured numbers.
   Make the successor decision (delegate to Browser Use ultrafast mode
   vs maintain this loop) *with* those numbers, not before.
5. **UX and strategic last:**
   [#159](https://github.com/browser-use/jev-ultrafast/issues/159) demo preflight (P1.7); the password/vault-scope
   decision and unused-deps prune-or-implement (§5) as an explicit
   ADR; perception PRs
   ([#234](https://github.com/browser-use/jev-ultrafast/pull/234) /
   [#189](https://github.com/browser-use/jev-ultrafast/pull/189) /
   [#211](https://github.com/browser-use/jev-ultrafast/pull/211)) and
   MCP ([#125](https://github.com/browser-use/jev-ultrafast/issues/125))
   only after the successor decision. P2 ignores need no scheduling.

---

## 10. Method, limits, and open gaps

### Method (so this can be re-run or challenged)

- Five independent research angles (local port anatomy; merged PRs;
  open issues; both-sides deviation audit; external discourse), an
  adversarial completeness critic, one gap follow-up, and a synthesis.
  Every angle was required to open the bodies it cites; the critic
  re-ran independent `gh` checks and re-fetched the HiddenLayer
  primary. The parent additionally spot-checked local HEAD, the two
  sync commits, and three P0 code citations
  ([browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:280)
  `Input.insertText`, unguarded screenshot, `INPUT`-only textarea
  naming, verbatim `ensure_daemon()`).
- Upstream data came from the authenticated `gh` CLI (`gh pr list/view`,
  `gh issue list/view`, `gh api search/issues`, `gh repo view`) — no
  `web_fetch` fallback was needed for GitHub. All 3 merged PRs were
  viewed with diffs; all 57 open issues were body-read (top 30 by
  `updatedAt` deep-read first). Upstream code was cloned
  (`--depth 50`) to `/tmp/jev-ultrafast-upstream` at `cf3c406` and
  read file-by-file against local (`agent.py`, `model.py`,
  `browser.py`, `questions.py`, `snapshot.js`, `demo.py`,
  `pyproject.toml`, `.env.example` in full on both sides).
- Local handled-set discovery was independent per angle: full
  `git log --all` bodies (28 commits),
  [jev-ultrafast-port.md](/Users/jagadesh/Documents/Projects/account-vault/docs/jev-ultrafast-port.md:1)
  tables, `DEVIATION` comments across
  [agent.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/agent.py:1) /
  [model.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/model.py:1) /
  [browser.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/browser.py:1) /
  [snapshot.js](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/snapshot.js:1) /
  [questions.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/questions.py:1) /
  [settings.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/settings.py:1) /
  [demo.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/demo.py:1),
  `tests/test_regressions.py`, `tests/test_static.py`, `README`,
  `pyproject.toml`, `CONTRIBUTING`. Test count was independently
  corroborated (`pytest --collect-only` = 113).
- Local structural reference for §6/§8:
  `src/account_vault/{agent.py (568, loop), model.py (521, decide+text+
  offline doubles), browser.py (289, CDP perceive/act), snapshot.js
  (168, in-page perceive), static_dom.py (575, offline perceive/act),
  questions.py (53, prompts+budgets), settings.py (86, env), demo.py
  (214, inspector server), __main__.py (132, CLI), static/ (inspector
  UI+fixtures)}`; `examples/hotel_search.html`;
  `scripts/{e2e_offline_demo.py, check_dist_assets.py}`;
  `tests/{test_agent, test_regressions, test_browser_cdp, test_static,
  test_main, test_demo, test_live_cdp}` (2,286 lines). Entry points:
  `python -m account_vault` (offline demo), `account-vault-demo`
  (inspector on `127.0.0.1:8766`), library `Agent` / `CdpBrowser` /
  `StaticBrowser`.
- Corrections applied by the critic and carried throughout this
  document: 57 issues + 153 PRs = 210 items (not "210 issues" /
  "~175 PRs"); upstream is not archived;
  [#212](https://github.com/browser-use/jev-ultrafast/pull/212)
  citation-only (handled → P0); HiddenLayer 91% is a single-call
  probability split. Where an early angle and the critic disagreed,
  the critic's code-checked version won and the disagreement is noted
  inline (see [#53](https://github.com/browser-use/jev-ultrafast/issues/53),
  [#14](https://github.com/browser-use/jev-ultrafast/issues/14)).

### Open gaps (unresolved — read before acting on the P-order)

1. **Non-main branches never diffed line-by-line.** `codex/planner-loop`,
   `codex/planner-state-final`, `codex/state-action-hillclimb`,
   `codex/bu-bench-eval*` (including `bf5b7fd` hittable-controls work)
   may overlap or supersede local fixes attributed to
   [#245](https://github.com/browser-use/jev-ultrafast/pull/245) /
   [#209](https://github.com/browser-use/jev-ultrafast/pull/209) and
   the P0.4 timeout fix. All branch items above are triage-first for
   this reason. This is the review's largest gap.
2. **Open-PR triage is sampled, not exhaustive.** ~153 open PRs; ~20
   cited PRs plus spot-checks were assessed (the deviation angle
   returned `complete=false` on exactly this point). A high-impact
   security/correctness fix could still hide in the remainder beyond
   the named-but-unassessed
   [#234](https://github.com/browser-use/jev-ultrafast/pull/234) /
   [#231](https://github.com/browser-use/jev-ultrafast/pull/231) /
   [#199](https://github.com/browser-use/jev-ultrafast/pull/199) /
   [#206](https://github.com/browser-use/jev-ultrafast/pull/206).
3. **Nothing was run live.** No live CDP/Chrome execution for
   [#227](https://github.com/browser-use/jev-ultrafast/issues/227)
   date-commit, [#51](https://github.com/browser-use/jev-ultrafast/issues/51)
   Windows, [#14](https://github.com/browser-use/jev-ultrafast/issues/14)
   screenshot frequency,
   [#129](https://github.com/browser-use/jev-ultrafast/issues/129) MDN,
   or [#23](https://github.com/browser-use/jev-ultrafast/issues/23)
   rows — all verified at code level only. The real decide model has
   never run end-to-end, so
   [#248](https://github.com/browser-use/jev-ultrafast/issues/248) /
   [#67](https://github.com/browser-use/jev-ultrafast/issues/67)
   accuracy and `d1-2B`-vs-Jev drift are unquantified; the Decido
   benchmark linked from
   [#248](https://github.com/browser-use/jev-ultrafast/issues/248) was
   not executed. Issue comment threads were fetched only for
   [#247](https://github.com/browser-use/jev-ultrafast/issues/247);
   a fix discussed only in comments elsewhere could be missed.
   [#233](https://github.com/browser-use/jev-ultrafast/issues/233)'s
   root cause is undeterminable from its text.
4. **External evidence is thin in places.** `fastbrowse` 4.6% and the
   Go port's 9.6 s rest on single sources in this review (only
   HiddenLayer was re-fetched from its primary). $0.0039 and 7.073 s
   have no independent metered replication on the upstream Python
   harness; whether the Go-port gap is harness, network, Chrome
   version, or timing boundary is unknown. X metrics are secondary
   aggregators only; the YouTube transcript was unextractable; the
   ~1.7k-fork population and non-English coverage were not surveyed.
5. **Successor API uninspected.** No angle opened Browser Use's
   ultrafast-mode API (`Agent(mode='ultrafast')` / `bu-ultrafast`), so
   migration cost — and whether this loop still wins on latency or
   accuracy — is unknown. Static assets, examples, and test bodies
   were compared by sample/diff, not full-body reads; performance
   comparability with upstream was not re-verified.

### Bottom line

Stop thinking of this as a port that is behind. On code, it is ahead of
a frozen upstream; on strategy, it is maintaining an experiment whose
owner has moved on to a successor this repo already depends on but does
not use. Clear the four P0s (safety first), bank the S-sized P1 wins,
build the real-model baseline, diff the planner branches — and only
then decide whether the loop's future is here or in
`Agent(mode='ultrafast')`.






