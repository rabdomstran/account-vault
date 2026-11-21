<!-- Keep the title in the repo's style: `feat:`, `fix:`, `test:`, `ci:`, `types:`, `deps:`. -->

## What changed

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- The problem this solves. If this deviates from upstream
jev-ultrafast, the code needs a DEVIATION comment citing the upstream
issue, per docs/jev-ultrafast-port.md. -->

## How it was verified

<!-- Evidence, not assertions: test output, coverage numbers, an e2e
run. The decider's DONE is never verification. -->

## Gate checklist

Branch protection requires all of these on every PR (see CONTRIBUTING.md):

- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run mypy` (strict, `src/account_vault`)
- [ ] `uv run pytest -rs --cov=account_vault --cov-fail-under=89`
- [ ] `uv run python scripts/e2e_offline_demo.py` (if the loop/inspector changed)
- [ ] New behaviour has a test; changed behaviour has an updated test
