"""S15 — surface a degraded manifest LOUDLY.

When ocb's manifest planner output is rejected (e.g. a hyphenated entry path
like `file-writer.py` trips FILENAME_RE), ocb degrades to its single-file legacy
path and slugs the filename from the prompt header — but the build still reports
GREEN. Pre-S15 oc2 left no trace of this; the S14 file-writer module came out as
`build_subsystem_file_writer_purpose_writ.py` with nobody the wiser. These tests
cover `_manifest_degrade_detail` (the detector) and the BUILD_REPORT surface.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.build import _manifest_degrade_detail, build_report_markdown  # noqa: E402


def _write_log(tmp: Path, phases: dict) -> Path:
    p = tmp / "oc_build_test.json"
    p.write_text(json.dumps({"phases": phases}), encoding="utf-8")
    return p


class TestManifestDegradeDetail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oc2_degrade_test_"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_degraded_manifest_returns_reason_with_first_error(self):
        log = _write_log(self.tmp, {"manifest": {
            "status": "degraded",
            "reason": "validation failed",
            "errors": ["entry[0]: invalid path 'file-writer.py' (must be bare filename ending .py)"],
        }})
        detail = _manifest_degrade_detail(log)
        self.assertIsNotNone(detail)
        self.assertIn("validation failed", detail)
        self.assertIn("file-writer.py", detail)

    def test_degraded_without_errors_list_uses_reason(self):
        log = _write_log(self.tmp, {"manifest": {
            "status": "degraded", "reason": "planner returned no JSON"}})
        self.assertEqual(_manifest_degrade_detail(log), "planner returned no JSON")

    def test_ok_manifest_returns_none(self):
        log = _write_log(self.tmp, {"manifest": {"status": "ok"}})
        self.assertIsNone(_manifest_degrade_detail(log))

    def test_no_manifest_phase_returns_none(self):
        log = _write_log(self.tmp, {"cross_file": {"status": "ok"}})
        self.assertIsNone(_manifest_degrade_detail(log))

    def test_missing_log_is_tolerant(self):
        self.assertIsNone(_manifest_degrade_detail(self.tmp / "nope.json"))
        self.assertIsNone(_manifest_degrade_detail(None))

    def test_real_s14_file_writer_log(self):
        """The actual S14 file-writer run degraded on the hyphen — assert the
        detector surfaces it from the on-disk log if present."""
        real = Path(__file__).resolve().parents[3] / "logs" / "oc_build_1780336537.json"
        if not real.exists():
            self.skipTest("S14 file-writer log not on disk")
        detail = _manifest_degrade_detail(real)
        self.assertIsNotNone(detail)
        self.assertIn("file-writer.py", detail)


class TestBuildReportSurfacesDegrade(unittest.TestCase):
    def _state(self, degraded_reason=None):
        e = {"status": "done", "ocb_run_id": "run_1", "built_at": "2026-06-01T00:00:00Z",
             "generated_files": ["build_subsystem_file_writer_purpose_writ.py"]}
        if degraded_reason:
            e["manifest_degraded"] = degraded_reason
        return {"subsystems": {"file-writer": e}}

    def test_report_lists_degrade_when_present(self):
        md = build_report_markdown(
            self._state("validation failed: invalid path 'file-writer.py'"),
            ["file-writer"], "numstat", smoke_summary="ok", smoke_ok=True)
        self.assertIn("## Manifest degrades", md)
        self.assertIn("`file-writer`", md)
        self.assertIn("invalid path 'file-writer.py'", md)

    def test_report_omits_section_when_no_degrade(self):
        md = build_report_markdown(
            self._state(None), ["file-writer"], "numstat",
            smoke_summary="ok", smoke_ok=True)
        self.assertNotIn("Manifest degrades", md)


if __name__ == "__main__":
    unittest.main()
