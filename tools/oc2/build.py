"""`oc2 build` — topological execution of an approved architecture.

STUB (Session 1). The build phase translates each subsystem into a Tier 1
(`ocb`) subprocess invocation, executes them in topological order, harvests the
ocb state log, and updates state.json one subsystem at a time. It lands in
Session 4 (doc Section 3); failure handling + the `--only` escape hatch +
integration smoke land in Session 5.
"""
from __future__ import annotations


def cmd_build(args) -> int:
    print("oc2 build: not yet implemented in Session 1 (build phase lands in Session 4).")
    if getattr(args, "only", None):
        print(f"  (--only {args.only} noted; single-subsystem rebuild is a Session 5 escape hatch.)")
    return 0
