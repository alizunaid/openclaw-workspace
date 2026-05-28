"""`oc2 list`, `oc2 status`, and the `oc2 archive` stub.

list   — every project with a one-word lifecycle label.
status — one project's per-subsystem build state.
archive — stubbed in Session 1 (project archival lands later).
"""
from __future__ import annotations

from pathlib import Path

from oc2 import TIER2_PROJECTS
from oc2.approve import resolve_project
from oc2.state import read_state

# Per-subsystem statuses that count as "not done yet" / "broken".
_BROKEN = {"failed", "failed_or_killed"}


def _project_dirs() -> list[Path]:
    if not TIER2_PROJECTS.exists():
        return []
    return sorted(
        p for p in TIER2_PROJECTS.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def _lifecycle_label(state: dict | None) -> str:
    """Collapse a project's state into one lifecycle word for `oc2 list`."""
    if not state or not state.get("architecture", {}).get("current_sha256"):
        return "pending-architecture"
    if not state.get("approval", {}).get("approved"):
        return "awaiting-approval"
    subs = state.get("subsystems", {})
    statuses = [s.get("status", "pending") for s in subs.values()]
    if any(s in _BROKEN for s in statuses):
        return "failed"
    if statuses and all(s == "done" for s in statuses):
        return "done"
    if any(s in ("in_progress", "done") for s in statuses):
        return "building"
    return "approved"


def cmd_list(args) -> int:
    dirs = _project_dirs()
    if not dirs:
        print('No tier-2 projects yet. Run: oc2 design "<task>" --name <name>')
        return 0
    width = max(max(len(p.name) for p in dirs), len("PROJECT"))
    print(f"{'PROJECT':<{width}}  {'STATE':<20}  SUBSYSTEMS")
    for p in dirs:
        state = read_state(p)
        label = _lifecycle_label(state)
        n = len(state.get("subsystems", {})) if state else 0
        print(f"{p.name:<{width}}  {label:<20}  {n}")
    return 0


def cmd_status(args) -> int:
    name = resolve_project(args.project)
    if name is None:
        print("error: specify which project, e.g. `oc2 status <name>`. "
              "Use `oc2 list` to see all projects.")
        return 1
    project_dir = TIER2_PROJECTS / name
    if not project_dir.exists():
        print(f"error: no project {name!r} in tier2_projects/.")
        return 1
    state = read_state(project_dir)
    if state is None:
        print(f"{name}: no state.json yet (design has not run).")
        return 1

    approved = state.get("approval", {}).get("approved")
    sha = (state.get("architecture", {}).get("current_sha256") or "")[:12]
    print(f"project:   {name}")
    print(f"state:     {_lifecycle_label(state)}")
    print(f"approved:  {approved}")
    print(f"arch sha:  {sha}")
    subs = state.get("subsystems", {})
    if not subs:
        print("subsystems: (none)")
        return 0
    width = max(len(n) for n in subs)
    print("\nsubsystems:")
    for sub_name in sorted(subs):
        info = subs[sub_name]
        line = f"  {sub_name:<{width}}  {info.get('status', '?')}"
        if info.get("ocb_run_id"):
            line += f"  (run {info['ocb_run_id']})"
        if info.get("error"):
            line += f"  ERROR: {info['error']}"
        print(line)
    return 0


def cmd_archive(args) -> int:
    print("oc2 archive: not yet implemented in Session 1.")
    return 0
