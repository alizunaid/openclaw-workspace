"""`oc2 design` — produce an architecture for a task.

Session 1: the LLM call is MOCKED. `mock_llm_design()` returns a fixed, valid
4-subsystem architecture (a daily snapshot reporter) so the parser + validator +
state plumbing can be exercised end to end without ollama. The real single-call
design against the Tier 1 model (qwen2.5-coder:32b, Q5) plus project-context
loading via oc_project lands in Session 2.

Flow (doc Section 2):
  1. resolve project name (--name or derived from task)
  2. create tier2_projects/<name>/ if absent (refuse on auto-name conflict, Q8)
  3. LLM call -> architecture markdown  [MOCKED here]
  4. parse + validate
  5. write architecture.md (rotating any prior version to architecture.v<N>.md)
  6. write/refresh state.json
  7. tell the user where to read it and how to approve
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from oc2 import TIER2_PROJECTS
from oc2.architecture import ParseError, parse, validate
from oc2.state import append_history, new_state, now_iso, read_state, write_state

# A fixed, valid architecture. 4 subsystems, valid DAG with a diamond shape that
# exercises the topological sort and its alphabetical tiebreak. The {title}
# placeholder is filled with the project name so the doc reads coherently.
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


def mock_llm_design(task: str, project_title: str) -> str:
    """Stand-in for the design-phase LLM call. Ignores the task content and
    returns the canned architecture with its title set to the project name."""
    return MOCK_ARCHITECTURE.format(title=project_title)


def derive_name(task: str) -> str:
    """Kebab-case the first 4 words of the task (doc Section 6: no LLM naming)."""
    words = re.findall(r"[A-Za-z0-9]+", task.lower())[:4]
    name = "-".join(words)
    return name or "untitled-project"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rotate_existing_architecture(project_dir: Path, version_count: int) -> Path | None:
    """Move the current architecture.md to architecture.v<N>.md. Returns the
    rotated path, or None if there was nothing to rotate."""
    current = project_dir / "architecture.md"
    if not current.exists():
        return None
    rotated = project_dir / f"architecture.v{version_count}.md"
    current.rename(rotated)
    return rotated


def cmd_design(args) -> int:
    task = args.task
    explicit_name = bool(args.name)
    name = (args.name or derive_name(task)).strip().lower()

    if args.from_path:
        print(f"note: --from is accepted but not wired in Session 1 (the LLM is "
              f"mocked); ignoring {args.from_path!r}.")

    project_dir = TIER2_PROJECTS / name
    exists = project_dir.exists()

    # Q8: an auto-derived name that collides refuses; an explicit --name on an
    # existing project is an intentional regeneration (rotate the old version).
    if exists and not explicit_name:
        print(f"error: project {name!r} already exists in tier2_projects/.")
        print(f"       Re-run with --name <other> to choose a distinct name,")
        print(f"       or pass --name {name} to intentionally regenerate it.")
        return 1

    project_dir.mkdir(parents=True, exist_ok=True)

    state = read_state(project_dir) or new_state(name)
    version_count = state["architecture"].get("version_count", 0)

    # Generate (mock) + parse + validate before touching architecture.md so a
    # bad generation never clobbers a good prior version.
    md = mock_llm_design(task, name)
    try:
        arch = parse(md)
    except ParseError as e:
        print(f"error: generated architecture did not parse: {e}")
        return 1
    res = validate(arch)

    # Rotate the prior version (if regenerating), then write the new doc. The doc
    # is always written — even when invalid — so the human can read what the LLM
    # produced (doc Section 2, "Pre-review validation").
    if exists and explicit_name:
        rotated = _rotate_existing_architecture(project_dir, version_count)
        if rotated:
            print(f"rotated previous architecture -> {rotated.name}")
        version_count += 1

    arch_path = project_dir / "architecture.md"
    arch_path.write_text(md, encoding="utf-8")

    # Refresh state. A new design supersedes any prior approval.
    state["project_name"] = name
    state["architecture"]["current_path"] = "architecture.md"
    state["architecture"]["current_sha256"] = _sha256_text(md)
    state["architecture"]["version_count"] = version_count
    state["approval"] = {
        "approved": False,
        "approved_at": None,
        "approved_architecture_sha256": None,
    }
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
    append_history(state, "design", architecture_sha256=state["architecture"]["current_sha256"])
    state["last_session_at"] = now_iso()
    write_state(project_dir, state)

    rel = arch_path.relative_to(TIER2_PROJECTS.parent)
    print(f"Architecture written to {rel}")
    if res.warnings:
        print("\nwarnings (advisory, do not block approval):")
        for w in res.warnings:
            print(f"  - {w}")
    if not res.ok:
        print("\nVALIDATION FAILED — architecture is not approvable as-is:")
        for e in res.errors:
            print(f"  - {e}")
        print("\nEdit the file to fix these, or re-run `oc2 design ... --name "
              f"{name}` to regenerate.")
        return 1

    print(f"\n{len(arch.subsystems)} subsystems, valid DAG. "
          f"Read it, edit if needed, then:\n    oc2 approve {name}")
    return 0
