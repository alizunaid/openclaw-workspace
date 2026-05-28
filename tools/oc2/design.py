"""`oc2 design` — produce an architecture for a task.

Session 2: the LLM call is REAL. A single chat-completion against the same
local ollama / qwen2.5-coder:32b that Tier 1 uses (locked decision Q5). The
mock from Session 1 is preserved behind `--mock` for fast offline testing.

This is the only module that needs to know about ollama. It copies the
oc_builder.py request shape (OpenAI-compat client at http://localhost:11434/v1,
api_key='ollama', timeout 300s) but does NOT import oc_builder — Tier 1 stays
subprocess-only / no-import per the design split.

Flow (doc Section 2):
  1. resolve project name (--name or derived from task)
  2. create tier2_projects/<name>/ if absent (refuse on auto-name conflict, Q8)
  3. load project context via oc_project (allowed this session)
  4. LLM call -> architecture markdown
  5. parse + validate; on first failure, ONE retry with errors fed back
  6. on success: write architecture.md (rotate prior to architecture.v<N>.md)
     on final failure: write architecture.raw.md (forensics), do NOT write
     architecture.md; mark state architecture_invalid; exit 1
  7. refresh state.json and tell the user where to go next
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from oc2 import TIER2_PROJECTS
from oc2.architecture import ParseError, parse, validate
from oc2.state import append_history, new_state, now_iso, read_state, write_state

# --- ollama call configuration (copied from oc_builder.py, not imported) ---

MODEL = "qwen2.5-coder:32b"
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_API_KEY = "ollama"
PER_LLM_TIMEOUT = 300  # seconds; matches oc_builder.PER_LLM_TIMEOUT


def _client():
    """Lazy OpenAI client. Lazy so importing this module doesn't require ollama
    or the openai package to be reachable (e.g., for unit tests)."""
    from openai import OpenAI
    return OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)


# --- mock architecture (Session 1, kept behind --mock) ----------------------

MOCK_ARCHITECTURE = """# Architecture: {title}

## Overview
A tool that runs each morning, reads the work-items register, computes the
day's top-ten and a per-category breakdown, and writes a single daily-brief
markdown the user can read at a glance.

## Subsystems

### register-reader
**Purpose:** Load the work-items register CSV from disk and expose typed,
validated rows to the rest of the system so no downstream subsystem touches the
raw file format.
**Inputs:**
- path to the work-items register CSV
- optional column-name overrides
**Outputs:**
- list of typed row dicts (one per work item)
**Depends on:** none
**Owns state:** stateless
**Failure modes:**
- register file missing or unreadable
- a row fails type coercion (bad date, non-numeric priority)

### top10-runner
**Purpose:** Rank the work items and select the day's top ten, reusing the
existing selection heuristic.
**Inputs:**
- typed rows from register-reader
**Outputs:**
- ordered list of the ten highest-priority items
**Depends on:** register-reader
**Owns state:** stateless
**Failure modes:**
- fewer than ten items available
- ranking key absent on some rows

### breakdown-generator
**Purpose:** Aggregate the work items into per-category counts and compute
simple day-over-day deltas.
**Inputs:**
- typed rows from register-reader
**Outputs:**
- per-category count table with deltas
**Depends on:** register-reader
**Owns state:** reads the previous day's snapshot from data/breakdown_prev.json
**Failure modes:**
- empty register yields an empty breakdown
- previous snapshot missing on first run

### output-formatter
**Purpose:** Merge the top-ten and the category breakdown into one daily-brief
markdown document and write it to disk.
**Inputs:**
- ordered top-ten list
- per-category count table
**Outputs:**
- daily_brief.md on disk
**Depends on:** top10-runner, breakdown-generator
**Owns state:** writes daily_brief.md
**Failure modes:**
- output directory not writable

## Data flow
register-reader loads and types the rows once. top10-runner and
breakdown-generator each consume those rows independently. output-formatter
waits for both, merges their results, and writes the daily brief.

## Cross-cutting failure modes
If the register file is missing the run aborts immediately with a clear message
rather than producing a partial brief. If the previous snapshot is absent the
breakdown reports counts without deltas instead of failing.

## Integration points
Rows are plain dicts with string keys, produced solely by register-reader. The
top-ten list and the breakdown table are passed in memory between subsystems;
only output-formatter performs output I/O. The daily brief is written to a path
supplied by the caller.
"""


def mock_llm_design(task: str, project_title: str, project_info: dict | None = None,
                    retry_with_errors: list[str] | None = None) -> str:
    """Stand-in for the design-phase LLM. Ignores task content and returns the
    canned 4-subsystem architecture. Kept available behind `--mock`."""
    return MOCK_ARCHITECTURE.format(title=project_title)


# --- system prompt ----------------------------------------------------------

# A neutral 2-subsystem worked example — deliberately unrelated to plausible
# user tasks (no csv tooling, no daily-brief tooling) so the model can't copy
# it verbatim. Its sole job is to lock the schema shape into context.
_WORKED_EXAMPLE = """# Architecture: heartbeat-monitor

## Overview
A small tool that pings a list of remote endpoints on a schedule, records the
results, and routes failures to an alerts channel.

## Subsystems

### endpoint-pinger
**Purpose:** Probe each configured endpoint over HTTP and capture latency and
status for every probe so downstream subsystems have a uniform health record.
**Inputs:**
- list of endpoint URLs from config
- per-endpoint timeout setting
**Outputs:**
- list of probe-result records (url, status, latency_ms, timestamp)
**Depends on:** none
**Owns state:** stateless
**Failure modes:**
- DNS resolution fails
- endpoint hangs past the timeout

### alert-router
**Purpose:** Inspect probe results, decide which failures warrant an alert,
and deliver a formatted message to the configured alerts channel.
**Inputs:**
- list of probe-result records from endpoint-pinger
- recent failure history (for de-duplication)
**Outputs:**
- alert deliveries (channel, message body, severity)
**Depends on:** endpoint-pinger
**Owns state:** persists recent failure history to disk for dedup across runs
**Failure modes:**
- alerts channel unreachable
- de-duplication file corrupt

## Data flow
endpoint-pinger probes each endpoint and emits a uniform list of probe-result
records. alert-router consumes those records, deduplicates against recent
history, and emits alerts for genuine failures only.

## Cross-cutting failure modes
If the config file is missing the run aborts before any probes run. If the
de-duplication file is missing or corrupt, alert-router falls back to alerting
on every failure (loud but safe).

## Integration points
Probe-result records are plain dicts with the keys (url, status, latency_ms,
timestamp). alert-router treats endpoint-pinger's output as the only source
of truth for current status."""


def _build_design_system_prompt(project_title: str, project_info: dict,
                                retry_with_errors: list[str] | None = None) -> str:
    short_desc = (project_info.get("short_description") or "").strip()
    proj_name = (project_info.get("project_name") or "").strip()
    context_block = "PROJECT CONTEXT\n"
    if proj_name:
        context_block += f"- Project: {proj_name}\n"
    if short_desc:
        context_block += f"- Short description: {short_desc}\n"
    if not (proj_name or short_desc):
        context_block += "- (none provided)\n"

    retry_block = ""
    if retry_with_errors:
        bullets = "\n".join(f"- {e}" for e in retry_with_errors)
        retry_block = (
            "YOUR PREVIOUS ATTEMPT FAILED these checks:\n"
            f"{bullets}\n\n"
            "Re-produce the WHOLE architecture markdown for the user's task, "
            "fixing those issues. The output rules below still apply.\n\n"
        )

    return (
        "You are an expert software architect. Produce a markdown architecture "
        "document for the user's task. The markdown is what a human will read "
        "and edit; a downstream tool will then parse it and use it to drive "
        "code generation, one subsystem at a time.\n\n"
        f"{context_block}\n"
        f"{retry_block}"
        "YOUR OUTPUT MUST FOLLOW THIS EXACT SCHEMA:\n\n"
        "# Architecture: <project name>\n\n"
        "## Overview\n"
        "<one paragraph: what the system does, the problem it solves, the rough shape>\n\n"
        "## Subsystems\n\n"
        "### <subsystem-name>\n"
        "**Purpose:** <one paragraph>\n"
        "**Inputs:** <bulleted list using `- ` markers>\n"
        "**Outputs:** <bulleted list using `- ` markers>\n"
        "**Depends on:** <comma-separated names of other subsystems, or `none`>\n"
        "**Owns state:** <one phrase or sentence, or `stateless`>\n"
        "**Failure modes:** <bulleted list using `- ` markers>\n\n"
        "### <next-subsystem-name>\n"
        "...\n\n"
        "## Data flow\n"
        "<one paragraph narrative tracing a typical input through the system>\n\n"
        "## Cross-cutting failure modes\n"
        "<prose: failures that span subsystems, with what the system does in response>\n\n"
        "## Integration points\n"
        "<prose: shared data schemas, file conventions, types — the contracts "
        "between subsystems>\n\n"
        "HARD CONSTRAINTS (your output MUST satisfy ALL of these):\n"
        "- Between 2 and 10 subsystems total.\n"
        "- Every subsystem MUST include all six fields above, with those exact labels.\n"
        "- Every subsystem name MUST be kebab-case (lowercase letters, digits, hyphens).\n"
        "- No subsystem may be named `main` (reserved).\n"
        "- `Depends on` must form a DAG (no cycles, no self-dependencies). Every "
        "name listed in `Depends on` must be the name of another subsystem in "
        "the same document.\n\n"
        "WORKED EXAMPLE — note the exact shape and field labels (this is "
        "illustrative; produce something DIFFERENT for the user's task):\n\n"
        f"{_WORKED_EXAMPLE}\n\n"
        "OUTPUT RULES (read carefully):\n"
        "- Output ONLY the architecture markdown for the user's task — nothing else.\n"
        "- Do NOT wrap the output in code fences (no ``` and no ```markdown).\n"
        "- Do NOT add a preamble like \"Here is the architecture:\" or a "
        "postamble like \"Hope this helps\".\n"
        "- Do NOT add commentary, explanation, or anything outside the schema sections.\n"
        "- The very first non-empty line MUST be `# Architecture: <project name>`.\n"
    )


def _format_errors_for_retry(parse_error: str | None,
                             validation_errors: list[str]) -> list[str]:
    out: list[str] = []
    if parse_error:
        out.append(f"document did not parse: {parse_error}")
    out.extend(validation_errors)
    return out


# --- real ollama call -------------------------------------------------------

def real_llm_design(task: str, project_title: str, project_info: dict,
                    retry_with_errors: list[str] | None = None) -> str:
    """Single chat-completion against qwen2.5-coder:32b. Returns raw content
    (caller does preclean+parse+validate). NOTE: the first call after a cold
    ollama has a 30-90s VRAM-load lag — that is NORMAL, not a hang."""
    system = _build_design_system_prompt(project_title, project_info, retry_with_errors)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    attempt_label = "retry" if retry_with_errors else "first call"
    print(f"[oc2 design] calling {MODEL} ({attempt_label}; "
          f"cold-start may take 30-90s for VRAM load)...", flush=True)
    t0 = time.time()
    resp = _client().chat.completions.create(
        model=MODEL, messages=messages, timeout=PER_LLM_TIMEOUT,
    )
    elapsed = time.time() - t0
    content = resp.choices[0].message.content or ""
    print(f"[oc2 design] model responded in {elapsed:.1f}s "
          f"({len(content)} chars)", flush=True)
    return content


# --- project context (oc_project is allowed this session) -------------------

def _load_project_context() -> dict:
    """Best-effort load of project context via Tier 1's oc_project module. Lazy
    + defensive: if the import fails (e.g., tools/ not on sys.path in a tools
    test harness), we return an empty context rather than crashing design."""
    try:
        from oc_project import load_project, resolve_slug
    except ImportError as e:
        print(f"[oc2 design] note: oc_project unavailable ({e}); "
              f"proceeding without project context.", flush=True)
        return {"project_name": "", "short_description": "", "raw_context": ""}
    try:
        slug = resolve_slug()
        return load_project(slug)
    except Exception as e:
        print(f"[oc2 design] note: project context load failed ({e}); "
              f"proceeding without it.", flush=True)
        return {"project_name": "", "short_description": "", "raw_context": ""}


# --- helpers (carried from Session 1, unchanged) ----------------------------

def derive_name(task: str) -> str:
    """Kebab-case the first 4 words of the task (doc Section 6: no LLM naming)."""
    words = re.findall(r"[A-Za-z0-9]+", task.lower())[:4]
    name = "-".join(words)
    return name or "untitled-project"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rotate_existing_architecture(project_dir: Path, version_count: int) -> Path | None:
    current = project_dir / "architecture.md"
    if not current.exists():
        return None
    rotated = project_dir / f"architecture.v{version_count}.md"
    current.rename(rotated)
    return rotated


# --- the command ------------------------------------------------------------

def _attempt(task: str, project_title: str, project_info: dict,
             use_mock: bool, retry_errors: list[str] | None
             ) -> tuple[str, ParseError | None, list[str], object | None]:
    """One generation+parse+validate attempt. Returns (raw_md, parse_err,
    validation_errors, arch_or_None)."""
    fn = mock_llm_design if use_mock else real_llm_design
    raw = fn(task, project_title, project_info, retry_with_errors=retry_errors)
    try:
        arch = parse(raw)
    except ParseError as e:
        return raw, e, [], None
    res = validate(arch)
    return raw, None, list(res.errors), arch


def cmd_design(args) -> int:
    task = args.task
    explicit_name = bool(args.name)
    name = (args.name or derive_name(task)).strip().lower()
    use_mock = bool(getattr(args, "mock", False))

    if args.from_path:
        print(f"note: --from is accepted but not yet wired; ignoring "
              f"{args.from_path!r}.", flush=True)

    project_dir = TIER2_PROJECTS / name
    exists = project_dir.exists()

    # Q8: an auto-derived name collision refuses; explicit --name on an
    # existing project is an intentional regeneration (rotate the old version).
    if exists and not explicit_name:
        print(f"error: project {name!r} already exists in tier2_projects/.")
        print(f"       Re-run with --name <other> to choose a distinct name,")
        print(f"       or pass --name {name} to intentionally regenerate it.")
        return 1

    project_dir.mkdir(parents=True, exist_ok=True)
    state = read_state(project_dir) or new_state(name)
    version_count = state["architecture"].get("version_count", 0)

    project_info = _load_project_context() if not use_mock else {}

    # First attempt + at most one retry with errors fed back (mirrors Tier 1
    # self-heal pattern; the brief locks the budget at 1).
    raw, parse_err, val_errs, arch = _attempt(
        task, name, project_info, use_mock, retry_errors=None,
    )
    if parse_err is not None or val_errs:
        feedback = _format_errors_for_retry(
            str(parse_err) if parse_err else None, val_errs,
        )
        print(f"[oc2 design] first attempt failed "
              f"({len(feedback)} issue(s)); retrying once...", flush=True)
        raw, parse_err, val_errs, arch = _attempt(
            task, name, project_info, use_mock, retry_errors=feedback,
        )

    # Decide outcome and what to write.
    raw_path = project_dir / "architecture.raw.md"
    arch_path = project_dir / "architecture.md"

    # On success: rotate any prior architecture and write the new one.
    success = parse_err is None and not val_errs
    if success:
        if exists and explicit_name:
            rotated = _rotate_existing_architecture(project_dir, version_count)
            if rotated:
                print(f"rotated previous architecture -> {rotated.name}")
            version_count += 1
        arch_path.write_text(raw, encoding="utf-8")
        # Clear any stale raw.md from a prior failed run.
        if raw_path.exists():
            raw_path.unlink()
    else:
        # Final failure: preserve the last raw response for forensics. We do
        # NOT touch architecture.md unless we have a parseable doc that just
        # fails validation — in that case, also write it so the human can edit
        # toward validity rather than starting from scratch.
        raw_path.write_text(raw, encoding="utf-8")
        if parse_err is None and arch is not None:
            arch_path.write_text(raw, encoding="utf-8")

    # State refresh. Design always supersedes any prior approval.
    state["project_name"] = name
    state["architecture"]["current_path"] = (
        "architecture.md" if success or (parse_err is None) else None
    )
    state["architecture"]["current_sha256"] = _sha256_text(raw) if success else None
    state["architecture"]["raw_path"] = None if success else "architecture.raw.md"
    state["architecture"]["valid"] = bool(success)
    state["architecture"]["version_count"] = version_count
    state["approval"] = {
        "approved": False,
        "approved_at": None,
        "approved_architecture_sha256": None,
    }
    if success:
        state["subsystems"] = {
            s.name: {
                "status": "pending",
                "spec_sha256": s.spec_sha256(),
                "ocb_run_id": None,
                "source_dir": f"subsystems/{s.name}/",
                "built_at": None,
                "error": None,
            }
            for s in arch.subsystems
        }
        append_history(state, "design",
                       architecture_sha256=state["architecture"]["current_sha256"])
    else:
        # Don't trust the partial subsystems map on failure.
        state["subsystems"] = {}
        append_history(
            state, "design_invalid",
            parse_error=str(parse_err) if parse_err else None,
            validation_errors=val_errs,
        )
    state["last_session_at"] = now_iso()
    write_state(project_dir, state)

    # Print outcome + guidance.
    if success:
        rel = arch_path.relative_to(TIER2_PROJECTS.parent)
        print(f"\nArchitecture written to {rel}")
        warn_res = validate(arch)
        if warn_res.warnings:
            print("\nwarnings (advisory, do not block approval):")
            for w in warn_res.warnings:
                print(f"  - {w}")
        print(f"\n{len(arch.subsystems)} subsystems, valid DAG. "
              f"Read it, edit if needed, then:\n    oc2 approve {name}")
        return 0

    # Failure path.
    print(f"\nVALIDATION FAILED after retry — raw model output preserved at "
          f"{raw_path.relative_to(TIER2_PROJECTS.parent)}.")
    if parse_err is not None:
        print(f"  parse error: {parse_err}")
    for e in val_errs:
        print(f"  - {e}")
    if parse_err is None:
        print(f"\nA best-effort architecture.md was written so you can edit "
              f"toward validity, then `oc2 approve {name}`. Or re-run "
              f"`oc2 design \"<task>\" --name {name}` to regenerate.")
    else:
        print(f"\nNo parseable title in the model output — edit "
              f"architecture.raw.md by hand into the schema, save it as "
              f"architecture.md, then `oc2 approve {name}`. Or re-run "
              f"`oc2 design \"<task>\" --name {name}` to regenerate.")
    return 1
