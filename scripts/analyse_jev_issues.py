"""Analyse the open-issue corpus of browser-use/jev-ultrafast against this port.

Pulls every open *issue* (not PR) with bodies, labels, comment counts and
comment text via the authenticated ``gh`` CLI, clusters the issues by
failure mode, cross-references each one against what this port already
handles (``docs/jev-ultrafast-port.md``, ``docs/jev-ultrafast-drift-review.md``
and the ``DEVIATION`` comments / regression tests in ``src/`` and
``tests/``), ranks the remainder by how often people appear to hit them,
and writes a Markdown report a maintainer can work from directly.

Usage (from the repo root)::

    uv run python scripts/analyse_jev_issues.py                # live fetch -> docs/jev-ultrafast-issue-analysis.md
    uv run python scripts/analyse_jev_issues.py --save-corpus /tmp/jev-corpus.json
    uv run python scripts/analyse_jev_issues.py --input /tmp/jev-corpus.json --output /tmp/report.md

``--input`` replays a previously saved corpus with no network or ``gh``
call, which is how the unit tests exercise this file. The "hit" ranking
is an engagement proxy (comments, distinct people, reactions,
cross-references) -- the tracker carries no traffic telemetry, and the
report says so. External writing about these failure modes cannot be
fetched reproducibly by this script, so the curated sources live in
:data:`EXTERNAL_SOURCES` below with the date each was last checked;
refresh them by re-searching, not by scraping at report time.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO = "browser-use/jev-ultrafast"
ISSUE_URL = "https://github.com/{repo}/issues/{{number}}".format(repo=REPO)
ROOT = Path(__file__).resolve().parents[1]
PORT_DOC = ROOT / "docs" / "jev-ultrafast-port.md"
DRIFT_DOC = ROOT / "docs" / "jev-ultrafast-drift-review.md"
DEFAULT_OUTPUT = ROOT / "docs" / "jev-ultrafast-issue-analysis.md"

# ---------------------------------------------------------------------------
# Failure-mode taxonomy. Rules run in order; the first match wins, so the
# most specific signals (spam/showcase, TYPE_TEXT) come before broad ones
# (decide, wire). Every keyword is matched case-insensitively against
# title + body. Keep this list readable: it is the report's method.
# ---------------------------------------------------------------------------

CLUSTER_ORDER = [
    "decide-accuracy",
    "loop-control",
    "text-helper",
    "perceive-enumeration",
    "execute-act",
    "perception-fidelity",
    "wire-providers",
    "browser-cdp",
    "demo-docs-ux",
    "community-noise",
]

CLUSTER_BLURBS = {
    "decide-accuracy": (
        "The decider picks the wrong operation/target (or loops over right-looking picks). "
        "Includes wrong-waypoint reports, simple-task failures, cycling, and accuracy benchmarks/planner proposals."
    ),
    "loop-control": (
        "Run budgets and stopping: empty first observations, no-progress detection, confidence gating, tab lifecycle."
    ),
    "text-helper": (
        "The second model that writes field text: element identity, stale-fill cache, null-value contract, retry."
    ),
    "perceive-enumeration": (
        "The snapshot's element table misses or mislabels controls, so the right action is never offered."
    ),
    "execute-act": (
        "A correct decision is applied to the wrong control or wrong value (stale ids, duplicate options)."
    ),
    "perception-fidelity": (
        "What the decider is shown about the page (flattened visible text, lost structure), short of missing elements."
    ),
    "wire-providers": (
        "Model HTTP surface: endpoints, keys, .env parsing, error envelopes, region blocks, provider configurability."
    ),
    "browser-cdp": ("Chrome/CDP harness behaviour: profile attach, proxies, tab painting, hangs and deadlines."),
    "demo-docs-ux": (
        "Inspector demo, README/docs and first-run experience. Real friction, but it does not change loop behaviour."
    ),
    "community-noise": (
        "Showcase posts, empty/garbled reports, spam and pure questions. Counted to reconcile; no work implied."
    ),
}

# (cluster, [substrings]) -- first matching cluster wins.
CLUSTER_RULES: list[tuple[str, list[str]]] = [
    (
        "community-noise",
        [
            "community project",
            "community benchmark",  # benchmark re-routed below by override
            "third-party ai-assisted",
            "ai said",
            "qr-menu",
            "create main.ym",
            "jev social",
            "opencode-browser-use",
            "jev-browse",
            "question about the runtime",
        ],
    ),
    (
        "text-helper",
        [
            "type_text",
            "text_value",
            "text-helper",
            "text helper",
            "field_text",
            "field_context",
            "missing values triggers",
            "text-model failure",
            "transient text-model",
            "llmtr",
            "reasoning field",
        ],
    ),
    (
        "execute-act",
        [
            "stale action ids",
            "select execution assigns",
            "e.value",
            "wrong option",
            "duplicate or disabled options",
        ],
    ),
    (
        "perceive-enumeration",
        [
            "element table misses",
            "never enumerated",
            "never reach the element table",
            "native input types",
            "contenteditable",
            "textarea",
            "suggestion rows",
            "clickable non-native",
            "date inputs are never enumerated",
        ],
    ),
    (
        "decide-accuracy",
        [
            "can't complete simple task",
            "cant complete simple task",
            "cannot complete",
            "wrong-waypoint",
            "loops through",
            "loop",
            "mind2web",
            "decision model at each step",
            "multi-stage agent architecture",
            "decision accuracy",
            "benchmark",
        ],
    ),
    (
        "perception-fidelity",
        ["visible page state preserve", "semantic structure", "flattened", "page text"],
    ),
    (
        "loop-control",
        [
            "first observation can return an empty",
            "model-call budget",
            "max_steps",
            "three-repeat",
            "no-progress",
            "confidence gate",
            "keep_open",
            "page_changed: null",
            "failed post-action observation",
        ],
    ),
    (
        "browser-cdp",
        [
            "socks proxy",
            "real browser by default",
            "backgrounded",
            "do not paint",
            "app not functioning",
            "screenshot",
            "bringtofront",
            "windows static asset",
        ],
    ),
    (
        "wire-providers",
        [
            "http 451",
            "http-200",
            "http 200",
            "invalid json",
            "keyerror",
            "post_json",
            "api_key",
            ".env loader",
            "hardcoded to api.typesafe",
            "inference provider",
            "local resident decision backend",
            "model context protocol",
            "latency from japan",
            "base url differs",
            "typesafe_api_key",
        ],
    ),
    (
        "demo-docs-ux",
        [
            "demo ux",
            "theme switch",
            "clone url",
            "minified",
            "flights demo",
            "google flights demo",
            "hardcoded date",
            "goal date",
            "invisible",
            "cannot tell why nothing works",
        ],
    ),
]

# Titles too short/garbled for keywords to mean anything. Verified against
# the live corpus titles at authoring time; the drift review reaches the
# same verdict for each of these numbers (docs/jev-ultrafast-drift-review.md §4).
NOISE_NUMBERS = {46, 47, 77, 100, 140, 233, 235}

# Keyword order alone misfiles a handful of issues; pin them explicitly.
CLUSTER_OVERRIDES = {
    248: "decide-accuracy",  # "Community benchmark" would hit community-noise first
    11: "wire-providers",  # provider-compat (reasoning field), not field-text semantics
    5: "browser-cdp",  # Windows demo-server decoding; fixed via utf-8, harness-adjacent
    54: "demo-docs-ux",  # README clone URL (title carries no rule keyword verbatim)
    53: "demo-docs-ux",  # minified agent.py readability complaint
    26: "community-noise",  # architecture question, no defect claimed
    93: "demo-docs-ux",  # Flights demo goal date; body text trips text-helper rules
    156: "demo-docs-ux",  # same hardcoded-date family as #93
    227: "perceive-enumeration",  # "never enumerated"; CDP fill half tracked as residual
    149: "perception-fidelity",  # structured visible text, not pick accuracy
    51: "browser-cdp",  # background-tab painting, not pick accuracy
    25: "wire-providers",  # backend decoupling request
    125: "wire-providers",  # local backend + MCP request
}

# Issues the docs describe as only partly fixed. A row may still promote to
# "handled" when a DEVIATION/test covers it -- the resolver reports that as
# a doc-vs-code disagreement instead of silently trusting either side.
# Cited in code/tests as *context* (a fork's behaviour, a cousin guard),
# not as a fix. The port notes explicitly list the underlying problem as
# NOT fixed, so a citation must not promote these to handled.
NOT_A_FIX = {
    67: "cited by the no-op/cycle spend guards; decision accuracy itself is explicitly NOT fixed (see #248)",
}

PARTIAL_NOTES = {
    16: "attach default fixed in code; SOCKS first-call crash remains (httpx lacks the socks extra)",
    25: "decider object + base URL landed; wire shape still /v1/systemone",
    125: "local backend landed; no MCP stdio server",
}

STATUSES = ("handled", "partial", "open", "ignore", "unclassified")

# Curated writing outside the tracker, each checked against its primary
# page on 2026-11-22 (search + fetch). "bearing" is what it changes for
# this port -- opinion pieces that only recycle launch numbers are out.
EXTERNAL_SOURCES = [
    {
        "title": "When Jev Breaks: Lessons From Building a Fast Web Agent (TinyFish)",
        "url": "https://www.tinyfish.ai/blog/jev-web-agent-speed-failures",
        "date": "2026-10-14",
        "clusters": ["decide-accuracy", "text-helper", "browser-cdp", "perception-fidelity"],
        "finding": (
            "142 runs / 1,395 decisions on real sites: Jev averaged 102 ms (9% of step time); "
            "acting + settle took 64% at 1.25 s/step. Four failures: the 32K menu budget "
            "(8,700 tokens average), no persistent plan on multi-part goals, the decide->type "
            "handoff (correct field, wrong text: 'basketball and quantum physics' typed as one "
            "query), and settle timing / bot visibility once decisions are fast."
        ),
        "bearing": (
            "Independently reproduces the tracker's text-helper cluster (#191/#198/#202) and "
            "decide-accuracy ceiling, and reframes browser-cdp work: settle waits, not model "
            "latency, dominate. Supports keeping page/settle deadlines (#14) ahead of more speed."
        ),
    },
    {
        "title": "What Can't Jev Decide? Probing the Limits of One-Pass Decision Models (arXiv)",
        "url": "https://arxiv.org/html/2610.24929v1",
        "date": "2026-10",
        "clusters": ["decide-accuracy"],
        "finding": (
            "One-pass decision models cannot make decisions an LLM only reaches by writing "
            "intermediate steps; Jev is error-free at find-and-select up to 20k tokens / 64 "
            "options, but accuracy falls once the answer depends on two or more updates."
        ),
        "bearing": (
            "Explains why #67/#129/#247-style failures will not yield to prompt tweaks alone: "
            "multi-update state tracking needs harness memory/planning (drift P1.5) or escalation."
        ),
    },
    {
        "title": "kev-0.6b-browser-use Mind2Web table (1,000 steps, 10 candidates)",
        "url": "https://www.arbazsiddiqui.me/kev-browser-use/",
        "date": "2026-09-23",
        "clusters": ["decide-accuracy"],
        "finding": (
            "Hosted Jev: 43.5% step success (element 47.9%, operation 82.7%); best open "
            "in-browser model 32.2%. Different setup from #248's 59.9% (1,500 steps through "
            "choose()), so the numbers are not directly comparable -- both agree element "
            "choice, not operation choice, is the bottleneck."
        ),
        "bearing": (
            "Corroborates #248's direction with an independent harness: budget effort on the "
            "offered set and verification, not on operation vocabulary."
        ),
    },
    {
        "title": "Fine-tuning Laya as a browser-agent decision head",
        "url": "https://nandhakishorm.github.io/laya/finetune_browser_agent/",
        "date": "2026-09",
        "clusters": ["decide-accuracy", "wire-providers"],
        "finding": (
            "Element top-1 rises from 0.10 (chance, zero-shot) to 0.66 fine-tuned; 16 real "
            "browser tasks go 0% -> 62%. Uses the same /v1/systemone request shape as this port."
        ),
        "bearing": (
            "Evidence that a local decider (ADR-001 d1-2B) needs task fine-tuning or a "
            "measured baseline before it can be judged -- zero-shot numbers mislead (#248/P1.4)."
        ),
    },
    {
        "title": "Jev Security Risks: AI Decision Models (HiddenLayer) + The Register coverage",
        "url": "https://www.hiddenlayer.com/insight/jev-security-risks-ai-decision-models",
        "date": "2026-10-14",
        "clusters": ["decide-accuracy", "perception-fidelity", "browser-cdp"],
        "finding": (
            "On a simulated checkout driven by the jev-ultrafast harness, a planted ad button "
            "drew 91% choice probability vs 9% for 'Place order' on the one printed query, "
            "despite page text being marked untrusted. Single-call split, no trial count -- "
            "an existence proof for injection-via-page-text, not a field rate."
        ),
        "bearing": (
            "For an account vault, existence is enough: keep independent verification and the "
            "dedicated-profile default (#16) non-negotiable, and treat page text as adversarial."
        ),
    },
    {
        "title": "Research: the Jev / jev-ultrafast agent loop (Browzy)",
        "url": "https://github.com/nguy-n-th-huy/browzy/blob/HEAD/plans/reports/research-260919-0743-jev-agent-loop.md",
        "date": "2026-09-19",
        "clusters": ["loop-control", "decide-accuracy"],
        "finding": (
            "Independent code read: upstream has no planner, no prior-step evaluation, no "
            "memory and no backtracking -- observe, one request, act, with a 3-step no-change "
            "stall ending 'blocked'."
        ),
        "bearing": (
            "Matches the loop-control cluster (#1/#55/#94) and the #247 cycle gap this port "
            "patched in the harness; anything smarter is new machinery, not a bug fix."
        ),
    },
    {
        "title": "Jev Ultrafast explained / DOM-indexed agent write-ups (boundaries)",
        "url": "https://mattcullerton.com/tokdocs/jev-ultrafast-browser-use-s-dom-indexed-agent-that-picks-an-action-and-a-target/",
        "date": "2026-09-18",
        "clusters": ["perceive-enumeration"],
        "finding": (
            "snapshot.js relies on document.querySelectorAll, so shadow roots, iframes, "
            "canvas, uploads, pop-up tabs and nested scrolling are invisible (e.g. Stripe "
            "Elements, web components). Derivative jev-max pierces open shadow roots / "
            "same-origin iframes and falls back to vision for canvas."
        ),
        "bearing": (
            "The perceive-enumeration fixes (#23/#215/#218) widen the same wall; they do not "
            "remove it. Perception PRs stay strategic (drift §4), gated on the successor call."
        ),
    },
    {
        "title": "Visual-Jev status: the termination problem",
        "url": "https://github.com/guanxuyu-sv/visual-jev-browser-use/blob/HEAD/docs/status.md",
        "date": "2026-09-26",
        "clusters": ["loop-control", "decide-accuracy"],
        "finding": (
            "Mind2Web trajectories contain no DONE/BLOCKED examples at all (offline traces "
            "simply stop), so replay benchmarks like #248 cannot validate stopping behaviour."
        ),
        "bearing": (
            "Caveat for the recommended real-model baseline: step accuracy and run completion "
            "must be measured separately; a good replay score can still loop or stop early."
        ),
    },
]


@dataclass
class IssueRecord:
    """One open issue plus everything this analysis derives about it."""

    number: int
    title: str
    body: str
    author: str
    created_at: str
    updated_at: str
    labels: list[str]
    comments: list[dict]
    reactions: int = 0
    cluster: str = "unclustered"
    status: str = "unclassified"
    evidence: list[str] = field(default_factory=list)
    stale_doc: bool = False
    hit_score: int = 0
    hit_parts: dict[str, int] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return ISSUE_URL.format(number=self.number)

    @property
    def comment_count(self) -> int:
        return len(self.comments)

    @property
    def people(self) -> set[str]:
        names = {self.author} if self.author else set()
        for comment in self.comments:
            login = ((comment.get("author") or {}).get("login") or "").strip()
            if login:
                names.add(login)
        return names

    @property
    def text(self) -> str:
        parts = [self.title, self.body]
        parts.extend((c.get("body") or "") for c in self.comments)
        return "\n".join(parts)


def _gh_json(args: list[str]) -> object:
    """Run a gh command and parse its JSON stdout, failing loudly."""
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(args)} exited {result.returncode}\n{result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FAILED: {' '.join(args)} returned non-JSON output: {exc}") from exc


def fetch_issues(repo: str = REPO) -> list[dict]:
    """Pull the full open-issue corpus (bodies, labels, comments) via gh."""
    data = _gh_json(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title,body,labels,comments,createdAt,updatedAt,author,reactionGroups",
        ]
    )
    if not isinstance(data, list):
        raise SystemExit("FAILED: gh issue list did not return a JSON list")
    return data


def fetch_reactions(repo: str = REPO) -> dict[int, int]:
    """Best-effort reaction totals via the REST API; empty dict on failure."""
    try:
        data = _gh_json(["gh", "api", f"repos/{repo}/issues?state=open&per_page=100"])
    except SystemExit:
        return {}
    totals: dict[int, int] = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "pull_request" not in item:
                totals[int(item["number"])] = int(((item.get("reactions") or {}).get("total_count")) or 0)
    return totals


def normalise_issue(raw: dict, reactions: int = 0) -> IssueRecord:
    """Convert one gh JSON object into an IssueRecord (tolerant of gaps)."""
    labels = [str((label or {}).get("name") or "") for label in (raw.get("labels") or [])]
    reaction_total = reactions
    if not reaction_total:
        for group in raw.get("reactionGroups") or []:
            reaction_total += int((group or {}).get("totalCount") or 0)
    return IssueRecord(
        number=int(raw["number"]),
        title=str(raw.get("title") or "").strip(),
        body=str(raw.get("body") or ""),
        author=str(((raw.get("author") or {}).get("login")) or ""),
        created_at=str(raw.get("createdAt") or raw.get("created_at") or ""),
        updated_at=str(raw.get("updatedAt") or raw.get("updated_at") or ""),
        labels=[label for label in labels if label],
        comments=[c for c in (raw.get("comments") or []) if isinstance(c, dict)],
        reactions=int(reaction_total),
    )


def classify_issue(record: IssueRecord) -> str:
    """Assign the primary failure-mode cluster (first rule wins).

    Titles are matched before bodies: bodies routinely mention other
    subsystems (a stack trace naming TYPE_TEXT, a loop symptom) and would
    otherwise drag the issue into the wrong cluster.
    """
    if record.number in CLUSTER_OVERRIDES:
        return CLUSTER_OVERRIDES[record.number]
    if record.number in NOISE_NUMBERS:
        return "community-noise"
    for haystack in (record.title.lower(), f"{record.title}\n{record.body}".lower()):
        for cluster, keywords in CLUSTER_RULES:
            if any(keyword in haystack for keyword in keywords):
                return cluster
    return "unclustered"


_ISSUE_LINK = re.compile(r"(?:issues/|#)(\d{1,3})\b")


def _section_between(text: str, start_marker: str, end_marker: str | None) -> str:
    """Return the slice of text from start_marker up to end_marker (or EOF)."""
    start = text.find(start_marker)
    if start < 0:
        return ""
    if end_marker is None:
        return text[start:]
    end = text.find(end_marker, start + len(start_marker))
    return text[start:] if end < 0 else text[start:end]


def _issue_numbers(text: str) -> set[int]:
    """Every issue number linked/referenced in a Markdown fragment."""
    return {int(match) for match in _ISSUE_LINK.findall(text)}


def parse_port_doc(text: str) -> dict[str, set[int]]:
    """Split the port notes into fixed vs deliberately-not-fixed issue sets."""
    fixed = _issue_numbers(_section_between(text, "### Fixed in this port", "### Known upstream problems"))
    not_fixed = _issue_numbers(_section_between(text, "### Known upstream problems", "## New in this port"))
    return {"fixed": fixed, "not_fixed": not_fixed}


def parse_drift_doc(text: str) -> dict[str, set[int]]:
    """Split the drift review into must/should/ignore/handled issue sets."""
    p0 = _issue_numbers(_section_between(text, "## 2. P0", "## 3. P1"))
    p1 = _issue_numbers(_section_between(text, "## 3. P1", "## 4. P2"))
    ignore = _issue_numbers(_section_between(text, "### Open issues — ignore", "### Perception boundaries"))
    handled = _issue_numbers(_section_between(text, "## 8. Already handled", "## 9. Recommended"))
    evaluate = _issue_numbers(_section_between(text, "### Open issues — evaluate", "### Open issues — ignore"))
    return {"p0": p0, "p1": p1, "evaluate": evaluate, "ignore": ignore, "handled": handled}


def scan_code_evidence(root: Path = ROOT) -> dict[int, list[str]]:
    """Map issue numbers to DEVIATION/test citations that claim a fix.

    A citation only counts when it sits on (or next to) a DEVIATION comment
    in ``src/`` or inside ``tests/`` -- a bare mention in prose does not
    promote an issue to handled.
    """
    evidence: dict[int, list[str]] = {}
    candidates: list[Path] = []
    for base in (root / "src" / "account_vault", root / "tests"):
        if base.is_dir():
            candidates.extend(sorted(p for p in base.rglob("*") if p.suffix in {".py", ".js"} and p.is_file()))
    # The analyser's own tests quote issue numbers as fixtures; counting
    # them would let this report "fix" issues by describing them.
    candidates = [p for p in candidates if p.name != "test_analyse_jev_issues.py"]
    for path in candidates:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel = path.relative_to(root)
        in_tests = "tests" in path.parts
        for index, line in enumerate(lines):
            numbered = _issue_numbers(line)
            if not numbered:
                continue
            window = "\n".join(lines[max(0, index - 3) : index + 1])
            if in_tests or "DEVIATION" in window:
                for number in numbered:
                    evidence.setdefault(number, []).append(f"{rel}:{index + 1}")
    return evidence


def resolve_status(
    record: IssueRecord,
    port_sets: dict[str, set[int]],
    drift_sets: dict[str, set[int]],
    code_evidence: dict[int, list[str]],
) -> tuple[str, list[str], bool]:
    """Decide handled/partial/open/ignore/unclassified with cited evidence.

    Code (DEVIATION/tests) outranks docs. When code says fixed but a doc
    still lists the issue as open/P0, the status is "handled" and the
    disagreement is surfaced via ``stale_doc`` so the docs get corrected.
    """
    number = record.number
    notes: list[str] = []
    in_code = number in code_evidence
    in_port_fixed = number in port_sets["fixed"]
    in_drift_handled = number in drift_sets["handled"]
    docs_say_open = (
        number in port_sets["not_fixed"]
        or number in drift_sets["p0"]
        or number in drift_sets["p1"]
        or number in drift_sets["evaluate"]
    )
    if in_code:
        notes.append("code: " + ", ".join(code_evidence[number][:3]))
    if in_port_fixed:
        notes.append("port notes: Fixed table")
    if in_drift_handled:
        notes.append("drift review §8")
    if number in drift_sets["p0"]:
        notes.append("drift review P0")
    if number in drift_sets["p1"]:
        notes.append("drift review P1")
    if number in drift_sets["evaluate"]:
        notes.append("drift review P2-evaluate")
    if number in port_sets["not_fixed"]:
        notes.append("port notes: deliberately NOT fixed")

    if number in NOT_A_FIX:
        notes.append(f"not a fix: {NOT_A_FIX[number]}")
        return "open", notes, False
    if in_code or in_port_fixed or in_drift_handled:
        if number in PARTIAL_NOTES and number in (16, 25, 125):
            # Code covers the first half; the named residual is still open.
            return "partial", notes, False
        if number in PARTIAL_NOTES and not in_code:
            return "partial", notes, False
        # Stale = code fixed it but the living record has not caught up.
        # The drift review is a dated snapshot (its P0/P1 queues are
        # history, never edited after the fact), so the port notes'
        # Fixed table is the doc that must record a pickup; once it
        # does, a lingering drift-review queue entry is not doc lag.
        stale = in_code and docs_say_open and not in_port_fixed
        return "handled", notes, stale
    if number in drift_sets["ignore"] or record.cluster == "community-noise":
        if not notes:
            notes.append("drift review P2-ignore / noise cluster")
        return "ignore", notes, False
    if docs_say_open or number in PARTIAL_NOTES:
        return ("partial" if number in PARTIAL_NOTES else "open"), notes, False
    return "unclassified", notes, False


_ME_TOO = ("+1", "same here", "same issue", "same pattern", "also hit", "me too", "we've hit", "we hit")
_REPRO = ("repro", "steps to reproduce", "live site", "in our runs", "benchmark", "measured", "trace")


def cross_reference_counts(records: list[IssueRecord]) -> Counter:
    """Count how often each issue number is mentioned by *other* issues."""
    counts: Counter = Counter()
    for record in records:
        for mentioned in _issue_numbers(record.text):
            if mentioned != record.number:
                counts[mentioned] += 1
    return counts


def hit_score(record: IssueRecord, xrefs: int) -> tuple[int, dict[str, int]]:
    """Transparent 'how often hit' proxy -- engagement, never telemetry.

    score = 2*comments + 3*distinct people + 2*reactions + 3*cross-refs
            + 4 per me-too comment + 3 if a repro/benchmark is attached.
    """
    lowered_comments = "\n".join((c.get("body") or "").lower() for c in record.comments)
    me_too = sum(1 for phrase in _ME_TOO if phrase in lowered_comments)
    has_repro = any(phrase in record.text.lower() for phrase in _REPRO)
    parts = {
        "comments": 2 * record.comment_count,
        "people": 3 * len(record.people),
        "reactions": 2 * record.reactions,
        "xrefs": 3 * xrefs,
        "me-too": 4 * me_too,
        "repro": 3 if has_repro else 0,
    }
    return sum(parts.values()), parts


def analyse(records: list[IssueRecord], port_text: str, drift_text: str, code_evidence: dict) -> list[IssueRecord]:
    """Classify, cross-reference and score every record in place."""
    port_sets = parse_port_doc(port_text)
    drift_sets = parse_drift_doc(drift_text)
    xrefs = cross_reference_counts(records)
    for record in records:
        record.cluster = classify_issue(record)
        status, evidence, stale = resolve_status(record, port_sets, drift_sets, code_evidence)
        record.status, record.evidence, record.stale_doc = status, evidence, stale
        record.hit_score, record.hit_parts = hit_score(record, int(xrefs.get(record.number, 0)))
    return records


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _issue_link(record: IssueRecord) -> str:
    return f"[#{record.number}]({record.url})"


def _short_title(record: IssueRecord, limit: int = 72) -> str:
    title = _md_escape(record.title)
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


# One-line next actions for the work queue. Keyed by issue number where the
# drift review already scoped the work; cluster defaults cover the rest.
NEXT_ACTIONS = {
    248: "Run the metered real-model baseline (Jev vs d1-2B); score steps and run completion separately",
    67: "Reproduce on the baseline suite; classify misses as menu vs pick before changing prompts",
    129: "Add the MDN task to the baseline suite as a pinned wrong-waypoint repro",
    149: "Prototype structured visible text behind the baseline; keep the 6,000-char budget explicit",
    51: "Add Page.bringToFront on the Windows path; needs a Windows live verifier before calling it fixed",
    223: "Add opt-in keep_open (small, offline-testable); default stays close",
    159: "Demo preflight: surface missing keys/backends before the first tick",
    224: "Name the region block in the hosted-path error text; point at the local backend",
    225: "Same fix as #224 (duplicate report) -- one error-text change covers both",
    187: "Evaluate only via the planner-branch diff (drift P1.5); do not design from the issue alone",
    21: "Docs-only: document how to obtain a key if the hosted path stays documented",
}

CLUSTER_ACTIONS = {
    "decide-accuracy": "Gate on the real-model baseline; fix the harness (menu/verify) before the model",
    "loop-control": "Bound the spend first; add an offline regression at the loop seam",
    "text-helper": "Carry more identity/context around the decider; pin with a same-label regression",
    "perceive-enumeration": "Extend snapshot.js + static mirror together; pin offline and live",
    "execute-act": "Re-validate identity at act time; never fall back to value matching",
    "perception-fidelity": "Prototype behind the baseline; measure before adopting",
    "wire-providers": "Fail at startup with a named error; keep endpoints in Settings",
    "browser-cdp": "Deadline every CDP call; degrade, never retry a mutation",
    "demo-docs-ux": "Cheap and local; batch with the next docs pass",
    "community-noise": "No action",
    "unclustered": "Triage: read the body and extend CLUSTER_RULES",
}


def _next_action(record: IssueRecord) -> str:
    if record.number in NEXT_ACTIONS:
        return NEXT_ACTIONS[record.number]
    if record.status == "partial" and record.number in PARTIAL_NOTES:
        return f"Close the residual: {PARTIAL_NOTES[record.number]}"
    return CLUSTER_ACTIONS.get(record.cluster, "Triage")


def render_report(records: list[IssueRecord], fetched_at: str, source_note: str) -> str:
    """Render the full Markdown report from analysed records."""
    by_status = Counter(r.status for r in records)
    total_comments = sum(r.comment_count for r in records)
    total_people = set().union(*(r.people for r in records)) if records else set()
    label_counts = Counter(label for r in records for label in r.labels)
    open_work = sorted(
        (r for r in records if r.status in {"open", "partial", "unclassified"}),
        key=lambda r: (-r.hit_score, r.number),
    )
    handled = sorted((r for r in records if r.status == "handled"), key=lambda r: r.number)
    stale = [r for r in records if r.stale_doc]
    unclustered = [r for r in records if r.cluster == "unclustered"]

    lines: list[str] = []
    lines += [
        "# jev-ultrafast open-issue analysis — failure modes vs this port",
        "",
        f"- **Generated:** {fetched_at} by `scripts/analyse_jev_issues.py` ({source_note}).",
        f"- **Corpus:** {len(records)} open issues in [{REPO}](https://github.com/{REPO}) "
        f"({total_comments} comments, {len(total_people)} distinct people). PRs are out of scope.",
        "- **Cross-referenced against:** `docs/jev-ultrafast-port.md`, `docs/jev-ultrafast-drift-review.md`, "
        "plus `DEVIATION` comments and regression tests in `src/` / `tests/` (code outranks docs).",
        "- **Reproduce:** `uv run python scripts/analyse_jev_issues.py` (needs authenticated `gh`).",
        "",
        "## TL;DR",
        "",
        f"- **{by_status.get('handled', 0)} handled, {by_status.get('partial', 0)} partial, "
        f"{by_status.get('open', 0)} open, {by_status.get('ignore', 0)} ignore/noise, "
        f"{by_status.get('unclassified', 0)} unclassified** out of {len(records)}.",
    ]
    if open_work:
        top = open_work[0]
        lines.append(
            f"- Highest-ranked remaining work is {_issue_link(top)} "
            f"({top.cluster}, hit score {top.hit_score}): {_short_title(top, 90)}."
        )
    if stale:
        nums = ", ".join(f"#{r.number}" for r in sorted(stale, key=lambda r: r.number))
        lines.append(
            f"- **Docs lag the code on {len(stale)} issue(s)** ({nums}): fixed in code since the "
            "drift review / port notes were written — update the docs, not the code (see §5)."
        )
    lines.append(
        "- The hit score is an **engagement proxy** (comments, people, reactions, cross-references), "
        "not usage telemetry — the tracker has none. Formula in §7."
    )
    if unclustered:
        lines.append(
            f"- ⚠️ {len(unclustered)} issue(s) matched no cluster rule and need triage: "
            + ", ".join(f"#{r.number}" for r in unclustered)
            + "."
        )

    lines += ["", "## 1. Work queue (ranked — start here)", ""]
    lines.append("Open + partial + unclassified, most-hit first. Noise and handled issues are excluded.")
    lines.append("")
    lines.append("| Rank | Issue | Cluster | Status | Hit | Comments | Next action |")
    lines.append("|---|---|---|---|---|---|---|")
    for rank, record in enumerate(open_work, start=1):
        lines.append(
            f"| {rank} | {_issue_link(record)} {_short_title(record, 48)} | {record.cluster} | "
            f"{record.status} | {record.hit_score} | {record.comment_count} | {_md_escape(_next_action(record))} |"
        )
    if not open_work:
        lines.append("| — | _nothing open_ | — | — | — | — | — |")

    lines += ["", "## 2. Corpus snapshot", ""]
    lines.append(f"- Issues: {len(records)} open; comments: {total_comments}; distinct people: {len(total_people)}.")
    if label_counts:
        lines.append("- Labels: " + ", ".join(f"{name} ×{count}" for name, count in label_counts.most_common()) + ".")
    else:
        lines.append("- Labels: **none** — upstream applies no labels, so clustering is by title/body text (§7).")
    by_month = Counter(r.created_at[:7] for r in records if r.created_at)
    if by_month:
        lines.append("- Filed by month: " + ", ".join(f"{m} ×{by_month[m]}" for m in sorted(by_month)) + ".")
    top_commented = sorted(records, key=lambda r: -r.comment_count)[:5]
    lines.append(
        "- Most discussed: "
        + ", ".join(f"#{r.number} ({r.comment_count})" for r in top_commented if r.comment_count)
        + "."
    )

    lines += ["", "## 3. Failure-mode clusters", ""]
    lines.append("| Cluster | Issues | Handled | Partial | Open | Ignore |")
    lines.append("|---|---|---|---|---|---|")
    for cluster in CLUSTER_ORDER + (["unclustered"] if unclustered else []):
        members = [r for r in records if r.cluster == cluster]
        if not members:
            continue
        counts = Counter(r.status for r in members)
        lines.append(
            f"| {cluster} | {len(members)} | {counts.get('handled', 0)} | {counts.get('partial', 0)} | "
            f"{counts.get('open', 0) + counts.get('unclassified', 0)} | {counts.get('ignore', 0)} |"
        )
    for cluster in CLUSTER_ORDER + (["unclustered"] if unclustered else []):
        members = sorted((r for r in records if r.cluster == cluster), key=lambda r: r.number)
        if not members:
            continue
        blurb = CLUSTER_BLURBS.get(cluster, "No rule matched — triage needed.")
        lines += ["", f"### {cluster}", "", blurb, ""]
        lines.append("| Issue | Status | Comments | Evidence / note |")
        lines.append("|---|---|---|---|")
        for record in members:
            note = "; ".join(record.evidence) or "—"
            if record.number in PARTIAL_NOTES:
                note += f" · residual: {PARTIAL_NOTES[record.number]}"
            lines.append(
                f"| {_issue_link(record)} {_short_title(record, 52)} | {record.status} | "
                f"{record.comment_count} | {_md_escape(note)} |"
            )

    lines += ["", "## 4. Already handled by this port", ""]
    lines.append("Each row cites where the handling is recorded. Code citations are `file:line` at generation time.")
    lines.append("")
    lines.append("| Issue | Cluster | Evidence |")
    lines.append("|---|---|---|")
    for r in handled:
        lines.append(f"| {_issue_link(r)} {_short_title(r, 52)} | {r.cluster} | {_md_escape('; '.join(r.evidence))} |")

    lines += ["", "## 5. Doc-vs-code disagreements (fix the docs)", ""]
    if stale:
        lines.append(
            "These read as open in the port notes or drift review but are fixed in code. Verify, then update the docs:"
        )
        lines.append("")
        for record in sorted(stale, key=lambda r: r.number):
            lines.append(
                f"- {_issue_link(record)} {_short_title(record, 80)} — {_md_escape('; '.join(record.evidence))}."
            )
    else:
        lines.append("None found at generation time.")

    lines += ["", "## 6. Outside the tracker", ""]
    lines.append(
        "Curated sources checked against their primary pages on 2026-11-22 (see `EXTERNAL_SOURCES` in the "
        "script). Launch-number recycling is excluded; the drift review's §7 remains the fuller survey."
    )
    for source in EXTERNAL_SOURCES:
        lines += [
            "",
            f"### [{source['title']}]({source['url']}) ({source['date']})",
            "",
            f"- **Clusters:** {', '.join(source['clusters'])}",
            f"- **Finding:** {source['finding']}",
            f"- **Bearing on this port:** {source['bearing']}",
        ]

    lines += [
        "",
        "## 7. Method and limits",
        "",
        "- **Fetch:** `gh issue list --state open --json ...` (exact fields in the script) "
        f"against `{REPO}`, enriched with REST reaction totals. Open PRs (the larger half of the tracker's "
        "open items) are deliberately out of scope; the drift review triages the load-bearing PRs.",
        "- **Clustering:** ordered keyword rules over title + body (`CLUSTER_RULES` in the script), first match "
        "wins, with a small explicit override table for titles keywords misfile. Comment text informs the hit "
        "score and cross-references, not the primary cluster, so a long thread cannot drag an issue sideways.",
        "- **Handled:** an issue counts as handled when a `DEVIATION` comment or regression test cites it, or "
        "when the port notes' Fixed table / drift review §8 lists it. Code wins ties; disagreements land in §5 "
        "instead of being silently resolved.",
        "- **Hit score:** `2×comments + 3×distinct people + 2×reactions + 3×cross-references + 4×me-too phrases "
        "+ 3×repro/benchmark attached`. It ranks *visible* pain; silent failures (e.g. a wrong fill nobody "
        "noticed) are under-counted by construction — read the cluster tables, not just the ranking.",
        "- **Limits:** keyword clustering can misfile paraphrased reports (unclustered rows are flagged, never "
        "dropped); reaction totals are near-zero upstream and barely move the score; comment counts reward "
        "discussion, including maintainer replies. Re-run after any pickup batch and diff the report.",
        "",
        "## Appendix A. Every open issue",
        "",
        "| # | Title | Cluster | Status | Comments | Reactions | Author | Updated |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for record in sorted(records, key=lambda r: r.number):
        lines.append(
            f"| {_issue_link(record)} | {_short_title(record, 64)} | {record.cluster} | {record.status} | "
            f"{record.comment_count} | {record.reactions} | {_md_escape(record.author)} | {record.updated_at[:10]} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyse browser-use/jev-ultrafast open issues against this port.")
    parser.add_argument("--repo", default=REPO, help=f"GitHub repo to analyse (default: {REPO})")
    parser.add_argument("--input", type=Path, default=None, help="Replay a saved corpus JSON instead of calling gh")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Report path (default: docs/…analysis.md)")
    parser.add_argument("--save-corpus", type=Path, default=None, help="Also save the raw corpus JSON to this path")
    args = parser.parse_args(argv)

    if args.input is not None:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise SystemExit(f"FAILED: {args.input} does not contain a JSON list of issues")
        reactions: dict[int, int] = {}
        source_note = f"replayed from {args.input}"
    else:
        raw = fetch_issues(args.repo)
        reactions = fetch_reactions(args.repo)
        source_note = "live `gh` fetch"

    if args.save_corpus is not None:
        args.save_corpus.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved corpus: {args.save_corpus} ({len(raw)} issues)")

    records = [normalise_issue(item, reactions.get(int(item["number"]), 0)) for item in raw]
    port_text = PORT_DOC.read_text(encoding="utf-8") if PORT_DOC.is_file() else ""
    drift_text = DRIFT_DOC.read_text(encoding="utf-8") if DRIFT_DOC.is_file() else ""
    if not port_text or not drift_text:
        print("WARNING: port/drift doc missing; handled flags will rely on code evidence only", file=sys.stderr)
    analyse(records, port_text, drift_text, scan_code_evidence())

    fetched_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    args.output.write_text(render_report(records, fetched_at, source_note), encoding="utf-8")
    counts = Counter(r.status for r in records)
    unclustered = [r.number for r in records if r.cluster == "unclustered"]
    print(
        f"wrote {args.output}: {len(records)} issues "
        f"(handled={counts.get('handled', 0)}, partial={counts.get('partial', 0)}, "
        f"open={counts.get('open', 0)}, ignore={counts.get('ignore', 0)}, "
        f"unclassified={counts.get('unclassified', 0)})"
    )
    if unclustered:
        print(f"WARNING: unclustered issues need triage: {unclustered}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
