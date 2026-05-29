"""Spec-level diff and forward cascade propagation for oc2 approve.

When the human edits architecture.md and re-runs approve, we need to know:
  - which subsystems' specs CHANGED (spec_sha256 differs from prior state)
  - which subsystems were ADDED (in new doc, not in prior state)
  - which subsystems were REMOVED (in prior state, gone from new doc)
  - which subsystems survived UNCHANGED
And once we know the changed+added set (the "initial pending"), we propagate
forward through the dependency graph so every transitive dependent of any
pending subsystem is also marked pending. That is "make" semantics, locked
decision Q7.

This module is pure: no filesystem, no Tier 1, no parser. It takes a prior
state-map and a freshly-parsed list of `Subsystem` objects and returns a
report. The caller (approve.py) applies it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from oc2.architecture import Architecture, Subsystem


@dataclass
class SubsystemDiff:
    """Per-name classification of a re-approve. All lists are sorted for
    deterministic output across runs."""
    surviving_unchanged: list[str] = field(default_factory=list)
    surviving_changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def is_empty_diff(self) -> bool:
        """True iff every subsystem survived with the same spec."""
        return not (self.surviving_changed or self.added or self.removed)


def diff_subsystems(prior_map: dict, new_subs: list[Subsystem]) -> SubsystemDiff:
    """Classify subsystems against the prior state map.

    Args:
        prior_map: `state['subsystems']` from a prior approval (or {}). Each
            entry is expected to carry a `spec_sha256` field; if missing, the
            subsystem is treated as `surviving_changed` (force rebuild — safer
            than silently treating an absent sha as unchanged).
        new_subs: the subsystems just parsed from the current architecture.md.

    Returns:
        SubsystemDiff with each name placed in exactly one bucket.
    """
    new_by_name = {s.name: s for s in new_subs}
    diff = SubsystemDiff()

    for name, sub in new_by_name.items():
        old = prior_map.get(name)
        if old is None:
            diff.added.append(name)
            continue
        old_sha = old.get("spec_sha256")
        if old_sha and old_sha == sub.spec_sha256():
            diff.surviving_unchanged.append(name)
        else:
            diff.surviving_changed.append(name)

    for name in prior_map:
        if name not in new_by_name:
            diff.removed.append(name)

    for lst in (diff.surviving_unchanged, diff.surviving_changed,
                diff.added, diff.removed):
        lst.sort()
    return diff


def propagate_pending(initial: set[str], arch: Architecture) -> set[str]:
    """Forward BFS over the reverse-adjacency graph.

    Given a set of subsystems that have already been declared pending (e.g.,
    because their spec changed or they were newly added), return the set
    closed under "is depended on by" — i.e., every subsystem reachable forward
    from any initial member. The result includes the initial set.

    Dangling deps (a `depends_on` name that doesn't exist in `arch`) are
    silently skipped here; the validator catches them upstream and approve
    refuses to write state in that case.
    """
    names = {s.name for s in arch.subsystems}
    # Build the reverse adjacency: dependents_of[x] = subsystems that list x in
    # their `depends_on`. This is the "downstream" direction for cascade.
    dependents_of: dict[str, set[str]] = {n: set() for n in names}
    for s in arch.subsystems:
        for d in s.depends_on:
            if d in dependents_of:
                dependents_of[d].add(s.name)

    closure = {n for n in initial if n in names}
    queue = list(closure)
    while queue:
        node = queue.pop(0)
        for dep in dependents_of.get(node, ()):
            if dep not in closure:
                closure.add(dep)
                queue.append(dep)
    return closure
