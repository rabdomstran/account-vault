# Security pass 2026-11-22 — triage record

Each finding: verdict + evidence in a few lines. Fixes merged as
[#41](https://github.com/rabdomstran/account-vault/pull/41),
[#42](https://github.com/rabdomstran/account-vault/pull/42),
[#43](https://github.com/rabdomstran/account-vault/pull/43), and
the CodeQL-coverage PR that added this file.

## CodeQL (github.com code-scanning API)

- Alert #1 `py/incomplete-url-substring-sanitization` (model.py,
  `api.deepseek.com/` substring): **real, already fixed** before
  this pass — `urlparse(...).hostname ==` equality + regression
  tests for `evil.test/api.deepseek.com/` and
  `api.deepseek.com.evil.test`. Open alerts at pass time: **0**.
- Gap: only `python` was analyzed. In-page JS and workflow files
  now scanned by a second job (this PR). First-run alerts, if any,
  get appended below.

## Dependencies (OSV vs `uv export --locked --all-extras --all-groups`, 85 pkgs)

- `pillow==12.3.0`: **real** — 43 GHSA advisories (decoder heap
  overflows/DoS; screenshots are untrusted input), all fixed in
  13.0.0. `browser-harness==0.1.13` (latest) pins 12.3.0 itself,
  so 13.0.0 comes via `[tool.uv] override-dependencies` (#41);
  re-audit after: **0 vulnerable rows**. Drop the override when
  upstream releases against pillow>=13.
- Everything else: no advisory at the pinned version.
- Dependabot *alerts* were disabled in repo settings (version
  updates were on). Enabled 2026-11-22 via API; 0 open alerts.

## `trust_remote_code` on the d1 checkpoint

- Claim ("code read/scanned at a pinned revision",
  docs/d1-mps-smoke.md): **did not hold for the library default**
  — `revision=None` loads Hub `main`; any caller `model_id` ran
  unpinned remote code. Fixed in #42: default pins the reviewed
  `a6bce774…`; unpinned custom Hub checkpoints are refused;
  local paths exempt.

## Workflows (zizmor + manual)

- Already sound: every action SHA-pinned, least-privilege
  `permissions`, no `pull_request_target`, no untrusted `${{ }}`
  interpolation into `run:` (matrix/`needs` values are static).
  `release.yml`'s job-scoped `contents: write` is required and
  tag-only — accepted, not a finding.
- Real (fixed #43): checkout persisted the token in 8 jobs that
  never push (`artipacked`); release build restored the uv cache
  (`cache-poisoning`). zizmor now: no findings.
- `.env` was missing from `.gitignore` while API keys live
  there — added (#43). Secret-scanning alerts: 0.

## Reviewed, not vulnerabilities

- `static/app.js` innerHTML: every interpolated string goes
  through `escape()`; the rest are numbers from our own JSON.
- `static/fixture.html` innerHTML: curated in-file data plus the
  viewer's own search text, served loopback-only by demo.py
  (Host check + per-process token + Origin check on POST).
- `scripts/` subprocess calls: fixed argv lists, no shell, no
  untrusted input.
