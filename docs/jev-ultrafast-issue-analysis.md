# jev-ultrafast open-issue analysis — failure modes vs this port

- **Generated:** 2026-11-21 20:07 UTC by `scripts/analyse_jev_issues.py` (live `gh` fetch).
- **Corpus:** 57 open issues in [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) (45 comments, 65 distinct people). PRs are out of scope.
- **Cross-referenced against:** `docs/jev-ultrafast-port.md`, `docs/jev-ultrafast-drift-review.md`, plus `DEVIATION` comments and regression tests in `src/` / `tests/` (code outranks docs).
- **Reproduce:** `uv run python scripts/analyse_jev_issues.py` (needs authenticated `gh`).

## TL;DR

- **27 handled, 3 partial, 10 open, 17 ignore/noise, 0 unclassified** out of 57.
- Highest-ranked remaining work is [#67](https://github.com/browser-use/jev-ultrafast/issues/67) (decide-accuracy, hit score 31): Jev ultra fast Can't complete simple task.
- The hit score is an **engagement proxy** (comments, people, reactions, cross-references), not usage telemetry — the tracker has none. Formula in §7.

## 1. Work queue (ranked — start here)

Open + partial + unclassified, most-hit first. Noise and handled issues are excluded.

| Rank | Issue | Cluster | Status | Hit | Comments | Next action |
|---|---|---|---|---|---|---|
| 1 | [#67](https://github.com/browser-use/jev-ultrafast/issues/67) Jev ultra fast Can't complete simple task | decide-accuracy | open | 31 | 8 | Reproduce on the baseline suite; classify misses as menu vs pick before changing prompts |
| 2 | [#125](https://github.com/browser-use/jev-ultrafast/issues/125) Feature Request: Support local resident decisio… | wire-providers | partial | 20 | 4 | Close the residual: local backend landed; no MCP stdio server |
| 3 | [#51](https://github.com/browser-use/jev-ultrafast/issues/51) Windows: owned tab is backgrounded, modal menus… | browser-cdp | open | 19 | 2 | Add Page.bringToFront on the Windows path; needs a Windows live verifier before calling it fixed |
| 4 | [#16](https://github.com/browser-use/jev-ultrafast/issues/16) Setup friction: SOCKS proxy crash on startup; b… | browser-cdp | partial | 12 | 0 | Close the residual: attach default fixed in code; SOCKS first-call crash remains (httpx lacks the socks extra) |
| 5 | [#149](https://github.com/browser-use/jev-ultrafast/issues/149) Could the visible page state preserve more sema… | perception-fidelity | open | 11 | 1 | Prototype structured visible text behind the baseline; keep the 6,000-char budget explicit |
| 6 | [#223](https://github.com/browser-use/jev-ultrafast/issues/223) Add opt-in keep_open mode to preserve and foreg… | loop-control | open | 11 | 1 | Add opt-in keep_open (small, offline-testable); default stays close |
| 7 | [#25](https://github.com/browser-use/jev-ultrafast/issues/25) [Feature Request]: Decouple Jev inference provi… | wire-providers | partial | 8 | 1 | Close the residual: decider object + base URL landed; wire shape still /v1/systemone |
| 8 | [#187](https://github.com/browser-use/jev-ultrafast/issues/187) Idea: Multi-Stage Agent Architecture for Improv… | decide-accuracy | open | 6 | 0 | Evaluate only via the planner-branch diff (drift P1.5); do not design from the issue alone |
| 9 | [#248](https://github.com/browser-use/jev-ultrafast/issues/248) Community benchmark: how accurate is the decisi… | decide-accuracy | open | 6 | 0 | Run the metered real-model baseline (Jev vs d1-2B); score steps and run completion separately |
| 10 | [#129](https://github.com/browser-use/jev-ultrafast/issues/129) MDN wrong-waypoint actions | decide-accuracy | open | 3 | 0 | Add the MDN task to the baseline suite as a pinned wrong-waypoint repro |
| 11 | [#159](https://github.com/browser-use/jev-ultrafast/issues/159) Demo UX: a first-time user cannot tell why noth… | demo-docs-ux | open | 3 | 0 | Demo preflight: surface missing keys/backends before the first tick |
| 12 | [#224](https://github.com/browser-use/jev-ultrafast/issues/224) Choose next 会报错Model provider returned HTTP 451… | wire-providers | open | 3 | 0 | Name the region block in the hosted-path error text; point at the local backend |
| 13 | [#225](https://github.com/browser-use/jev-ultrafast/issues/225) When I click "Choose next", I get an error: Mod… | wire-providers | open | 3 | 0 | Same fix as #224 (duplicate report) -- one error-text change covers both |

## 2. Corpus snapshot

- Issues: 57 open; comments: 45; distinct people: 65.
- Labels: **none** — upstream applies no labels, so clustering is by title/body text (§7).
- Filed by month: 2026-09 ×38, 2026-10 ×15, 2026-11 ×4.
- Most discussed: #67 (8), #125 (4), #120 (4), #14 (3), #133 (2).

## 3. Failure-mode clusters

| Cluster | Issues | Handled | Partial | Open | Ignore |
|---|---|---|---|---|---|
| decide-accuracy | 5 | 1 | 0 | 4 | 0 |
| loop-control | 5 | 4 | 0 | 1 | 0 |
| text-helper | 5 | 5 | 0 | 0 | 0 |
| perceive-enumeration | 4 | 4 | 0 | 0 | 0 |
| execute-act | 2 | 2 | 0 | 0 | 0 |
| perception-fidelity | 1 | 0 | 0 | 1 | 0 |
| wire-providers | 13 | 7 | 2 | 2 | 2 |
| browser-cdp | 4 | 2 | 1 | 1 | 0 |
| demo-docs-ux | 6 | 2 | 0 | 1 | 3 |
| community-noise | 12 | 0 | 0 | 0 | 12 |

### decide-accuracy

The decider picks the wrong operation/target (or loops over right-looking picks). Includes wrong-waypoint reports, simple-task failures, cycling, and accuracy benchmarks/planner proposals.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#67](https://github.com/browser-use/jev-ultrafast/issues/67) Jev ultra fast Can't complete simple task | open | 8 | code: src/account_vault/agent.py:482, tests/test_regressions.py:475, tests/test_regressions.py:479; drift review P1; port notes: deliberately NOT fixed; not a fix: cited by the no-op/cycle spend guards; decision accuracy itself is explicitly NOT fixed (see #248) |
| [#129](https://github.com/browser-use/jev-ultrafast/issues/129) MDN wrong-waypoint actions | open | 0 | drift review P1 |
| [#187](https://github.com/browser-use/jev-ultrafast/issues/187) Idea: Multi-Stage Agent Architecture for Improved D… | open | 0 | drift review P2-evaluate; port notes: deliberately NOT fixed |
| [#247](https://github.com/browser-use/jev-ultrafast/issues/247) Jev loops through catalog navigation instead of sor… | handled | 1 | code: src/account_vault/agent.py:216, src/account_vault/agent.py:522, tests/test_regressions.py:475; port notes: Fixed table; drift review §8; drift review P1; port notes: deliberately NOT fixed |
| [#248](https://github.com/browser-use/jev-ultrafast/issues/248) Community benchmark: how accurate is the decision m… | open | 0 | drift review P1; port notes: deliberately NOT fixed |

### loop-control

Run budgets and stopping: empty first observations, no-progress detection, confidence gating, tab lifecycle.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#1](https://github.com/browser-use/jev-ultrafast/issues/1) Library API: first observation can return an empty… | handled | 0 | code: tests/test_regressions.py:286, tests/test_regressions.py:462; port notes: Fixed table; drift review §8 |
| [#55](https://github.com/browser-use/jev-ultrafast/issues/55) Model-call budget (MAX_STEPS * 2) is undocumented a… | handled | 0 | code: src/account_vault/agent.py:316, src/account_vault/demo.py:54, src/account_vault/questions.py:43; port notes: Fixed table; drift review §8 |
| [#94](https://github.com/browser-use/jev-ultrafast/issues/94) A failed post-action observation (page_changed: nul… | handled | 2 | code: tests/test_regressions.py:267, tests/test_regressions.py:351, tests/test_regressions.py:359; port notes: Fixed table; drift review §8 |
| [#120](https://github.com/browser-use/jev-ultrafast/issues/120) Expose a confidence gate for ordinary browser actio… | handled | 4 | code: src/account_vault/agent.py:345, tests/test_regressions.py:309; port notes: Fixed table; drift review §8 |
| [#223](https://github.com/browser-use/jev-ultrafast/issues/223) Add opt-in keep_open mode to preserve and foregroun… | open | 1 | drift review P1; port notes: deliberately NOT fixed |

### text-helper

The second model that writes field text: element identity, stale-fill cache, null-value contract, retry.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#157](https://github.com/browser-use/jev-ultrafast/issues/157) One transient text-model failure aborts the whole r… | handled | 0 | code: src/account_vault/model.py:370, src/account_vault/model.py:413, tests/test_regressions.py:176; port notes: Fixed table; drift review §8 |
| [#191](https://github.com/browser-use/jev-ultrafast/issues/191) TYPE_TEXT cache key has no element identity: a stal… | handled | 0 | code: src/account_vault/agent.py:410, tests/test_regressions.py:192; port notes: Fixed table; drift review §8 |
| [#198](https://github.com/browser-use/jev-ultrafast/issues/198) TYPE_TEXT helper cannot distinguish same-labeled fi… | handled | 0 | code: src/account_vault/snapshot.js:116, tests/test_regressions.py:192; port notes: Fixed table; drift review §8 |
| [#202](https://github.com/browser-use/jev-ultrafast/issues/202) TYPE_TEXT helper context omits element ID and outer… | handled | 0 | code: src/account_vault/snapshot.js:116, tests/test_regressions.py:192; port notes: Fixed table; drift review §8 |
| [#203](https://github.com/browser-use/jev-ultrafast/issues/203) TEXT_VALUE contract mismatch: model returning {"tex… | handled | 0 | code: src/account_vault/model.py:381, tests/test_agent.py:330, tests/test_regressions.py:157; port notes: Fixed table; drift review §8 |

### perceive-enumeration

The snapshot's element table misses or mislabels controls, so the right action is never offered.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#23](https://github.com/browser-use/jev-ultrafast/issues/23) Element table misses clickable non-native elements,… | handled | 2 | code: src/account_vault/snapshot.js:56, src/account_vault/snapshot.js:82, src/account_vault/snapshot.js:84; port notes: Fixed table; drift review §8; drift review P1 |
| [#215](https://github.com/browser-use/jev-ultrafast/issues/215) Native input types (date, time, datetime-local, mon… | handled | 0 | code: src/account_vault/model.py:122, src/account_vault/snapshot.js:75, src/account_vault/static_dom.py:432; port notes: Fixed table; drift review §8 |
| [#218](https://github.com/browser-use/jev-ultrafast/issues/218) Bare contenteditable / contenteditable="" elements… | handled | 0 | code: src/account_vault/snapshot.js:51, tests/test_static.py:28; port notes: Fixed table; drift review §8 |
| [#227](https://github.com/browser-use/jev-ultrafast/issues/227) date inputs are never enumerated, so the agent cann… | handled | 0 | code: src/account_vault/model.py:122, src/account_vault/snapshot.js:75, src/account_vault/static_dom.py:432; port notes: Fixed table; drift review §8; drift review P0 |

### execute-act

A correct decision is applied to the wrong control or wrong value (stale ids, duplicate options).

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#132](https://github.com/browser-use/jev-ultrafast/issues/132) Stale action ids can select a different live contro… | handled | 0 | code: src/account_vault/agent.py:267, src/account_vault/agent.py:385, src/account_vault/model.py:269; port notes: Fixed table; drift review §8 |
| [#216](https://github.com/browser-use/jev-ultrafast/issues/216) SELECT execution assigns e.value, so duplicate or d… | handled | 0 | code: src/account_vault/static_dom.py:777, tests/test_browser_cdp.py:673, tests/test_static.py:159; port notes: Fixed table; drift review §8 |

### perception-fidelity

What the decider is shown about the page (flattened visible text, lost structure), short of missing elements.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#149](https://github.com/browser-use/jev-ultrafast/issues/149) Could the visible page state preserve more semantic… | open | 1 | drift review P1; port notes: deliberately NOT fixed |

### wire-providers

Model HTTP surface: endpoints, keys, .env parsing, error envelopes, region blocks, provider configurability.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#11](https://github.com/browser-use/jev-ultrafast/issues/11) Support LLMTR as a text-helper provider | handled | 0 | code: src/account_vault/model.py:351; port notes: Fixed table; drift review §8 |
| [#21](https://github.com/browser-use/jev-ultrafast/issues/21) How can I get the TYPESAFE_API_KEY? | ignore | 2 | drift review P2-ignore / noise cluster |
| [#25](https://github.com/browser-use/jev-ultrafast/issues/25) [Feature Request]: Decouple Jev inference provider… | partial | 1 | code: src/account_vault/model.py:442, src/account_vault/settings.py:37, tests/test_regressions.py:138; port notes: Fixed table; drift review §8; drift review P2-evaluate · residual: decider object + base URL landed; wire shape still /v1/systemone |
| [#36](https://github.com/browser-use/jev-ultrafast/issues/36) Missing TEXT_MODEL_API_KEY crashes mid-run; code de… | handled | 1 | code: src/account_vault/agent.py:138, src/account_vault/demo.py:53, src/account_vault/model.py:395; port notes: Fixed table; drift review §8 |
| [#85](https://github.com/browser-use/jev-ultrafast/issues/85) Latency from Japan: 12-17s per task - it's the clie… | ignore | 2 | port notes: deliberately NOT fixed |
| [#87](https://github.com/browser-use/jev-ultrafast/issues/87) Model responses with invalid JSON leak decoder erro… | handled | 0 | code: src/account_vault/model.py:67, tests/test_regressions.py:119; port notes: Fixed table; drift review §8 |
| [#125](https://github.com/browser-use/jev-ultrafast/issues/125) Feature Request: Support local resident decision ba… | partial | 4 | code: src/account_vault/settings.py:37; port notes: Fixed table; drift review §8; drift review P2-evaluate · residual: local backend landed; no MCP stdio server |
| [#133](https://github.com/browser-use/jev-ultrafast/issues/133) choose() can return raw KeyError for an off-envelop… | handled | 2 | code: src/account_vault/model.py:248, tests/test_regressions.py:128, tests/test_regressions.py:426; port notes: Fixed table; drift review §8 |
| [#158](https://github.com/browser-use/jev-ultrafast/issues/158) post_json treats HTTP 200 with an error body as a s… | handled | 0 | code: tests/test_regressions.py:110; port notes: Fixed table; drift review §8 |
| [#217](https://github.com/browser-use/jev-ultrafast/issues/217) .env loader keeps quotes, inline comments, whitespa… | handled | 0 | code: src/account_vault/demo.py:43, tests/test_regressions.py:340; port notes: Fixed table; drift review §8 |
| [#224](https://github.com/browser-use/jev-ultrafast/issues/224) Choose next 会报错Model provider returned HTTP 451; no… | open | 0 | drift review P2-evaluate; port notes: deliberately NOT fixed |
| [#225](https://github.com/browser-use/jev-ultrafast/issues/225) When I click "Choose next", I get an error: Model p… | open | 0 | drift review P2-evaluate; port notes: deliberately NOT fixed |
| [#241](https://github.com/browser-use/jev-ultrafast/issues/241) Decisions endpoint hardcoded to api.typesafe.ai: Ve… | handled | 1 | code: src/account_vault/model.py:442, src/account_vault/settings.py:36, tests/test_regressions.py:138; port notes: Fixed table; drift review §8 |

### browser-cdp

Chrome/CDP harness behaviour: profile attach, proxies, tab painting, hangs and deadlines.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#5](https://github.com/browser-use/jev-ultrafast/issues/5) Fix Windows static asset decoding in demo server | handled | 2 | code: src/account_vault/demo.py:160; port notes: Fixed table; drift review §8 |
| [#14](https://github.com/browser-use/jev-ultrafast/issues/14) app not functioning | handled | 3 | code: src/account_vault/browser.py:152, src/account_vault/browser.py:542, src/account_vault/static/app.js:104; port notes: Fixed table; drift review P0; port notes: deliberately NOT fixed |
| [#16](https://github.com/browser-use/jev-ultrafast/issues/16) Setup friction: SOCKS proxy crash on startup; brows… | partial | 0 | code: src/account_vault/browser.py:23, src/account_vault/browser.py:46, src/account_vault/model.py:42; port notes: Fixed table; drift review §8; drift review P0; drift review P1; port notes: deliberately NOT fixed · residual: attach default fixed in code; SOCKS first-call crash remains (httpx lacks the socks extra) |
| [#51](https://github.com/browser-use/jev-ultrafast/issues/51) Windows: owned tab is backgrounded, modal menus do… | open | 2 | drift review P1; port notes: deliberately NOT fixed |

### demo-docs-ux

Inspector demo, README/docs and first-run experience. Real friction, but it does not change loop behaviour.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#53](https://github.com/browser-use/jev-ultrafast/issues/53) agent.py is minified and violates the project's own… | ignore | 1 | port notes: deliberately NOT fixed |
| [#54](https://github.com/browser-use/jev-ultrafast/issues/54) HIGH — README clone URL points to the wrong reposit… | ignore | 1 | port notes: deliberately NOT fixed |
| [#93](https://github.com/browser-use/jev-ultrafast/issues/93) The bundled Google Flights demo can no longer succe… | handled | 0 | code: src/account_vault/static/app.js:8, tests/test_agent.py:9; drift review §8 |
| [#115](https://github.com/browser-use/jev-ultrafast/issues/115) Add a theme switch button | ignore | 1 | port notes: deliberately NOT fixed |
| [#156](https://github.com/browser-use/jev-ultrafast/issues/156) Hardcoded date in flights example and demo default… | handled | 0 | code: src/account_vault/static/app.js:8, tests/test_agent.py:9; drift review §8 |
| [#159](https://github.com/browser-use/jev-ultrafast/issues/159) Demo UX: a first-time user cannot tell why nothing… | open | 0 | drift review P1; port notes: deliberately NOT fixed |

### community-noise

Showcase posts, empty/garbled reports, spam and pure questions. Counted to reconcile; no work implied.

| Issue | Status | Comments | Evidence / note |
|---|---|---|---|
| [#26](https://github.com/browser-use/jev-ultrafast/issues/26) Question about the Runtime/JeV boundary in jev-ultr… | ignore | 2 | port notes: deliberately NOT fixed |
| [#46](https://github.com/browser-use/jev-ultrafast/issues/46) Min | ignore | 0 | drift review P2-ignore / noise cluster |
| [#47](https://github.com/browser-use/jev-ultrafast/issues/47) Minin | ignore | 0 | drift review P2-ignore / noise cluster |
| [#77](https://github.com/browser-use/jev-ultrafast/issues/77) Create main.ym. | ignore | 0 | drift review P2-ignore / noise cluster |
| [#100](https://github.com/browser-use/jev-ultrafast/issues/100) qr-menu | ignore | 0 | drift review P2-ignore / noise cluster |
| [#140](https://github.com/browser-use/jev-ultrafast/issues/140) ioio | ignore | 1 | drift review P2-ignore / noise cluster |
| [#145](https://github.com/browser-use/jev-ultrafast/issues/145) Community project built on this: jev-browse (agent-… | ignore | 0 | drift review P2-ignore / noise cluster |
| [#152](https://github.com/browser-use/jev-ultrafast/issues/152) Community project built on a bounded Jev loop: Jev… | ignore | 0 | drift review P2-ignore / noise cluster |
| [#177](https://github.com/browser-use/jev-ultrafast/issues/177) Third-party AI-assisted analysis report on jev-ultr… | ignore | 0 | drift review P2-ignore / noise cluster |
| [#233](https://github.com/browser-use/jev-ultrafast/issues/233) AI said： it,s really great | ignore | 0 | drift review P2-ignore / noise cluster |
| [#235](https://github.com/browser-use/jev-ultrafast/issues/235) Jev-ultrafast | ignore | 0 | drift review P2-ignore / noise cluster |
| [#242](https://github.com/browser-use/jev-ultrafast/issues/242) Community project: opencode-browser-use (Jev Ultraf… | ignore | 0 | drift review P2-ignore / noise cluster |

## 4. Already handled by this port

Each row cites where the handling is recorded. Code citations are `file:line` at generation time.

| Issue | Cluster | Evidence |
|---|---|---|
| [#1](https://github.com/browser-use/jev-ultrafast/issues/1) Library API: first observation can return an empty… | loop-control | code: tests/test_regressions.py:286, tests/test_regressions.py:462; port notes: Fixed table; drift review §8 |
| [#5](https://github.com/browser-use/jev-ultrafast/issues/5) Fix Windows static asset decoding in demo server | browser-cdp | code: src/account_vault/demo.py:160; port notes: Fixed table; drift review §8 |
| [#11](https://github.com/browser-use/jev-ultrafast/issues/11) Support LLMTR as a text-helper provider | wire-providers | code: src/account_vault/model.py:351; port notes: Fixed table; drift review §8 |
| [#14](https://github.com/browser-use/jev-ultrafast/issues/14) app not functioning | browser-cdp | code: src/account_vault/browser.py:152, src/account_vault/browser.py:542, src/account_vault/static/app.js:104; port notes: Fixed table; drift review P0; port notes: deliberately NOT fixed |
| [#23](https://github.com/browser-use/jev-ultrafast/issues/23) Element table misses clickable non-native elements,… | perceive-enumeration | code: src/account_vault/snapshot.js:56, src/account_vault/snapshot.js:82, src/account_vault/snapshot.js:84; port notes: Fixed table; drift review §8; drift review P1 |
| [#36](https://github.com/browser-use/jev-ultrafast/issues/36) Missing TEXT_MODEL_API_KEY crashes mid-run; code de… | wire-providers | code: src/account_vault/agent.py:138, src/account_vault/demo.py:53, src/account_vault/model.py:395; port notes: Fixed table; drift review §8 |
| [#55](https://github.com/browser-use/jev-ultrafast/issues/55) Model-call budget (MAX_STEPS * 2) is undocumented a… | loop-control | code: src/account_vault/agent.py:316, src/account_vault/demo.py:54, src/account_vault/questions.py:43; port notes: Fixed table; drift review §8 |
| [#87](https://github.com/browser-use/jev-ultrafast/issues/87) Model responses with invalid JSON leak decoder erro… | wire-providers | code: src/account_vault/model.py:67, tests/test_regressions.py:119; port notes: Fixed table; drift review §8 |
| [#93](https://github.com/browser-use/jev-ultrafast/issues/93) The bundled Google Flights demo can no longer succe… | demo-docs-ux | code: src/account_vault/static/app.js:8, tests/test_agent.py:9; drift review §8 |
| [#94](https://github.com/browser-use/jev-ultrafast/issues/94) A failed post-action observation (page_changed: nul… | loop-control | code: tests/test_regressions.py:267, tests/test_regressions.py:351, tests/test_regressions.py:359; port notes: Fixed table; drift review §8 |
| [#120](https://github.com/browser-use/jev-ultrafast/issues/120) Expose a confidence gate for ordinary browser actio… | loop-control | code: src/account_vault/agent.py:345, tests/test_regressions.py:309; port notes: Fixed table; drift review §8 |
| [#132](https://github.com/browser-use/jev-ultrafast/issues/132) Stale action ids can select a different live contro… | execute-act | code: src/account_vault/agent.py:267, src/account_vault/agent.py:385, src/account_vault/model.py:269; port notes: Fixed table; drift review §8 |
| [#133](https://github.com/browser-use/jev-ultrafast/issues/133) choose() can return raw KeyError for an off-envelop… | wire-providers | code: src/account_vault/model.py:248, tests/test_regressions.py:128, tests/test_regressions.py:426; port notes: Fixed table; drift review §8 |
| [#156](https://github.com/browser-use/jev-ultrafast/issues/156) Hardcoded date in flights example and demo default… | demo-docs-ux | code: src/account_vault/static/app.js:8, tests/test_agent.py:9; drift review §8 |
| [#157](https://github.com/browser-use/jev-ultrafast/issues/157) One transient text-model failure aborts the whole r… | text-helper | code: src/account_vault/model.py:370, src/account_vault/model.py:413, tests/test_regressions.py:176; port notes: Fixed table; drift review §8 |
| [#158](https://github.com/browser-use/jev-ultrafast/issues/158) post_json treats HTTP 200 with an error body as a s… | wire-providers | code: tests/test_regressions.py:110; port notes: Fixed table; drift review §8 |
| [#191](https://github.com/browser-use/jev-ultrafast/issues/191) TYPE_TEXT cache key has no element identity: a stal… | text-helper | code: src/account_vault/agent.py:410, tests/test_regressions.py:192; port notes: Fixed table; drift review §8 |
| [#198](https://github.com/browser-use/jev-ultrafast/issues/198) TYPE_TEXT helper cannot distinguish same-labeled fi… | text-helper | code: src/account_vault/snapshot.js:116, tests/test_regressions.py:192; port notes: Fixed table; drift review §8 |
| [#202](https://github.com/browser-use/jev-ultrafast/issues/202) TYPE_TEXT helper context omits element ID and outer… | text-helper | code: src/account_vault/snapshot.js:116, tests/test_regressions.py:192; port notes: Fixed table; drift review §8 |
| [#203](https://github.com/browser-use/jev-ultrafast/issues/203) TEXT_VALUE contract mismatch: model returning {"tex… | text-helper | code: src/account_vault/model.py:381, tests/test_agent.py:330, tests/test_regressions.py:157; port notes: Fixed table; drift review §8 |
| [#215](https://github.com/browser-use/jev-ultrafast/issues/215) Native input types (date, time, datetime-local, mon… | perceive-enumeration | code: src/account_vault/model.py:122, src/account_vault/snapshot.js:75, src/account_vault/static_dom.py:432; port notes: Fixed table; drift review §8 |
| [#216](https://github.com/browser-use/jev-ultrafast/issues/216) SELECT execution assigns e.value, so duplicate or d… | execute-act | code: src/account_vault/static_dom.py:777, tests/test_browser_cdp.py:673, tests/test_static.py:159; port notes: Fixed table; drift review §8 |
| [#217](https://github.com/browser-use/jev-ultrafast/issues/217) .env loader keeps quotes, inline comments, whitespa… | wire-providers | code: src/account_vault/demo.py:43, tests/test_regressions.py:340; port notes: Fixed table; drift review §8 |
| [#218](https://github.com/browser-use/jev-ultrafast/issues/218) Bare contenteditable / contenteditable="" elements… | perceive-enumeration | code: src/account_vault/snapshot.js:51, tests/test_static.py:28; port notes: Fixed table; drift review §8 |
| [#227](https://github.com/browser-use/jev-ultrafast/issues/227) date inputs are never enumerated, so the agent cann… | perceive-enumeration | code: src/account_vault/model.py:122, src/account_vault/snapshot.js:75, src/account_vault/static_dom.py:432; port notes: Fixed table; drift review §8; drift review P0 |
| [#241](https://github.com/browser-use/jev-ultrafast/issues/241) Decisions endpoint hardcoded to api.typesafe.ai: Ve… | wire-providers | code: src/account_vault/model.py:442, src/account_vault/settings.py:36, tests/test_regressions.py:138; port notes: Fixed table; drift review §8 |
| [#247](https://github.com/browser-use/jev-ultrafast/issues/247) Jev loops through catalog navigation instead of sor… | decide-accuracy | code: src/account_vault/agent.py:216, src/account_vault/agent.py:522, tests/test_regressions.py:475; port notes: Fixed table; drift review §8; drift review P1; port notes: deliberately NOT fixed |

## 5. Doc-vs-code disagreements (fix the docs)

None found at generation time.

## 6. Outside the tracker

Curated sources checked against their primary pages on 2026-11-22 (see `EXTERNAL_SOURCES` in the script). Launch-number recycling is excluded; the drift review's §7 remains the fuller survey.

### [When Jev Breaks: Lessons From Building a Fast Web Agent (TinyFish)](https://www.tinyfish.ai/blog/jev-web-agent-speed-failures) (2026-10-14)

- **Clusters:** decide-accuracy, text-helper, browser-cdp, perception-fidelity
- **Finding:** 142 runs / 1,395 decisions on real sites: Jev averaged 102 ms (9% of step time); acting + settle took 64% at 1.25 s/step. Four failures: the 32K menu budget (8,700 tokens average), no persistent plan on multi-part goals, the decide->type handoff (correct field, wrong text: 'basketball and quantum physics' typed as one query), and settle timing / bot visibility once decisions are fast.
- **Bearing on this port:** Independently reproduces the tracker's text-helper cluster (#191/#198/#202) and decide-accuracy ceiling, and reframes browser-cdp work: settle waits, not model latency, dominate. Supports keeping page/settle deadlines (#14) ahead of more speed.

### [What Can't Jev Decide? Probing the Limits of One-Pass Decision Models (arXiv)](https://arxiv.org/html/2610.24929v1) (2026-10)

- **Clusters:** decide-accuracy
- **Finding:** One-pass decision models cannot make decisions an LLM only reaches by writing intermediate steps; Jev is error-free at find-and-select up to 20k tokens / 64 options, but accuracy falls once the answer depends on two or more updates.
- **Bearing on this port:** Explains why #67/#129/#247-style failures will not yield to prompt tweaks alone: multi-update state tracking needs harness memory/planning (drift P1.5) or escalation.

### [kev-0.6b-browser-use Mind2Web table (1,000 steps, 10 candidates)](https://www.arbazsiddiqui.me/kev-browser-use/) (2026-09-23)

- **Clusters:** decide-accuracy
- **Finding:** Hosted Jev: 43.5% step success (element 47.9%, operation 82.7%); best open in-browser model 32.2%. Different setup from #248's 59.9% (1,500 steps through choose()), so the numbers are not directly comparable -- both agree element choice, not operation choice, is the bottleneck.
- **Bearing on this port:** Corroborates #248's direction with an independent harness: budget effort on the offered set and verification, not on operation vocabulary.

### [Fine-tuning Laya as a browser-agent decision head](https://nandhakishorm.github.io/laya/finetune_browser_agent/) (2026-09)

- **Clusters:** decide-accuracy, wire-providers
- **Finding:** Element top-1 rises from 0.10 (chance, zero-shot) to 0.66 fine-tuned; 16 real browser tasks go 0% -> 62%. Uses the same /v1/systemone request shape as this port.
- **Bearing on this port:** Evidence that a local decider (ADR-001 d1-2B) needs task fine-tuning or a measured baseline before it can be judged -- zero-shot numbers mislead (#248/P1.4).

### [Jev Security Risks: AI Decision Models (HiddenLayer) + The Register coverage](https://www.hiddenlayer.com/insight/jev-security-risks-ai-decision-models) (2026-10-14)

- **Clusters:** decide-accuracy, perception-fidelity, browser-cdp
- **Finding:** On a simulated checkout driven by the jev-ultrafast harness, a planted ad button drew 91% choice probability vs 9% for 'Place order' on the one printed query, despite page text being marked untrusted. Single-call split, no trial count -- an existence proof for injection-via-page-text, not a field rate.
- **Bearing on this port:** For an account vault, existence is enough: keep independent verification and the dedicated-profile default (#16) non-negotiable, and treat page text as adversarial.

### [Research: the Jev / jev-ultrafast agent loop (Browzy)](https://github.com/nguy-n-th-huy/browzy/blob/HEAD/plans/reports/research-260919-0743-jev-agent-loop.md) (2026-09-19)

- **Clusters:** loop-control, decide-accuracy
- **Finding:** Independent code read: upstream has no planner, no prior-step evaluation, no memory and no backtracking -- observe, one request, act, with a 3-step no-change stall ending 'blocked'.
- **Bearing on this port:** Matches the loop-control cluster (#1/#55/#94) and the #247 cycle gap this port patched in the harness; anything smarter is new machinery, not a bug fix.

### [Jev Ultrafast explained / DOM-indexed agent write-ups (boundaries)](https://mattcullerton.com/tokdocs/jev-ultrafast-browser-use-s-dom-indexed-agent-that-picks-an-action-and-a-target/) (2026-09-18)

- **Clusters:** perceive-enumeration
- **Finding:** snapshot.js relies on document.querySelectorAll, so shadow roots, iframes, canvas, uploads, pop-up tabs and nested scrolling are invisible (e.g. Stripe Elements, web components). Derivative jev-max pierces open shadow roots / same-origin iframes and falls back to vision for canvas.
- **Bearing on this port:** The perceive-enumeration fixes (#23/#215/#218) widen the same wall; they do not remove it. Perception PRs stay strategic (drift §4), gated on the successor call.

### [Visual-Jev status: the termination problem](https://github.com/guanxuyu-sv/visual-jev-browser-use/blob/HEAD/docs/status.md) (2026-09-26)

- **Clusters:** loop-control, decide-accuracy
- **Finding:** Mind2Web trajectories contain no DONE/BLOCKED examples at all (offline traces simply stop), so replay benchmarks like #248 cannot validate stopping behaviour.
- **Bearing on this port:** Caveat for the recommended real-model baseline: step accuracy and run completion must be measured separately; a good replay score can still loop or stop early.

## 7. Method and limits

- **Fetch:** `gh issue list --state open --json ...` (exact fields in the script) against `browser-use/jev-ultrafast`, enriched with REST reaction totals. Open PRs (the larger half of the tracker's open items) are deliberately out of scope; the drift review triages the load-bearing PRs.
- **Clustering:** ordered keyword rules over title + body (`CLUSTER_RULES` in the script), first match wins, with a small explicit override table for titles keywords misfile. Comment text informs the hit score and cross-references, not the primary cluster, so a long thread cannot drag an issue sideways.
- **Handled:** an issue counts as handled when a `DEVIATION` comment or regression test cites it, or when the port notes' Fixed table / drift review §8 lists it. Code wins ties; disagreements land in §5 instead of being silently resolved.
- **Hit score:** `2×comments + 3×distinct people + 2×reactions + 3×cross-references + 4×me-too phrases + 3×repro/benchmark attached`. It ranks *visible* pain; silent failures (e.g. a wrong fill nobody noticed) are under-counted by construction — read the cluster tables, not just the ranking.
- **Limits:** keyword clustering can misfile paraphrased reports (unclustered rows are flagged, never dropped); reaction totals are near-zero upstream and barely move the score; comment counts reward discussion, including maintainer replies. Re-run after any pickup batch and diff the report.

## Appendix A. Every open issue

| # | Title | Cluster | Status | Comments | Reactions | Author | Updated |
|---|---|---|---|---|---|---|---|
| [#1](https://github.com/browser-use/jev-ultrafast/issues/1) | Library API: first observation can return an empty action space… | loop-control | handled | 0 | 0 | DevMortimer | 2026-09-17 |
| [#5](https://github.com/browser-use/jev-ultrafast/issues/5) | Fix Windows static asset decoding in demo server | browser-cdp | handled | 2 | 0 | iamkunal9 | 2026-09-22 |
| [#11](https://github.com/browser-use/jev-ultrafast/issues/11) | Support LLMTR as a text-helper provider | wire-providers | handled | 0 | 0 | knowhycodata | 2026-09-17 |
| [#14](https://github.com/browser-use/jev-ultrafast/issues/14) | app not functioning | browser-cdp | handled | 3 | 0 | ahmed-alqasaby | 2026-10-13 |
| [#16](https://github.com/browser-use/jev-ultrafast/issues/16) | Setup friction: SOCKS proxy crash on startup; browser-harness a… | browser-cdp | partial | 0 | 0 | 0xsline | 2026-09-18 |
| [#21](https://github.com/browser-use/jev-ultrafast/issues/21) | How can I get the TYPESAFE_API_KEY? | wire-providers | ignore | 2 | 0 | wxyuwojiusi | 2026-09-22 |
| [#23](https://github.com/browser-use/jev-ultrafast/issues/23) | Element table misses clickable non-native elements, so suggesti… | perceive-enumeration | handled | 2 | 0 | Mind-Hand | 2026-09-23 |
| [#25](https://github.com/browser-use/jev-ultrafast/issues/25) | [Feature Request]: Decouple Jev inference provider to support a… | wire-providers | partial | 1 | 0 | ycmjason | 2026-09-18 |
| [#26](https://github.com/browser-use/jev-ultrafast/issues/26) | Question about the Runtime/JeV boundary in jev-ultrafast | community-noise | ignore | 2 | 0 | goatliamia | 2026-09-22 |
| [#36](https://github.com/browser-use/jev-ultrafast/issues/36) | Missing TEXT_MODEL_API_KEY crashes mid-run; code default base U… | wire-providers | handled | 1 | 0 | MrJev | 2026-09-19 |
| [#46](https://github.com/browser-use/jev-ultrafast/issues/46) | Min | community-noise | ignore | 0 | 0 | mortezagholami912-arch | 2026-09-19 |
| [#47](https://github.com/browser-use/jev-ultrafast/issues/47) | Minin | community-noise | ignore | 0 | 0 | mortezagholami912-arch | 2026-09-19 |
| [#51](https://github.com/browser-use/jev-ultrafast/issues/51) | Windows: owned tab is backgrounded, modal menus do not paint in… | browser-cdp | open | 2 | 0 | richardadonnell | 2026-09-23 |
| [#53](https://github.com/browser-use/jev-ultrafast/issues/53) | agent.py is minified and violates the project's own line-length… | demo-docs-ux | ignore | 1 | 0 | MobinaSafarirad | 2026-09-20 |
| [#54](https://github.com/browser-use/jev-ultrafast/issues/54) | HIGH — README clone URL points to the wrong repository File: ``… | demo-docs-ux | ignore | 1 | 0 | MobinaSafarirad | 2026-09-20 |
| [#55](https://github.com/browser-use/jev-ultrafast/issues/55) | Model-call budget (MAX_STEPS * 2) is undocumented and untested | loop-control | handled | 0 | 0 | MobinaSafarirad | 2026-09-19 |
| [#67](https://github.com/browser-use/jev-ultrafast/issues/67) | Jev ultra fast Can't complete simple task | decide-accuracy | open | 8 | 0 | yonikremer | 2026-09-21 |
| [#77](https://github.com/browser-use/jev-ultrafast/issues/77) | Create main.ym. | community-noise | ignore | 0 | 0 | esmanurdemirci776-glitch | 2026-09-20 |
| [#85](https://github.com/browser-use/jev-ultrafast/issues/85) | Latency from Japan: 12-17s per task - it's the client's link, n… | wire-providers | ignore | 2 | 0 | kuroudo-ai | 2026-09-21 |
| [#87](https://github.com/browser-use/jev-ultrafast/issues/87) | Model responses with invalid JSON leak decoder errors | wire-providers | handled | 0 | 0 | IsaacPu | 2026-09-21 |
| [#93](https://github.com/browser-use/jev-ultrafast/issues/93) | The bundled Google Flights demo can no longer succeed: its goal… | demo-docs-ux | handled | 0 | 0 | ffffj-ai | 2026-09-21 |
| [#94](https://github.com/browser-use/jev-ultrafast/issues/94) | A failed post-action observation (page_changed: null) disables… | loop-control | handled | 2 | 0 | ffffj-ai | 2026-09-23 |
| [#100](https://github.com/browser-use/jev-ultrafast/issues/100) | qr-menu | community-noise | ignore | 0 | 0 | bekzhigit-mr | 2026-09-21 |
| [#115](https://github.com/browser-use/jev-ultrafast/issues/115) | Add a theme switch button | demo-docs-ux | ignore | 1 | 0 | sparshsingh12312 | 2026-09-22 |
| [#120](https://github.com/browser-use/jev-ultrafast/issues/120) | Expose a confidence gate for ordinary browser actions | loop-control | handled | 4 | 0 | 2nd1st | 2026-09-23 |
| [#125](https://github.com/browser-use/jev-ultrafast/issues/125) | Feature Request: Support local resident decision backend (SemIf… | wire-providers | partial | 4 | 0 | ikeikeikeda66 | 2026-09-27 |
| [#129](https://github.com/browser-use/jev-ultrafast/issues/129) | MDN wrong-waypoint actions | decide-accuracy | open | 0 | 0 | marcusbond67 | 2026-09-23 |
| [#132](https://github.com/browser-use/jev-ultrafast/issues/132) | Stale action ids can select a different live control without an… | execute-act | handled | 0 | 0 | EF-Code | 2026-09-23 |
| [#133](https://github.com/browser-use/jev-ultrafast/issues/133) | choose() can return raw KeyError for an off-envelope HTTP-200 r… | wire-providers | handled | 2 | 0 | EF-Code | 2026-09-24 |
| [#140](https://github.com/browser-use/jev-ultrafast/issues/140) | ioio | community-noise | ignore | 1 | 1 | panyachai2018-crypto | 2026-09-24 |
| [#145](https://github.com/browser-use/jev-ultrafast/issues/145) | Community project built on this: jev-browse (agent-callable loo… | community-noise | ignore | 0 | 0 | danielnc | 2026-09-25 |
| [#149](https://github.com/browser-use/jev-ultrafast/issues/149) | Could the visible page state preserve more semantic structure? | perception-fidelity | open | 1 | 0 | fluo-rite | 2026-09-26 |
| [#152](https://github.com/browser-use/jev-ultrafast/issues/152) | Community project built on a bounded Jev loop: Jev Social | community-noise | ignore | 0 | 0 | IRONICBo | 2026-09-26 |
| [#156](https://github.com/browser-use/jev-ultrafast/issues/156) | Hardcoded date in flights example and demo default is now in th… | demo-docs-ux | handled | 0 | 0 | axisrow | 2026-09-27 |
| [#157](https://github.com/browser-use/jev-ultrafast/issues/157) | One transient text-model failure aborts the whole run — no retr… | text-helper | handled | 0 | 0 | axisrow | 2026-09-28 |
| [#158](https://github.com/browser-use/jev-ultrafast/issues/158) | post_json treats HTTP 200 with an error body as a successful re… | wire-providers | handled | 0 | 0 | axisrow | 2026-09-28 |
| [#159](https://github.com/browser-use/jev-ultrafast/issues/159) | Demo UX: a first-time user cannot tell why nothing works — stat… | demo-docs-ux | open | 0 | 0 | axisrow | 2026-09-28 |
| [#177](https://github.com/browser-use/jev-ultrafast/issues/177) | Third-party AI-assisted analysis report on jev-ultrafast | community-noise | ignore | 0 | 0 | weixiongf | 2026-09-29 |
| [#187](https://github.com/browser-use/jev-ultrafast/issues/187) | Idea: Multi-Stage Agent Architecture for Improved Decision Accu… | decide-accuracy | open | 0 | 0 | Aladincz | 2026-10-01 |
| [#191](https://github.com/browser-use/jev-ultrafast/issues/191) | TYPE_TEXT cache key has no element identity: a stale fill's cac… | text-helper | handled | 0 | 0 | AWUQAQ | 2026-10-02 |
| [#198](https://github.com/browser-use/jev-ultrafast/issues/198) | TYPE_TEXT helper cannot distinguish same-labeled fields: field_… | text-helper | handled | 0 | 0 | AWUQAQ | 2026-10-03 |
| [#202](https://github.com/browser-use/jev-ultrafast/issues/202) | TYPE_TEXT helper context omits element ID and outer form contai… | text-helper | handled | 0 | 0 | ege-arhan | 2026-10-04 |
| [#203](https://github.com/browser-use/jev-ultrafast/issues/203) | TEXT_VALUE contract mismatch: model returning {"text": null} fo… | text-helper | handled | 0 | 0 | ege-arhan | 2026-10-04 |
| [#215](https://github.com/browser-use/jev-ultrafast/issues/215) | Native input types (date, time, datetime-local, month, week, ra… | perceive-enumeration | handled | 0 | 0 | rahul05ranjan | 2026-10-15 |
| [#216](https://github.com/browser-use/jev-ultrafast/issues/216) | SELECT execution assigns e.value, so duplicate or disabled opti… | execute-act | handled | 0 | 0 | rahul05ranjan | 2026-10-15 |
| [#217](https://github.com/browser-use/jev-ultrafast/issues/217) | .env loader keeps quotes, inline comments, whitespace, and "exp… | wire-providers | handled | 0 | 0 | rahul05ranjan | 2026-10-15 |
| [#218](https://github.com/browser-use/jev-ultrafast/issues/218) | Bare contenteditable / contenteditable="" elements are never en… | perceive-enumeration | handled | 0 | 0 | rahul05ranjan | 2026-10-15 |
| [#223](https://github.com/browser-use/jev-ultrafast/issues/223) | Add opt-in keep_open mode to preserve and foreground the comple… | loop-control | open | 1 | 0 | moeacgx | 2026-11-01 |
| [#224](https://github.com/browser-use/jev-ultrafast/issues/224) | Choose next 会报错Model provider returned HTTP 451; no action exec… | wire-providers | open | 0 | 0 | Amaza-na | 2026-10-19 |
| [#225](https://github.com/browser-use/jev-ultrafast/issues/225) | When I click "Choose next", I get an error: Model provider retu… | wire-providers | open | 0 | 0 | Amaza-na | 2026-10-19 |
| [#227](https://github.com/browser-use/jev-ultrafast/issues/227) | date inputs are never enumerated, so the agent cannot fill them | perceive-enumeration | handled | 0 | 0 | asleugim | 2026-10-21 |
| [#233](https://github.com/browser-use/jev-ultrafast/issues/233) | AI said： it,s really great | community-noise | ignore | 0 | 1 | hello245m | 2026-11-03 |
| [#235](https://github.com/browser-use/jev-ultrafast/issues/235) | Jev-ultrafast | community-noise | ignore | 0 | 0 | adel-aljabal | 2026-10-28 |
| [#241](https://github.com/browser-use/jev-ultrafast/issues/241) | Decisions endpoint hardcoded to api.typesafe.ai: Vercel AI Gate… | wire-providers | handled | 1 | 0 | PennybagsCX | 2026-11-17 |
| [#242](https://github.com/browser-use/jev-ultrafast/issues/242) | Community project: opencode-browser-use (Jev Ultrafast plugin f… | community-noise | ignore | 0 | 0 | israelsaba | 2026-11-10 |
| [#247](https://github.com/browser-use/jev-ultrafast/issues/247) | Jev loops through catalog navigation instead of sorting products | decide-accuracy | handled | 1 | 0 | oleg-koval | 2026-11-17 |
| [#248](https://github.com/browser-use/jev-ultrafast/issues/248) | Community benchmark: how accurate is the decision model at each… | decide-accuracy | open | 0 | 0 | gauravapiscean | 2026-11-17 |
