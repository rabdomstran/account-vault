# account-vault

[![CI](https://github.com/rabdomstran/account-vault/actions/workflows/ci.yml/badge.svg)](https://github.com/rabdomstran/account-vault/actions/workflows/ci.yml)

Ultrafast System-One browsing agent with a local-first encrypted account store.

The agent runs a tight **perceive → decide → act** loop where each step costs
milliseconds, not the seconds a frontier LLM needs to generate and parse
free-form text. The decide step is a System-One model: a single forward pass
that returns typed answers (`choice` / `noul` / `score`) with calibrated
probabilities — nothing to parse. Later the agent will run flows end to end
(form filling, creating its own logins), which is why an encrypted,
local-first account store is part of the same project. Vault data lives
outside the repo at `~/.local/share/account-vault/` and is never committed
(`*.vault` / `*.db` are gitignored).

**Status:** v0.1.0 — the perceive → decide → act loop is implemented in
`src/account_vault/` (ported from jev-ultrafast; see
[porting notes](docs/jev-ultrafast-port.md) for what changed and why) and
runs end to end against a static HTML file today:

```bash
uv run python -m account_vault   # offline demo on examples/hotel_search.html
uv run account-vault-demo        # inspector UI on http://127.0.0.1:8766 (offline mode default)
uv run pytest                    # 109 tests, no API keys (4 live-CDP tests are opt-in)
uv run python scripts/e2e_offline_demo.py  # end-to-end: CLI demo + inspector over HTTP
ACCOUNT_VAULT_LIVE=1 uv run pytest tests/test_live_cdp.py  # real Chrome, dedicated profile
```

The live-Chrome (CDP) backend is exercised end to end against a real
(dedicated-profile) Chrome driving the JS fixture to DONE — see
`tests/test_live_cdp.py`. The System-One wire client is unit-tested with
mocks but not yet exercised against a real decide model. The vault and
eval harness are not yet implemented. The model decision is settled and
recorded in [ADR-001](docs/adr-001-model-choice.md).

## How it works

The loop design is adopted from
[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast):

1. **Indexed action space.** Each observation reads visible DOM controls
   into a numbered element table (`[1] button …`, `[2] combobox …`). The
   model never emits selectors, coordinates, or code — only an operation
   (`CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP/DOWN`, `WAIT`, `DONE`,
   `BLOCKED`) and an element index that must resolve to an observed node.
2. **One request per cycle.** A single `POST /v1/systemone` call carries the
   shared state (URL, title, visible text, element table, last 10 actions)
   plus parallel questions: one operation choice and one speculative target
   choice per operation type. Only the selected operation's target executes.
   Two decisions, one round trip.
3. **No screenshots in the loop by default.** Structured DOM state is the
   input; screenshots are for inspection and for the perception fallback
   (below) only.
4. **Text is the only generation.** `TYPE_TEXT` delegates to a small
   OpenAI-compatible model that must return a tiny validated JSON object.
   Values already on the page are served by the decide model's extraction
   types, and vault values are injected from the local encrypted store —
   never generated.

The reference runs set the budget this project aims to match or beat:
Google Flights (Zürich → London) in 7.073 s end to end across 17
System-One requests, with a 178 ms median decide latency. The target for
the decide step here is **< 200 ms p50 end to end** (network included),
ideally **< 50 ms of model compute**, so browser waits — not inference —
dominate. See ADR-001 for the full evidence and requirements (R1–R8).

## Model choice

The primary decide model is **Liquid AI d1-2B** (1.92B parameters,
post-trained from `LFM3-VL-2B`), run locally behind the TypeSafe
`/v1/systemone` contract. Hosted **Jev 1.14** is retained as (a) the
bootstrap/baseline for evaluation and (b) an explicit escalation path for
low-confidence steps. **Base Labs joey-1** is the designated fallback open
model. **Cactus Mirage** is adopted only as an optional perception
fallback (screenshots → structured JSON with boxes), not as the decider;
primary perception stays the DOM snapshot.

Why d1-2B:

- **Local, millisecond-class inference.** Vendor-measured at 5.7 ms per
  decision on an NVIDIA H100 (Apple Silicon MPS latency is still to be
  verified locally), with no network round trip and no per-step API cost.
  Credential-bearing page state never has to leave the machine — decisive
  for an agent whose roadmap is creating and storing its own logins.
- **Largest validated context among the small finalists:** 128k tokens
  (answers get less reliable past ~64k of state), 8× joey-1's 16k limit.
- **Extra heads fit the loop:** typed extraction with spans and
  `box`/`boxes` visual grounding, plus native text+image+video input for
  the screenshot fallback path. Served natively by vLLM as a
  structured-decisions model.
- **One wire contract, two backends.** The loop is written once against
  the System-One request/response shape. Swapping local d1-2B for hosted
  Jev is an environment-variable change, never a loop change.

Trade-offs and risks (all recorded in ADR-001): d1-2B ships under the
**LFM Open License v1.0**, not Apache-2.0 — commercial terms must be
reviewed before any commercial deployment, and this is the pick's biggest
non-technical risk. Browser-specific accuracy is unproven until the
validation gate in ADR-001 passes (tasks: Google Flights, Wikipedia,
local hotel filter; each with an independent outcome checker; keep d1-2B
only if it stays within one failure of Jev and sustains < 100 ms p50
decide latency on the Apple M4 dev machine). joey-1 has the strongest
small-model public accuracy (63.30 on the Jev Decision Index 0.4 public
split, ahead of Jev 1.14's 60.70) and the cleanest license (MIT +
Apache-2.0), but its self-hosting path requires a CUDA GPU, which makes
it hosted-in-practice on Apple Silicon today.

Read [ADR-001](docs/adr-001-model-choice.md) for the side-by-side
comparison, rejected alternatives (frontier LLMs as decider, 27B–31B open
leaders, Kev-4B, CLM-8B, tiny encoders, Mirage alone, and others), and
the revisit triggers.

## Project layout

```
docs/adr-001-model-choice.md   # decide-model decision (read this first)
docs/jev-ultrafast-port.md     # what was ported, upstream issues, deviations
pyproject.toml                 # metadata, dependencies, tool config
.env.example                   # backend + text-model settings template
src/account_vault/             # agent loop (agent/model/browser/snapshot.js)
                               # + offline static_dom backend, settings,
                               # inspector demo (demo.py + static/: upstream
                               # JS fixture + static-rendered catalog twin)
examples/hotel_search.html     # static fixture the loop runs against
tests/                         # ported contracts + upstream regression tests
~/.local/share/account-vault/  # encrypted vault data (never in the repo)
```

Core dependencies (see `pyproject.toml`): `httpx[http2]` (System-One wire
client), `browser-use` + `browser-harness` (browser driving),
`pydantic` + `python-dotenv` (schemas/settings), and `cryptography` +
`argon2-cffi` (vault AEAD + KDF). The heavy torch stack for local d1-2B
and `cactus-compute` for Mirage are optional extras, so the hosted-Jev
path installs in seconds.

## Setup

Requires Python `>=3.12, <3.14`. The virtual environment lives inside
the project directory as `.venv` (auto-detected by editors and tooling)
and is managed with [uv](https://docs.astral.sh/uv/). Do not install into
the system interpreter — on Homebrew/Debian/Ubuntu that interpreter is
externally managed and `pip install` will refuse; creating the project
environment is the intended path, not `--break-system-packages`.

From the project root:

```bash
# 1. Create the project-local venv (skip if .venv already exists)
uv venv

# 2. Install the core loop (hosted-Jev capable; light)
uv pip install -e .

# Optional: local d1-2B decide model (torch/transformers; heavy)
uv pip install -e ".[local]"

# Optional: Mirage screenshot-perception fallback
uv pip install -e ".[perception]"

# Development tools (pytest, ruff)
uv sync --group dev
```

`uv sync` installs from `pyproject.toml` into the same `.venv` and is
equivalent for dependency resolution; use whichever you started with
rather than mixing in a second environment.

Run code in the project environment without activating:

```bash
uv run python -c "import account_vault; print(account_vault.__version__)"
# 0.1.0
```

Or activate it for an interactive shell:

```bash
source .venv/bin/activate
python -c "import account_vault; print(account_vault.__version__)"
```

If `uv` is unavailable, the stdlib fallback creates the same layout:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration

Copy `.env.example` to `.env` and fill in the keys. The decide backend is
selected by environment variable (ADR-001, Decision §2):

| Variable | Meaning |
| --- | --- |
| `SYSTEMONE_BASE_URL` | Local vLLM server (`http://127.0.0.1:8000`) by default; `https://api.typesafe.ai` for hosted Jev |
| `SYSTEMONE_MODEL` | `d1-2B` locally; Jev model id when hosted |
| `SYSTEMONE_API_KEY` | Hosted API key (empty for local) |
| `TEXT_MODEL_BASE_URL` / `TEXT_MODEL` / `TEXT_MODEL_API_KEY` | Small OpenAI-compatible model used only for `TYPE_TEXT` (defaults: OpenRouter, `inception/mercury-2.5`) |
| `TEXT_MODEL_REASONING` | `none` — reasoning disabled for the text helper |

Confidence gating (planned, per ADR-001): if the operation or target
confidence falls below a tuned threshold, the step re-observes once and
may escalate to hosted Jev — never with vault secrets or credential field
values in state; those steps are local-only by policy.

## Development

```bash
source .venv/bin/activate   # or prefix each command with `uv run`
ruff check src              # lint (line-length 120; rules E, F, I)
pytest                      # tests (testpaths = tests/)
```

## License

MIT (project code). Note the local decide model itself is under the LFM
Open License v1.0 — see Model choice above before any commercial use.
