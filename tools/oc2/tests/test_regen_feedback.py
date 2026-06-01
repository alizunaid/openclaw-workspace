"""S13 — Tier-1 regen-with-error-feedback loop (oc_builder.py).

Proves the LOOP LOGIC with a FAKE model — no ollama. `run_runtime_gates` takes
injected execute_fn / smoke_fn / do_regen, so we script broken->fixed sequences
and assert the loop's decisions (repair, no-fire-on-pass, smoke #7 entry-only +
halt-with-finding, no-progress, cycle, flag-off) plus the budget/guard math in
_RegenFeedback directly.

Lives in the oc2 test dir (the discovered suite); imports the Tier-1 module via
the tools/ path the harness already adds.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import oc_builder as ob  # noqa: E402
from oc_builder import (  # noqa: E402
    _RegenFeedback,
    _corrective_for_runtime,
    _error_signature,
    run_runtime_gates,
)

ENTRY = {"path": "main.py"}
SMOKE = [{"path": "smoke_test.py", "purpose": "smoke test"}]
RUN_DIR = Path("/tmp")  # fakes ignore it


def seq_fn(seq):
    """Return a fake execute/smoke fn that yields seq items in order (repeating
    the last). Each item is (rc, stdout, stderr)."""
    st = {"i": 0}

    def fn(path, run_dir):
        i = min(st["i"], len(seq) - 1)
        st["i"] += 1
        return seq[i]
    return fn


def regen_fn(ok=True):
    """Fake do_regen that records calls and reports success/failure."""
    calls = []

    def fn(gate, error_text, smoke_path=None):
        calls.append((gate, smoke_path))
        return ok, ""
    fn.calls = calls
    return fn


def run(execute, smoke, do_regen, fb=None, check_cap=None):
    fb = fb or _RegenFeedback()
    res = run_runtime_gates(ENTRY, SMOKE, RUN_DIR, fb,
                            execute_fn=execute, smoke_fn=smoke,
                            do_regen=do_regen, check_cap=check_cap)
    return res, fb


# --- the loop, end to end (fake model) --------------------------------------

class TestRunRuntimeGates(unittest.TestCase):
    def test_no_fire_on_pass(self):
        # All gates green -> loop never regenerates (the blast-radius guarantee).
        dr = regen_fn()
        res, fb = run(seq_fn([(0, "ok", "")]), seq_fn([(0, "", "")]), dr)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(dr.calls, [])
        self.assertEqual(fb.log, [])
        self.assertEqual(fb.global_used, 0)

    def test_entry_execution_broken_then_fixed(self):
        # rc=1 then rc=0 after a regen -> loop repairs, build proceeds.
        dr = regen_fn(ok=True)
        res, fb = run(seq_fn([(1, "", "ValueError: boom"), (0, "ok", "")]),
                      seq_fn([(0, "", "")]), dr)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(dr.calls), 1)
        self.assertEqual(dr.calls[0][0], "entry_execution")
        self.assertEqual(fb.global_used, 1)

    def test_smoke_broken_then_fixed_entry(self):
        dr = regen_fn(ok=True)
        res, fb = run(seq_fn([(0, "ok", "")]),
                      seq_fn([(1, "", "AssertionError"), (0, "", "")]), dr)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(dr.calls), 1)
        self.assertEqual(dr.calls[0][0], "smoke_tests")
        # #7: the regen targeted the entry, carrying the smoke path as context.
        self.assertEqual(dr.calls[0][1], "smoke_test.py")

    def test_smoke_bogus_test_halts_with_finding_entry_only(self):
        # The S11 roll-3 archetype: entry runs clean (rc 0), the test asserts a
        # string that never appears -> one entry regen doesn't help -> HALT with a
        # finding. The test is NEVER regenerated.
        dr = regen_fn(ok=True)  # regen "succeeds" but the smoke still fails
        res, fb = run(seq_fn([(0, "ok", "")]),
                      seq_fn([(1, "", "AssertionError")]), dr)  # smoke always fails
        self.assertEqual(res["status"], "fail")
        self.assertEqual(res["gate"], "smoke_tests")
        self.assertEqual(len(dr.calls), 1)            # exactly one entry regen
        self.assertTrue(all(c[0] == "smoke_tests" for c in dr.calls))
        # never regenerated the test file itself
        self.assertNotIn("smoke_test.py-as-target", str(dr.calls))

    def test_no_progress_same_error_stops_after_one_regen(self):
        # Same entry_execution error twice -> regen once, then no-progress halt.
        dr = regen_fn(ok=True)
        res, fb = run(seq_fn([(1, "", "ValueError: boom")]),  # always fails, same err
                      seq_fn([(0, "", "")]), dr)
        self.assertEqual(res["status"], "fail")
        self.assertEqual(res["gate"], "entry_execution")
        self.assertEqual(len(dr.calls), 1)            # did NOT spend a second regen
        self.assertIn("no-progress", res["reason"])

    def test_cycle_guard_halts_on_revisited_signature(self):
        # ee:A -> regen -> (ee ok) smoke:B -> regen -> ee:A again -> cycle halt.
        dr = regen_fn(ok=True)
        execute = seq_fn([(1, "", "ValueError: A"), (0, "ok", ""), (1, "", "ValueError: A")])
        smoke = seq_fn([(1, "", "AssertionError: B"), (0, "", "")])
        res, fb = run(execute, smoke, dr)
        self.assertEqual(res["status"], "fail")
        self.assertEqual(len(dr.calls), 2)            # ee once + smoke once, then halt
        self.assertIn("no-progress/cycle", res["reason"])

    def test_regen_itself_failing_halts(self):
        # do_regen returns ok=False (LLM/AST failure) -> halt, no infinite loop.
        dr = regen_fn(ok=False)
        res, fb = run(seq_fn([(1, "", "ValueError: boom")]), seq_fn([(0, "", "")]), dr)
        self.assertEqual(res["status"], "fail")
        self.assertEqual(len(dr.calls), 1)
        self.assertIn("regeneration failed", res["reason"])

    def test_flag_off_is_pre_s13_behavior(self):
        # REGEN_FEEDBACK_ENABLED=False -> first failure halts, regen never tried,
        # no regen_feedback log written.
        saved = ob.REGEN_FEEDBACK_ENABLED
        ob.REGEN_FEEDBACK_ENABLED = False
        try:
            dr = regen_fn(ok=True)
            res, fb = run(seq_fn([(1, "", "ValueError: boom")]), seq_fn([(0, "", "")]), dr)
        finally:
            ob.REGEN_FEEDBACK_ENABLED = saved
        self.assertEqual(res["status"], "fail")
        self.assertEqual(dr.calls, [])
        self.assertEqual(fb.log, [])


# --- the budget/guard math (directly) ---------------------------------------

class TestRegenFeedbackGuards(unittest.TestCase):
    def test_per_gate_budget_caps_at_one(self):
        fb = _RegenFeedback()
        ok, _ = fb.consider("entry_execution", "err A")
        self.assertTrue(ok)
        fb.record_regen("entry_execution", "err A", accepted=True)
        # a DIFFERENT error on the same gate is now blocked by the per-gate budget
        ok2, reason = fb.consider("entry_execution", "totally different error B")
        self.assertFalse(ok2)
        self.assertIn("per-gate", reason)

    def test_global_budget_blocks_a_third_gate(self):
        # In the 2-gate v1 the per-gate budget (1 each) binds first, so global=3
        # is headroom; exercise it directly with >2 distinct gate names.
        fb = _RegenFeedback()
        for g in ("entry_execution", "smoke_tests", "dep_honesty"):
            ok, _ = fb.consider(g, f"err {g}")
            self.assertTrue(ok, msg=g)
            fb.record_regen(g, f"err {g}", accepted=True)
        self.assertEqual(fb.global_used, ob.REGEN_FEEDBACK_GLOBAL)
        ok4, reason = fb.consider("dry_import", "err four")
        self.assertFalse(ok4)
        self.assertIn("global", reason)

    def test_no_progress_signature_match(self):
        fb = _RegenFeedback()
        ok, _ = fb.consider("entry_execution", "Traceback\nValueError: x at line 9")
        self.assertTrue(ok)
        fb.record_regen("entry_execution", "Traceback\nValueError: x at line 9", accepted=True)
        # same logical error, different line number -> same signature -> blocked
        ok2, reason = fb.consider("entry_execution", "Traceback\nValueError: x at line 42")
        self.assertFalse(ok2)
        self.assertIn("no-progress", reason)

    def test_disabled_short_circuits_without_logging(self):
        saved = ob.REGEN_FEEDBACK_ENABLED
        ob.REGEN_FEEDBACK_ENABLED = False
        try:
            fb = _RegenFeedback()
            ok, reason = fb.consider("entry_execution", "err")
            self.assertFalse(ok)
            self.assertEqual(fb.log, [])
        finally:
            ob.REGEN_FEEDBACK_ENABLED = saved


# --- the pure helpers -------------------------------------------------------

class TestSignatureAndCorrective(unittest.TestCase):
    def test_signature_normalizes_paths_and_numbers(self):
        a = _error_signature("entry_execution",
                             'File "/tmp/run_111/main.py", line 9\nValueError: bad value 5')
        b = _error_signature("entry_execution",
                             'File "/tmp/run_999/main.py", line 88\nValueError: bad value 7')
        self.assertEqual(a, b)  # same logical error -> identical signature

    def test_signature_differs_by_gate(self):
        self.assertNotEqual(_error_signature("entry_execution", "AssertionError"),
                            _error_signature("smoke_tests", "AssertionError"))

    def test_corrective_smoke_forbids_test_edit_and_corruption(self):
        msg = _corrective_for_runtime("smoke_tests", "main.py", "AssertionError",
                                      smoke_path="smoke_test.py")
        self.assertIn("may not edit the test", msg)
        self.assertIn("UNCHANGED", msg)            # anti-corruption (#7)
        self.assertIn("smoke_test.py", msg)

    def test_corrective_entry_execution_mentions_no_arg_exit0(self):
        msg = _corrective_for_runtime("entry_execution", "main.py",
                                      "ValueError: I/O operation on closed file")
        self.assertIn("main.py", msg)
        self.assertIn("exit 0", msg)
        self.assertIn("I/O operation on closed file", msg)


if __name__ == "__main__":
    unittest.main()
