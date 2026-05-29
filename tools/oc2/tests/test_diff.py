"""Unit tests for oc2.diff — diff_subsystems and propagate_pending.

These are pure-function tests: we build Subsystem and Architecture objects by
hand (no parser involvement) so the diff logic can be tested in isolation.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.architecture import Architecture, Subsystem  # noqa: E402
from oc2.diff import SubsystemDiff, diff_subsystems, propagate_pending  # noqa: E402


def mk_sub(name: str, depends_on=(), version: str = "v1") -> Subsystem:
    """Build a Subsystem with a deterministic raw_section, so spec_sha256 is
    stable + predictable. The `version` knob lets a test mutate one subsystem's
    sha without changing its name or deps."""
    s = Subsystem(name=name, depends_on=list(depends_on))
    s.raw_section = f"### {name}\n[content:{version}]"
    return s


def mk_arch(*subs: Subsystem) -> Architecture:
    return Architecture(project_name="test", subsystems=list(subs))


def mk_prior(name_to_sub: dict, extras: dict | None = None) -> dict:
    """Build a prior subsystems map keyed by name. Each entry just needs
    spec_sha256; we fill the rest with placeholder values approve.py expects."""
    extras = extras or {}
    out = {}
    for name, sub in name_to_sub.items():
        entry = {
            "status": "done",
            "spec_sha256": sub.spec_sha256(),
            "ocb_run_id": f"run_{name}",
            "source_dir": f"subsystems/{name}/",
            "built_at": "2026-01-01T00:00:00Z",
            "error": None,
        }
        entry.update(extras.get(name, {}))
        out[name] = entry
    return out


class TestDiffSubsystems(unittest.TestCase):
    def test_no_op_all_unchanged(self):
        a = mk_sub("a"); b = mk_sub("b", depends_on=["a"])
        prior = mk_prior({"a": a, "b": b})
        d = diff_subsystems(prior, [a, b])
        self.assertEqual(d.surviving_unchanged, ["a", "b"])
        self.assertEqual(d.surviving_changed, [])
        self.assertEqual(d.added, [])
        self.assertEqual(d.removed, [])
        self.assertTrue(d.is_empty_diff())

    def test_single_spec_changed(self):
        a = mk_sub("a"); b_old = mk_sub("b", depends_on=["a"], version="v1")
        prior = mk_prior({"a": a, "b": b_old})
        b_new = mk_sub("b", depends_on=["a"], version="v2")  # raw_section differs
        d = diff_subsystems(prior, [a, b_new])
        self.assertEqual(d.surviving_unchanged, ["a"])
        self.assertEqual(d.surviving_changed, ["b"])
        self.assertEqual(d.added, [])
        self.assertEqual(d.removed, [])
        self.assertFalse(d.is_empty_diff())

    def test_added(self):
        a = mk_sub("a")
        prior = mk_prior({"a": a})
        b = mk_sub("b", depends_on=["a"])
        d = diff_subsystems(prior, [a, b])
        self.assertEqual(d.surviving_unchanged, ["a"])
        self.assertEqual(d.added, ["b"])
        self.assertEqual(d.surviving_changed, [])
        self.assertEqual(d.removed, [])

    def test_removed(self):
        a = mk_sub("a"); b = mk_sub("b", depends_on=["a"])
        prior = mk_prior({"a": a, "b": b})
        d = diff_subsystems(prior, [a])
        self.assertEqual(d.surviving_unchanged, ["a"])
        self.assertEqual(d.removed, ["b"])
        self.assertEqual(d.added, [])
        self.assertEqual(d.surviving_changed, [])

    def test_simultaneous_add_remove_change(self):
        a_old = mk_sub("a", version="v1")
        b = mk_sub("b", depends_on=["a"])
        prior = mk_prior({"a": a_old, "b": b})
        a_new = mk_sub("a", version="v2")  # changed
        # b removed; c added
        c = mk_sub("c", depends_on=["a"])
        d = diff_subsystems(prior, [a_new, c])
        self.assertEqual(d.surviving_unchanged, [])
        self.assertEqual(d.surviving_changed, ["a"])
        self.assertEqual(d.added, ["c"])
        self.assertEqual(d.removed, ["b"])

    def test_first_approval_everything_added(self):
        a = mk_sub("a"); b = mk_sub("b", depends_on=["a"])
        d = diff_subsystems({}, [a, b])
        self.assertEqual(d.added, ["a", "b"])
        self.assertEqual(d.surviving_unchanged, [])
        self.assertEqual(d.surviving_changed, [])
        self.assertEqual(d.removed, [])

    def test_missing_prior_sha_forces_changed(self):
        """A prior entry with no spec_sha256 cannot be trusted as unchanged —
        safer to force a rebuild than silently treat absent-sha as equal."""
        a = mk_sub("a")
        prior = {"a": {"status": "done"}}  # no spec_sha256
        d = diff_subsystems(prior, [a])
        self.assertEqual(d.surviving_changed, ["a"])
        self.assertEqual(d.surviving_unchanged, [])

    def test_lists_are_sorted(self):
        x = mk_sub("x"); a = mk_sub("a"); m = mk_sub("m")
        d = diff_subsystems({}, [x, a, m])
        self.assertEqual(d.added, ["a", "m", "x"])  # sorted, not input order


class TestPropagatePending(unittest.TestCase):
    def test_empty_initial_returns_empty(self):
        a = mk_sub("a"); b = mk_sub("b", depends_on=["a"])
        self.assertEqual(propagate_pending(set(), mk_arch(a, b)), set())

    def test_root_with_no_dependents_closure_is_itself(self):
        a = mk_sub("a"); b = mk_sub("b")  # independent
        self.assertEqual(propagate_pending({"a"}, mk_arch(a, b)), {"a"})

    def test_linear_chain_cascades_forward(self):
        # a -> b -> c
        a = mk_sub("a")
        b = mk_sub("b", depends_on=["a"])
        c = mk_sub("c", depends_on=["b"])
        self.assertEqual(propagate_pending({"a"}, mk_arch(a, b, c)), {"a", "b", "c"})

    def test_linear_chain_initial_in_middle(self):
        a = mk_sub("a")
        b = mk_sub("b", depends_on=["a"])
        c = mk_sub("c", depends_on=["b"])
        # changing b should NOT pull a back in (cascade is forward, not backward)
        self.assertEqual(propagate_pending({"b"}, mk_arch(a, b, c)), {"b", "c"})

    def test_diamond_full_cascade(self):
        # a -> b, a -> c, b -> d, c -> d
        a = mk_sub("a")
        b = mk_sub("b", depends_on=["a"])
        c = mk_sub("c", depends_on=["a"])
        d_sub = mk_sub("d", depends_on=["b", "c"])
        self.assertEqual(
            propagate_pending({"a"}, mk_arch(a, b, c, d_sub)),
            {"a", "b", "c", "d"},
        )

    def test_diamond_partial_cascade(self):
        # Same diamond. Changing only b -> b and d, NOT a or c.
        a = mk_sub("a")
        b = mk_sub("b", depends_on=["a"])
        c = mk_sub("c", depends_on=["a"])
        d_sub = mk_sub("d", depends_on=["b", "c"])
        self.assertEqual(
            propagate_pending({"b"}, mk_arch(a, b, c, d_sub)),
            {"b", "d"},
        )

    def test_non_existent_name_in_initial_is_skipped(self):
        a = mk_sub("a"); b = mk_sub("b", depends_on=["a"])
        # 'ghost' is not in the arch; should be silently dropped.
        self.assertEqual(
            propagate_pending({"a", "ghost"}, mk_arch(a, b)),
            {"a", "b"},
        )

    def test_multiple_initial_seeds(self):
        a = mk_sub("a"); b = mk_sub("b"); c = mk_sub("c", depends_on=["a", "b"])
        self.assertEqual(
            propagate_pending({"a", "b"}, mk_arch(a, b, c)),
            {"a", "b", "c"},
        )

    def test_dangling_deps_dont_crash(self):
        """If a subsystem lists a depends_on that doesn't exist (which the
        validator would catch), propagate_pending must not blow up."""
        a = mk_sub("a", depends_on=["ghost"])
        b = mk_sub("b", depends_on=["a"])
        # propagating from 'a' should still cascade to 'b'.
        self.assertEqual(propagate_pending({"a"}, mk_arch(a, b)), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
