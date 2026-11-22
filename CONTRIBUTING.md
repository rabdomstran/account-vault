# Contributing

## Setup

```bash
uv sync --locked --dev   # Python 3.12 or 3.13; no API keys needed for tests
cp .env.example .env      # only needed for live/model-backed runs
```

## The gates

`main` is protected: nothing merges unless every required check passes
on the PR, and the branch must be up to date with `main` first. Run
the same gates locally before pushing:

```bash
uv run ruff check .                       # lint (E, F, I)
uv run ruff format --check .              # formatting — `ruff format .` to apply
uv run mypy                               # strict over src/account_vault
uv run pytest -rs --cov=account_vault --cov-fail-under=89
uv run python scripts/e2e_offline_demo.py # real loop + inspector, offline
uv build                                  # wheel/sdist must ship static assets
ACCOUNT_VAULT_LIVE=1 uv run pytest tests/test_live_cdp.py # if you touched the CDP path; CI gates on it
```

CI (`.github/workflows/ci.yml`) additionally runs the suite on macOS
and Python 3.13, and the live CDP tests headless on Linux. A few scope
notes, so nobody games a gate by accident:

- **Coverage** measures `src/account_vault`; the floor (89%) sits just
  below the actual number. Don't raise it by excluding files — cover
  the code. Tests are outside mypy's scope (Mocks/monkeypatching by
  design) but fully inside ruff's.
- **Skips are visible.** Skip reasons print by default (`-rs` is in
  `addopts`). In the default run the only expected skips are the fourteen
  live-CDP tests; the CI live job runs those for real. A new skip
  needs a reason a reader can act on.
- **pytest runs strict** (`[tool.pytest]` in `pyproject.toml`):
  unregistered markers, duplicate parametrization IDs, and unknown
  config keys are errors, and deprecation warnings fail the run.
  Register new markers in that table.
- **The socket test** (`test_demo.py`, marked `socket`) must pass on
  both Linux and macOS CI; it only skips where binds are forbidden.
- **Live Chrome tests** (`tests/test_live_cdp.py`) are a required CI
  gate (`live (headless Chrome)`, Linux): real headless Chrome, a
  throwaway profile, `file:`/`data:` pages only — no network or keys.
  Locally they stay opt-in:
  `ACCOUNT_VAULT_LIVE=1 uv run pytest tests/test_live_cdp.py` (run that
  file standalone — see its docstring). Opted in with no Chrome
  installed is a failure, not a skip, so the gate can't pass empty.

Code scanning (CodeQL, `.github/workflows/codeql.yml`) runs on PRs,
on pushes to `main`, and weekly. Treat new alerts like failing tests.

## Pull requests

Everything lands via PR — direct pushes to `main` are blocked,
including for admins (`gh pr merge --admin` is the documented
emergency bypass; using it should be rare and explained). Keep PRs
small and single-purpose; the template asks for verification evidence
because this project's core rule is that the decider's `DONE` is never
proof of an outcome.

Dependency PRs come from Dependabot weekly (`.github/dependabot.yml`).
They go through the same gates; a green Dependabot PR still gets a
human skim of the changelog before merging, majors especially.

## Porting conventions

Most of `src/account_vault/` is a port of
[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast).
Deliberate differences carry a `DEVIATION` comment citing the upstream
issue that forced them — keep that discipline (see
[the porting notes](docs/jev-ultrafast-port.md)). Vault data never
enters the repo (`*.vault` / `*.db` are gitignored) and secrets never
enter files — use `.env` / environment variables only.

## Releases

Tag `vX.Y.Z` (matching `version` in `pyproject.toml`) on `main`; the
release workflow builds the distributions, verifies the wheel's
packaged assets, and creates a GitHub Release with them attached.
See `.github/workflows/release.yml`.
