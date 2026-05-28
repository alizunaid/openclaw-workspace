"""`oc2 approve` — re-parse + re-validate the (possibly hand-edited)
architecture.md and lock it for build.

The human reads and edits architecture.md between `design` and `approve`, so
approve must re-parse the file on disk (not trust the design-time result) and
re-run the full validator. If clean, it records the approval in state.json.

Session 1 scope: approve re-parses, re-validates, and rebuilds the subsystems
map (carrying over any prior build status for surviving subsystems, defaulting
new ones to pending). The spec-diff cascading-rebuild logic — marking changed
subsystems + their transitive dependents pending — is Session 3 and is NOT done
here (build does not exist yet, so everything is pending regardless).
"""
from __future__ import annotations

import hashlib

from oc2 import TIER2_PROJECTS
from oc2.architecture import ParseError, parse, validate
from oc2.state import append_history, new_state, now_iso, read_state, write_state


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_project(name: str | None) -> str | None:
    """Resolve a project name: use the given name, or if exactly one project
    exists use it, otherwise return None (caller prints guidance)."""
    if name:
        return name.strip().lower()
    if not TIER2_PROJECTS.exists():
        return None
    projects = [p.name for p in TIER2_PROJECTS.iterdir()
                if p.is_dir() and not p.name.startswith(".")]
    if len(projects) == 1:
        return projects[0]
    return None


def cmd_approve(args) -> int:
    name = resolve_project(args.project)
    if name is None:
        print("error: specify which project to approve, e.g. `oc2 approve <name>`.")
        return 1

    project_dir = TIER2_PROJECTS / name
    if not project_dir.exists():
        print(f"error: no project {name!r} in tier2_projects/.")
        return 1
    arch_path = project_dir / "architecture.md"
    if not arch_path.exists():
        print(f"error: {name!r} has no architecture.md. Run `oc2 design ... --name {name}` first.")
        return 1

    md = arch_path.read_text(encoding="utf-8")
    try:
        arch = parse(md)
    except ParseError as e:
        print(f"error: architecture.md did not parse: {e}")
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
              f"{name}.")
        return 1

    sha = _sha256_text(md)
    state = read_state(project_dir) or new_state(name)
    prior = state.get("subsystems", {})

    # Rebuild the subsystems map from the current doc. Surviving subsystems keep
    # their build status; new ones start pending. (No spec-diff invalidation in
    # Session 1 — that is Session 3.)
    state["subsystems"] = {}
    for s in arch.subsystems:
        old = prior.get(s.name, {})
        state["subsystems"][s.name] = {
            "status": old.get("status", "pending"),
            "spec_sha256": s.spec_sha256(),
            "ocb_run_id": old.get("ocb_run_id"),
            "source_dir": f"subsystems/{s.name}/",
            "built_at": old.get("built_at"),
            "error": old.get("error"),
        }

    state["project_name"] = name
    state["architecture"]["current_sha256"] = sha
    state["approval"] = {
        "approved": True,
        "approved_at": now_iso(),
        "approved_architecture_sha256": sha,
    }
    append_history(state, "approve", architecture_sha256=sha)
    state["last_session_at"] = now_iso()
    write_state(project_dir, state)

    print(f"\nApproved {name!r}: {len(arch.subsystems)} subsystems locked for build.")
    print(f"    topological order: {', '.join(res.topo_order)}")
    print(f"Next:\n    oc2 build {name}")
    return 0
