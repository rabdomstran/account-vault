# ADR-002: Break prompt byte-parity for incremental scrolling

- **Status:** Accepted
- **Date:** 2026-11-21
- **Deciders:** Project owner (P0 pickup directive) + Muse Code
- **References:**
  - [Upstream PR #212 — Fix premature blocking during incremental scrolling](https://github.com/browser-use/jev-ultrafast/pull/212)
  - [Drift review P0.3](jev-ultrafast-drift-review.md)
  - [ADR-001: System-One model for the decide step](adr-001-model-choice.md)

## Context

Until now this port kept its prompts byte-identical to upstream
jev-ultrafast (AST-compared in the drift review), so upstream decision
behaviour stayed comparable. PR #212 showed that parity has a cost:
the decide request carried no scroll position or document height, and
history said only that a scroll changed the page. On a target below
the second viewport the decider selected `BLOCKED` after one
successful scroll even though `SCROLL_DOWN` remained available. The
PR's independent browser benchmark: 0/5 scroll-down tasks passed on
upstream `main`, 5/5 with the patch (3/3 scroll-up, 12/12
click/menu/accordion/select regressions, boundary correctly
`BLOCKED`). The drift review ranks this P0: below-fold targets are
systematically unachievable without it.

## Decision

Adopt PR #212 in full, including its two prompt sentences in
`NEXT_ACTION` (incremental scrolling; `BLOCKED` also means no scroll
can reveal more), the `scroll` state in the decide request,
`scroll_before`/`scroll_after` history, and suppression of `BLOCKED`
from the operation space while the last scroll demonstrably advanced
(proven from recorded positions, not assumed) and the same direction
remains offered. Prompt byte-parity with upstream ends at this commit;
`TARGET` and `TEXT_VALUE` remain byte-identical.

## Consequences

- Below-fold targets become reachable; a boundary or no-op scroll
  re-offers `BLOCKED`, and the existing action/decision budgets still
  bound a run that scrolls without converging.
- The prompt delta is tuned-by-upstream-benchmark evidence, not yet
  by a local real-model run: no real decide model has been exercised
  in this repo (drift review §9 step 4 / P1.4). **Revisit trigger:**
  when the metered hosted-Jev vs local `d1-2B` baseline exists, re-run
  the scroll tasks (both directions + boundary) against it; if the
  local model misbehaves under the new state/prompt shape, revise
  here rather than reverting blindly.
- Future prompt changes now need the same treatment: upstream
  evidence or a local benchmark, recorded in an ADR. Byte-parity is
  no longer a default argument either way.
