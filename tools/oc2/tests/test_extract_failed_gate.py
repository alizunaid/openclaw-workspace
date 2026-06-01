"""S13 STEP 1 — _extract_failed_gate handles all three sub-gate log shapes.

Pre-S11 the extractor only knew dict-with-`ok:False`, so a smoke_tests failure
(a LIST) or an entry_execution failure (dict-with-`rc`, no `ok`) surfaced as a
bare `phase=cross_file status=failed` with no sub-gate detail. These tests feed
each real shape and assert the right sub-gate + a useful error line.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.build import _extract_failed_gate, _failed_subgate_detail  # noqa: E402


def _write_log(tmp: Path, phases: dict) -> Path:
    p = tmp / "oc_build_test.json"
    p.write_text(json.dumps({"phases": phases}), encoding="utf-8")
    return p


class TestExtractFailedGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oc2_extract_test_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dict_with_ok_false(self):
        # dep_honesty-style: dict with ok:False
        log = _write_log(self.tmp, {"cross_file": {
            "status": "failed",
            "contracts": {"ok": True},
            "dep_honesty": {"ok": False, "self_imports": ["utils.py"]},
        }})
        out = _extract_failed_gate(log)
        self.assertIn("phase=`cross_file`", out)
        self.assertIn("sub-gate `dep_honesty` failed", out)

    def test_entry_execution_rc_shape(self):
        # entry_execution: dict with rc, NO ok key
        log = _write_log(self.tmp, {"cross_file": {
            "status": "failed",
            "dry_import": {"ok": True},
            "entry_execution": {"rc": 1, "stdout": "",
                                "stderr": "Traceback...\nValueError: I/O operation on closed file"},
        }})
        out = _extract_failed_gate(log)
        self.assertIn("sub-gate `entry_execution`", out)
        self.assertIn("rc=1", out)
        # surfaces a useful error line (first non-blank)
        self.assertIn("Traceback", out)

    def test_smoke_tests_list_shape(self):
        # smoke_tests: LIST of dicts with rc — the S11 gap
        log = _write_log(self.tmp, {"cross_file": {
            "status": "failed",
            "entry_execution": {"rc": 0, "stdout": "ok", "stderr": ""},
            "smoke_tests": [{"path": "smoke_test.py", "rc": 1, "stdout": "",
                             "stderr": "AssertionError"}],
        }})
        out = _extract_failed_gate(log)
        self.assertIn("sub-gate `smoke_tests`", out)
        self.assertIn("smoke_test.py", out)
        self.assertIn("rc=1", out)
        self.assertIn("AssertionError", out)

    def test_rc_zero_subgate_not_flagged(self):
        # an rc==0 entry_execution must NOT be reported as the failure
        detail = _failed_subgate_detail({
            "entry_execution": {"rc": 0, "stdout": "fine", "stderr": ""},
            "smoke_tests": [{"path": "t.py", "rc": 1, "stderr": "boom"}],
        })
        # smoke_tests is the real failure, not entry_execution
        self.assertIn("smoke_tests", detail)
        self.assertNotIn("entry_execution", detail)

    def test_real_s11_smoke_log(self):
        # The actual S11 roll-3 log, if present, must now surface smoke_tests.
        real = Path(__file__).resolve().parents[3] / "logs" / "oc_build_1780273462.json"
        if not real.exists():
            self.skipTest("S11 smoke log not on disk")
        out = _extract_failed_gate(real)
        self.assertIn("sub-gate `smoke_tests`", out)

    def test_missing_log_is_tolerant(self):
        self.assertIn("no state log", _extract_failed_gate(self.tmp / "nope.json"))


if __name__ == "__main__":
    unittest.main()
