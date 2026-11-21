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
| [#215](https://github.com/browser-use/jev-ultrafast/issues/215), [#227](https://github.com/browser-use/jev-ultrafast/issues/227) | `date/time/datetime-local/month/week/range/color` inputs map to `null` role and never reach the element table — unfillable | Mapped to fillable roles with `input_type` preserved (both backends) |
| [#218](https://github.com/browser-use/jev-ultrafast/issues/218) | Selector matches only `[contenteditable="true"]`; bare/`""` editors never enumerated | Selector matches `[contenteditable]`; `isContentEditable` confirms |
| [#23](https://github.com/browser-use/jev-ultrafast/issues/23) | Bare `li/div/span` suggestion rows with click handlers are invisible; loop retypes forever | `[onclick]` elements enumerated as `button` (bounded by the 250-action cap) |
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
| [#120](https://github.com/browser-use/jev-ultrafast/issues/120) | Confidence recorded but never gates ordinary actions | Optional `min_confidence` gate (re-observe once, then `blocked`); default off = upstream behaviour; ADR-001 escalation hook |
| [#55](https://github.com/browser-use/jev-ultrafast/issues/55) | `MAX_STEPS * 2` model-call budget undocumented; error names no number | `MAX_ACTIONS=60` / `MAX_DECISIONS=120`, both named in errors and tested |
| [#16](https://github.com/browser-use/jev-ultrafast/issues/16) (part) | `httpx.Client` built at import time crashes with SOCKS env vars; browser import requires harness | Lazy HTTP client and lazy `browser_harness` import; offline path imports cleanly anywhere |
| [#11](https://github.com/browser-use/jev-ultrafast/issues/11) | Text helper always sends a `reasoning` field some providers reject (HTTP 400) | Reasoning driven only by config; `TEXT_MODEL_REASONING=omit` sends nothing |
| [#247](https://github.com/browser-use/jev-ultrafast/issues/247) | Page-changing navigation cycle (`sort → index → Coffees`, 44 actions): every step changes the page so repeat/unchanged guards never fire. **No upstream fix to adopt** — issue open at HEAD `cf3c406`, 0 commits since, no linked PR (adjacent [#245](https://github.com/browser-use/jev-ultrafast/pull/245)/[#209](https://github.com/browser-use/jev-ultrafast/pull/209)/[#212](https://github.com/browser-use/jev-ultrafast/pull/212) address cousins, not cycles) | Adopted the issue comment's bounded form: same (action label, landing URL) 3× in last 12 actions → `blocked` + reason. Blocking instead of dropping the control keeps the one-request contract |
| [#5](https://github.com/browser-use/jev-ultrafast/issues/5) | Inspector reads static assets without encoding → Windows UTF-8 failure | Explicit `encoding="utf-8"` in [demo.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/demo.py) (same fix as upstream PR [#208](https://github.com/browser-use/jev-ultrafast/pull/208)) |

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
  `keep_open`, part of [#16](https://github.com/browser-use/jev-ultrafast/issues/16)
  attaching to the user's real browser, [#14](https://github.com/browser-use/jev-ultrafast/issues/14)
  screenshot timeouts): upstream lifecycle choices kept verbatim.
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
- **`python -m account_vault`** ([__main__.py](/Users/jagadesh/Documents/Projects/account-vault/src/account_vault/__main__.py)):
  runs the loop against [examples/hotel_search.html](/Users/jagadesh/Documents/Projects/account-vault/examples/hotel_search.html)
  (which exercises the date/contenteditable/duplicate-option fixes) and
  verifies the outcome independently. `--decider systemone` drives the
  same file with the real backend from `.env`.

## Verification (2026-11-21)

```bash
.venv/bin/python -m pytest -q        # 62 passed, 2 skipped (live-CDP opt-in)
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
