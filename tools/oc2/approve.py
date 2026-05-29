"""`oc2 approve` — re-parse + re-validate the (possibly hand-edited)
architecture.md, apply spec-diff cascading invalidation, and lock for build.

Session 3 wires the make-style cascade (locked Q7): per-subsystem spec_sha256
diff against prior state.json, forward closure over the dependency graph,
preservation of build status for surviving-unchanged subsystems (including
`failed` / `failed_or_killed` — the user may want to retry), and a tidy move
of removed subsystems' source directories to subsystems/.removed/<name>-<ts>/
instead of deleting them.

The CLI dispatcher (`cmd_approve`) is a thin wrapper around `approve_project`,
which takes a project_dir + name so integration tests can drive it against a
tmpdir without monkey-patching TIER2_PROJECTS.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from oc2 import TIER2_PROJECTS
from oc2.architecture import ParseError, parse, validate
from oc2.diff import diff_subsystems, propagate_pending
from oc2.state import append_history, new_state, now_iso, read_state, write_state

_REMOVED_TS_RE = "%Y%m%dT%H%M%SZ"  # filename-safe compact UTC, e.g. 20260528T030400Z


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_project(name: str | None) -> str | None:
    """Resolve a project name from the CLI: explicit, or the only project, else None."""
    if name:
        return name.strip().lower()
    if not TIER2_PROJECTS.exists():
        return None
    projects = [p.name for p in TIER2_PROJECTS.iterdir()
                if p.is_dir() and not p.name.startswith(".")]
    if len(projects) == 1:
        return projects[0]
    return None


def _pending_entry(spec_sha256: str, source_dir: str) -> dict:
    """A fresh per-subsystem state entry with build artifacts cleared."""
    return {
        "status": "pending",
        "spec_sha256": spec_sha256,
        "ocb_run_id": None,
        "source_dir": source_dir,
        "built_at": None,
        "error": None,
    }


def _compact_ts() -> str:
    """Filename-safe UTC timestamp (second precision) for .removed/ suffixes."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime(_REMOVED_TS_RE)


def _move_to_removed(project_dir: Path, name: str) -> Path | None:
    """Move subsystems/<name>/ -> subsystems/.removed/<name>-<ts>/ if present.
    Returns the destination path on a successful move, None if nothing to move."""
    src = project_dir / "subsystems" / name
    if not src.exists():
        return None
    removed_dir = project_dir / "subsystems" / ".removed"
    removed_dir.mkdir(parents=True, exist_ok=True)
    ts = _compact_ts()
    dst = removed_dir / f"{name}-{ts}"
    # Defensive against the rare same-second double approve.
    i = 2
    while dst.exists():
        dst = removed_dir / f"{name}-{ts}_{i}"
        i += 1
    shutil.move(str(src), str(dst))
    return dst


def _print_list(label: str, names: list[str], suffix: str = "") -> None:
    if names:
        print(f"  {label:<18} {', '.join(names)}{suffix}")


def approve_project(project_dir: Path, name: str) -> int:
    """Re-parse, re-validate, apply cascading diff, write state. Returns exit
    code. State is left UNCHANGED on validation failure."""
    project_dir = Path(project_dir)
    arch_path = project_dir / "architecture.md"
    if not project_dir.exists():
        print(f"error: no project {name!r} in tier2_projects/.")
        return 1
    if not arch_path.exists():
        print(f"error: {name!r} has no architecture.md. "
              f"Run `oc2 design ... --name {name}` first.")
        return 1

    md = arch_path.read_text(encoding="utf-8")
    try:
        arch = parse(md)
    except ParseError as e:
        print(f"error: architecture.md did not parse: {e}")
        print("State unchanged.")
        return 1

    res = validate(arch)
    if res.warnings:
        print("warnings (advisory):")
        for w in res.warnings:
            print(f"  - {w}")
    if not res.ok:
        print(f"\nNOT APPROVED — {name!r} failed validation:")
        for e in res.errors:
            print(f"  - {e}")
        print("\nFix architecture.md and re-run `oc2 approve` "
              f"{name}. State unchanged.")
        return 1

    sha = _sha256_text(md)
    state = read_state(project_dir) or new_state(name)
    prior_map = state.get("subsystems", {})
    first_approval = not prior_map

    # --- the cascade ---
    diff = diff_subsystems(prior_map, arch.subsystems)
    initial_pending = set(diff.surviving_changed) | set(diff.added)
    closure = propagate_pending(initial_pending, arch)
    cascade_extra = sorted(closure - initial_pending)  # pulled in only by propagation

    # --- new subsystems map ---
    new_map: dict[str, dict] = {}
    for s in arch.subsystems:
        source_dir = f"subsystems/{s.name}/"
        if s.name in closure:
            # Modified, added, or transitively dependent of either: rebuild.
            new_map[s.name] = _pending_entry(s.spec_sha256(), source_dir)
        else:
            # Surviving unchanged AND not pulled into cascade: preserve verbatim.
            # This is the path that keeps `failed` / `done` statuses across
            # re-approvals when the spec text is byte-identical.
            preserved = dict(prior_map[s.name])
            # Re-stamp spec_sha256 (idempotent — it already matches) and
            # source_dir in case the convention ever shifts; everything else
            # (status, ocb_run_id, built_at, error) stays.
            preserved["spec_sha256"] = s.spec_sha256()
            preserved["source_dir"] = source_dir
            new_map[s.name] = preserved
    state["subsystems"] = new_map

    # --- removed subsystems: move source out of the way ---
    moved_paths: dict[str, str] = {}  # name -> relative removed path (for the report)
    for removed_name in diff.removed:
        dst = _move_to_removed(project_dir, removed_name)
        if dst is not None:
            moved_paths[removed_name] = str(dst.relative_to(project_dir))

    # --- state updates + history ---
    state["project_name"] = name
    state["architecture"]["current_path"] = "architecture.md"
    state["architecture"]["current_sha256"] = sha
    state["architecture"]["valid"] = True
    # Clear any stale raw_path from a prior failed-design run.
    if state["architecture"].get("raw_path"):
        state["architecture"]["raw_path"] = None
    state["approval"] = {
        "approved": True,
        "approved_at": now_iso(),
        "approved_architecture_sha256": sha,
    }
    append_history(
        state, "approve",
        architecture_sha256=sha,
        diff={
            "surviving_unchanged": len(diff.surviving_unchanged),
            "modified": len(diff.surviving_changed),
            "added": len(diff.added),
            "removed": len(diff.removed),
            "cascade_size": len(closure),
        },
    )
    state["last_session_at"] = now_iso()
    write_state(project_dir, state)

    # --- structured report ---
    print()  # blank line for readability
    if first_approval:
        print(f"First approval: {len(arch.subsystems)} subsystems will be built.")
    elif diff.is_empty_diff():
        print("No spec changes since last approval.")
    else:
        print("Diff:")
        # `Unchanged` lists only the subsystems whose status will be preserved
        # verbatim. A spec-unchanged subsystem that gets pulled into the cascade
        # is NOT preserved (it goes pending), so it appears under Cascade only.
        truly_preserved = [n for n in diff.surviving_unchanged if n not in closure]
        _print_list("Unchanged:", truly_preserved)
        _print_list("Modified:", diff.surviving_changed,
                    suffix="   (spec_sha256 changed; rebuild required)")
        _print_list("Added:", diff.added)
        if diff.removed:
            removed_with_paths = [
                f"{n} (moved to {moved_paths[n]})" if n in moved_paths else f"{n} (no source dir)"
                for n in diff.removed
            ]
            _print_list("Removed:", removed_with_paths)
        if cascade_extra:
            print(f"  Cascade also marks pending: {', '.join(cascade_extra)}"
                  f"   (transitive dependents)")

    print(f"\nApproved {name!r}: {len(arch.subsystems)} subsystems locked for build.")
    print(f"    topological order: {', '.join(res.topo_order)}")
    print(f"Next:\n    oc2 build {name}")
    return 0


def cmd_approve(args) -> int:
    name = resolve_project(args.project)
    if name is None:
        print("error: specify which project to approve, e.g. `oc2 approve <name>`.")
        return 1
    return approve_project(TIER2_PROJECTS / name, name)
