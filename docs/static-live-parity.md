# Static/live perception parity: why two engines, and what keeps them honest

- **Date:** 2026-11-22
- **Status:** Accepted — keep both engines; enforce the invariant below.
- **Scope:** `src/account_vault/snapshot.js` (+ `browser.py`/`act.js`,
  the live CDP backend) vs `src/account_vault/static_dom.py`
  (the offline backend). Upstream
  [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast)
  has **no such split**: it ships only the live snapshot, so it cannot
  run its loop in CI or unit tests without mocking the browser. The
  static backend is this port's addition (see its module docstring)
  and exists to run the exact same browser contract —
  `observe()/fresh()/act()` — in unit tests, the offline demo/e2e,
  and the latency harness, with no Chrome and no daemon.

Two implementations of one semantics is where drift breeds, so this
document records (1) whether they can be unified or one generated
from the other, (2) precisely where they are specified to disagree,
and (3) the invariant that is tested so disagreement anywhere else
is a bug, not a discovery.

## The invariant

> **On a page whose every perception signal is in the markup itself**
> (attributes and inline `style=` — nothing a stylesheet, layout, or
> script decides), the two backends must produce the **same action
> table in the same order**, canonicalised to what the decider sees:
> `(kind, role, label, value, checked, input_type, current_value)`
> per action, excluding scroll/wait offers.

Order is part of the invariant because action ids are positional.
Excluded by construction: geometry (`rect` — static actions carry
none), node ids (different allocation schemes), scroll offers and
scroll state, screenshots, and the freshness encodings
(`marker`/`page_key`/`guards` shapes differ — they are only ever
compared within one backend, and `Agent` picks its backend once at
construction, so a cross-backend comparison can never occur;
`fingerprint()` itself is shared code, but its `scroll` input
differs, so fingerprints are likewise backend-local).

Enforced in `tests/test_live_cdp.py`
(`test_live_static_parity_on_markup_only_signals`, real Chrome in
the CI `live` gate; a differential test of a JS program cannot run
in the offline suite by definition). The complement —
`test_live_static_stylesheet_signals_are_the_pinned_divergence` —
pins the specified disagreements below so they cannot silently
widen. Its first run caught two real drifts, both fixed and pinned
offline in `tests/test_static.py`: options inside a disabled
`<optgroup>` were offered statically (live never offers them), and
a valueless checkbox reported `""` statically instead of the HTML
default `"on"` that `snapshot.js` reads from `e.value`.

Declarative tables no longer count as drift surface at all:
`ELEMENT_ROLES`, `SAFE_EXCLUDE`, `EDITABLE_ROLES` and
`NATIVE_FILL_TYPES` are declared once in `browser.py`, generated
into the JS at load (`__TOKENS__` in `snapshot.js`/`act.js`), and
imported by `static_dom.py`
(`test_perception_tables_are_generated_from_one_source`,
`test_native_fill_taxonomy_agrees_across_layers`).

## Where they are specified to disagree

Every row is a signal whose *source* does not exist offline. The
static backend approximates from markup; fixtures that need the
live answer declare the signal inline (that is how
`static/catalog.html`, the static-rendered twin of the JS fixture,
stays honest).

| Signal | Live source | Static approximation | Consequence |
|---|---|---|---|
| Stylesheets | CSS cascade | not read at all | class-hidden controls offered statically; stylesheet `cursor:pointer` rows not offered |
| Visibility | `checkVisibility` (opacity + CSS) | `hidden`/`aria-hidden`/`inert` attrs + inline `display/visibility` | stylesheet-hidden subtrees visible statically |
| Geometry | `getBoundingClientRect` vs viewport | no layout, no rect | below-fold / zero-size controls offered statically |
| Concealment | rect is 0 or invisible | inline `opacity:0` or `width/height:0` | class-concealed radios keep no label proxy statically unless declared inline |
| Cursor / position | `getComputedStyle` | inline `style=` only (`_style_map`) | pointer rows, pointer lists, overlay context missed when stylesheet-set |
| Occlusion | `elementFromPoint` hit test + covered set | none | covered controls offered and "clickable" statically; no `CoveredTarget` path |
| Scripts | full JS execution | none | JS-rendered content invisible (the fixture boundary already pinned in `test_static.py`); `addEventListener`/property handlers unseen — `onclick` attr and `data-clickable`/`data-action` only |
| Page text | visible text nodes, viewport-clipped | document-order text of "visible" elements | text differs on scrolled/clipped/relayouted pages |
| Scroll | real offsets + scroll offers | fixed `{y: 0, height: 780}`, no offers | scroll tasks are live-only |
| Screenshots | JPEG pixels | absent | — |

Two internal-only differences are observable in neither direction
and listed only so nobody "fixes" them blind: the static guard's
scope uses the action-scope selectors (with `fieldset`, 600 chars)
where the live guard keeps upstream's selectors (6000 chars), and
the static guard appends an option count. Guards are backend-local.

Actuation is *not* a mirror at all and cannot be one: live `act`
dispatches real CDP input after a geometry/occlusion recheck;
static `act` mutates the parsed tree (plus `valid_native_value`
standing in for Chrome's sanitisation, a submit-reveal rule for
fixtures, and relative-file navigation). They share only the
contract and the verdict strings.

## Unification options, costed

**1. Delete the static backend; use CDP for `file:` pages too.**
One engine, perfect parity by construction. Rejected: it deletes
the backend's reason to exist. The offline suite (200+ tests), the
inspector's offline mode, `scripts/e2e_offline_demo.py`, and the
static side of the latency harness all run with no browser, no
daemon, and no sockets; the CDP path additionally requires the
dedicated-Chrome attach guard (#16) satisfied before a single test
can start. Upstream lives with exactly this gap (its tests mock the
browser); this port deliberately closed it.

**2. Run `snapshot.js` in jsdom for static pages.** jsdom has no
layout engine: `getBoundingClientRect()` returns zeros and layout
properties are 0 (its own README lists layout under unimplemented
parts of the web platform). `snapshot.js`'s offer gate
(`centered()` — nonzero rect, centre inside the viewport) would
therefore offer *nothing at all* until we shim geometry, and
occlusion (`elementFromPoint`), real scroll offsets, and painted
visibility have no jsdom answer either. The shims would be a new
layout model — a third implementation, carrying the same divergence
questions as the static twin, plus a Node + jsdom dependency in a
currently pure-Python (`uv`) project whose library users would
inherit it. And actuation (fills, selects, the submit-reveal rule)
still has no JS counterpart to reuse.

**3. Run `snapshot.js` in an embeddable Python JS engine**
(QuickJS/Duktape-style bindings). Worse than jsdom: those provide a
language runtime with no DOM at all, so the entire DOM surface
`snapshot.js` uses (`querySelectorAll`, `checkVisibility`,
`getComputedStyle`, live node identity, …) becomes our code to
write — the static engine again, behind a harder interface.

**4. Generate one implementation from the other** (transpile
`snapshot.js` to Python, or emit both from a spec). The two
programs share decisions, not primitives: nearly every snapshot.js
helper bottoms out in a browser API with no `html.parser`
counterpart (see the table above). A generator would embed a
translator plus per-API adapters — the divergence moves into the
adapters, where it is harder to see and to test than in a direct
mirror. The part that *is* pure data — the role/type tables — is
generated from one source already (above); that is the honest
boundary of "generate one from the other" today.

**5. Keep both engines; test the invariant.** (Chosen.) The
must-agree semantics are enumerable and already mirrored line by
line; the disagreements are a short, closed list of signal sources
that provably do not exist offline. A differential test over
markup-only pages plus pinned divergences converts future drift
from a silent behaviour difference between CI and a real browser
into a failing gate. Cost when perception changes: extend both
engines and the parity fixtures in the same PR (the issue
analyser's standing instruction), or — if the new signal is
computed-only — add it to the pinned-divergence test and the table
above instead of approximating it silently.

### What would change this verdict

- The project takes a Node toolchain dependency anyway, **and**
  accepts a geometry shim whose answers are defined as "whatever
  the fixture declares" — at that point option 2 becomes a real,
  if still partial, unification of perception (never actuation).
- A layout-capable, embeddable engine with maintained Python
  bindings appears (a real cascade + box model, not a JS runtime).
- The offline backend's consumers go away (no CI/e2e/harness need),
  making option 1 free.

Until one of those holds, two engines plus the invariant is the
cheaper honest structure: the duplicated part is the enumerable
semantics (now differentially tested), and the non-duplicable part
is physics the offline world does not have.
