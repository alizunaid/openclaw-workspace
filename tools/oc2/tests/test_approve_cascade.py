"""Integration tests for the cascading approve flow.

We drive `approve_project(project_dir, name)` directly with a tmpdir so the
test is independent of TIER2_PROJECTS, has no live model dependency, and can
inspect state.json before/after each approve to assert behavior precisely.

Architecture used as the fixture is a diamond:
       file-reader
        |
        v
    csv-parser
        |
        v
    markdown-formatter
        |
        v
    file-writer
A linear chain — same shape as the live csvmd from Session 2. The cascade is
straightforward to reason about.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.approve import approve_project  # noqa: E402
from oc2.state import read_state, write_state  # noqa: E402


# Minimal valid 4-subsystem fixture. NB: dependencies form a linear chain so
# editing csv-parser cascades to markdown-formatter + file-writer.
FIXTURE_MD = """# Architecture: csvmd

## Overview
Read a CSV and emit a markdown table.

## Subsystems

### file-reader
**Purpose:** Read raw CSV bytes from disk.
**Inputs:**
- CSV path
**Outputs:**
- raw CSV string
**Depends on:** none
**Owns state:** stateless
**Failure modes:**
- file missing

### csv-parser
**Purpose:** Parse the raw CSV into structured rows.
**Inputs:**
- raw CSV string from file-reader
**Outputs:**
- list of row lists
**Depends on:** file-reader
**Owns state:** stateless
**Failure modes:**
- malformed CSV

### markdown-formatter
**Purpose:** Render rows as a markdown table.
**Inputs:**
- list of row lists from csv-parser
**Outputs:**
- markdown table string
**Depends on:** csv-parser
**Owns state:** stateless
**Failure modes:**
- empty input

### file-writer
**Purpose:** Write the markdown string to disk.
**Inputs:**
- markdown string from markdown-formatter
- destination path
**Outputs:**
- file on disk
**Depends on:** markdown-formatter
**Owns state:** writes the destination file
**Failure modes:**
- destination unwritable

## Data flow
Linear: file-reader -> csv-parser -> markdown-formatter -> file-writer.

## Cross-cutting failure modes
A missing input file aborts the run before any write.

## Integration points
Row lists are list[list[str]]; the markdown table is a single string.
"""

# A clearly-broken architecture (cycle) used for the state-untouched test.
CYCLE_MD = """# Architecture: cyc

## Overview
ov

## Subsystems

### alpha
**Purpose:** p
**Inputs:**
- i
**Outputs:**
- o
**Depends on:** beta
**Owns state:** stateless
**Failure modes:**
- f

### beta
**Purpose:** p
**Inputs:**
- i
**Outputs:**
- o
**Depends on:** alpha
**Owns state:** stateless
**Failure modes:**
- f

## Data flow
df

## Cross-cutting failure modes
cc

## Integration points
ip
"""


def _silent_approve(project_dir, name):
    """Run approve_project capturing stdout. Returns (exit_code, captured)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = approve_project(project_dir, name)
    return rc, buf.getvalue()


def _mark_done(state: dict, name: str, run_id: str = "run_123") -> None:
    """Simulate a successful build for one subsystem in the state map."""
    s = state["subsystems"][name]
    s["status"] = "done"
    s["ocb_run_id"] = run_id
    s["built_at"] = "2026-05-28T02:00:00Z"
    s["error"] = None


class CascadeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oc2_approve_test_"))
        self.project_dir = self.tmp / "csvmd"
        self.project_dir.mkdir()
        (self.project_dir / "architecture.md").write_text(FIXTURE_MD, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----
    def approve(self):
        return _silent_approve(self.project_dir, "csvmd")

    def state(self) -> dict:
        return read_state(self.project_dir)

    def edit_arch(self, replacements: list[tuple[str, str]]):
        path = self.project_dir / "architecture.md"
        md = path.read_text(encoding="utf-8")
        for old, new in replacements:
            assert old in md, f"fixture substring missing: {old!r}"
            md = md.replace(old, new, 1)
        path.write_text(md, encoding="utf-8")


class TestFirstApproval(CascadeTestBase):
    def test_first_approval_all_added(self):
        rc, out = self.approve()
        self.assertEqual(rc, 0)
        s = self.state()
        self.assertEqual(set(s["subsystems"]),
                         {"file-reader", "csv-parser", "markdown-formatter", "file-writer"})
        for entry in s["subsystems"].values():
            self.assertEqual(entry["status"], "pending")
            self.assertIsNone(entry["ocb_run_id"])
            self.assertIsNone(entry["built_at"])
        self.assertIn("First approval", out)
        self.assertTrue(s["approval"]["approved"])
        # History records the diff counts on the approve event.
        hist = [h for h in s["history"] if h["event"] == "approve"][-1]
        self.assertEqual(hist["diff"], {
            "surviving_unchanged": 0, "modified": 0,
            "added": 4, "removed": 0, "cascade_size": 4,
        })


class TestReapproveNoChange(CascadeTestBase):
    def test_zero_diff_preserves_done_statuses(self):
        self.approve()
        s = self.state()
        for n in ("file-reader", "csv-parser", "markdown-formatter", "file-writer"):
            _mark_done(s, n, run_id=f"run_{n}")
        write_state(self.project_dir, s)

        rc, out = self.approve()
        self.assertEqual(rc, 0)
        self.assertIn("No spec changes since last approval", out)

        after = self.state()
        for n in ("file-reader", "csv-parser", "markdown-formatter", "file-writer"):
            entry = after["subsystems"][n]
            self.assertEqual(entry["status"], "done", msg=n)
            self.assertEqual(entry["ocb_run_id"], f"run_{n}", msg=n)


class TestUpstreamEditCascades(CascadeTestBase):
    def test_editing_csv_parser_cascades_to_downstream(self):
        # First approve + mark every subsystem 'done' to simulate a full build.
        self.approve()
        s = self.state()
        for n in s["subsystems"]:
            _mark_done(s, n)
        write_state(self.project_dir, s)

        # Edit csv-parser's purpose paragraph.
        self.edit_arch([
            ("**Purpose:** Parse the raw CSV into structured rows.",
             "**Purpose:** Parse the raw CSV into structured typed rows with header detection.")
        ])
        rc, out = self.approve()
        self.assertEqual(rc, 0)

        after = self.state()
        # file-reader is upstream of the edit and must NOT be touched.
        self.assertEqual(after["subsystems"]["file-reader"]["status"], "done")
        self.assertIsNotNone(after["subsystems"]["file-reader"]["ocb_run_id"])
        # csv-parser is the directly-modified subsystem.
        self.assertEqual(after["subsystems"]["csv-parser"]["status"], "pending")
        self.assertIsNone(after["subsystems"]["csv-parser"]["ocb_run_id"])
        # markdown-formatter + file-writer are transitive downstream and must
        # be reset to pending by the cascade.
        for n in ("markdown-formatter", "file-writer"):
            self.assertEqual(after["subsystems"][n]["status"], "pending", msg=n)
            self.assertIsNone(after["subsystems"][n]["ocb_run_id"], msg=n)

        # Report must surface the cascade.
        self.assertIn("Modified", out)
        self.assertIn("csv-parser", out)
        self.assertIn("Cascade also marks pending", out)
        self.assertIn("markdown-formatter", out)
        self.assertIn("file-writer", out)
        # file-reader should appear in the Unchanged line.
        self.assertIn("Unchanged", out)
        self.assertIn("file-reader", out)


class TestRemovalMovesSourceDir(CascadeTestBase):
    def test_removed_subsystem_source_moves_to_removed_dir(self):
        self.approve()
        # Create a fake source dir for file-writer so removal has something to move.
        src = self.project_dir / "subsystems" / "file-writer"
        src.mkdir(parents=True)
        (src / "file_writer.py").write_text("# generated\n", encoding="utf-8")

        # Edit architecture.md to delete the file-writer subsystem block AND
        # remove the markdown-formatter -> file-writer dependency chain. The
        # last reference must go for the validator to accept the doc.
        path = self.project_dir / "architecture.md"
        md = path.read_text(encoding="utf-8")
        # Drop the file-writer block (### file-writer ... up to the next ## section).
        start = md.index("### file-writer")
        end = md.index("## Data flow")
        md = md[:start] + md[end:]
        path.write_text(md, encoding="utf-8")

        rc, out = self.approve()
        self.assertEqual(rc, 0)
        after = self.state()
        self.assertNotIn("file-writer", after["subsystems"])
        # Source dir must have been moved into .removed/.
        removed_dir = self.project_dir / "subsystems" / ".removed"
        self.assertTrue(removed_dir.is_dir())
        moved = list(removed_dir.iterdir())
        self.assertEqual(len(moved), 1)
        self.assertTrue(moved[0].name.startswith("file-writer-"))
        self.assertTrue((moved[0] / "file_writer.py").is_file())
        self.assertIn("Removed", out)
        self.assertIn("file-writer", out)


class TestRemovalWithoutSourceDir(CascadeTestBase):
    def test_remove_without_existing_source_is_a_no_op_filesystem_side(self):
        self.approve()
        # No source dir was ever created for file-writer.
        path = self.project_dir / "architecture.md"
        md = path.read_text(encoding="utf-8")
        start = md.index("### file-writer")
        end = md.index("## Data flow")
        md = md[:start] + md[end:]
        path.write_text(md, encoding="utf-8")

        rc, out = self.approve()
        self.assertEqual(rc, 0)
        after = self.state()
        self.assertNotIn("file-writer", after["subsystems"])
        # No .removed/ dir should have been created.
        self.assertFalse((self.project_dir / "subsystems" / ".removed").exists())
        self.assertIn("file-writer (no source dir)", out)


class TestFailedStatusPreservedAcrossUnchangedApprove(CascadeTestBase):
    def test_failed_kept_when_spec_unchanged_and_not_in_cascade(self):
        self.approve()
        s = self.state()
        # file-reader has nothing upstream of it that could cascade; mark it
        # failed and re-approve with no spec edits.
        s["subsystems"]["file-reader"]["status"] = "failed"
        s["subsystems"]["file-reader"]["error"] = "ocb gate X failed"
        write_state(self.project_dir, s)

        rc, _ = self.approve()
        self.assertEqual(rc, 0)
        after = self.state()
        self.assertEqual(after["subsystems"]["file-reader"]["status"], "failed")
        self.assertEqual(after["subsystems"]["file-reader"]["error"], "ocb gate X failed")

    def test_failed_reset_when_spec_changes(self):
        self.approve()
        s = self.state()
        s["subsystems"]["file-reader"]["status"] = "failed"
        s["subsystems"]["file-reader"]["error"] = "ocb gate X failed"
        write_state(self.project_dir, s)

        self.edit_arch([
            ("**Purpose:** Read raw CSV bytes from disk.",
             "**Purpose:** Read raw CSV bytes from disk and validate the encoding.")
        ])
        rc, _ = self.approve()
        self.assertEqual(rc, 0)
        after = self.state()
        self.assertEqual(after["subsystems"]["file-reader"]["status"], "pending")
        self.assertIsNone(after["subsystems"]["file-reader"]["error"])


class TestValidationFailureLeavesStateUntouched(CascadeTestBase):
    def test_cycle_does_not_modify_state(self):
        self.approve()
        before = (self.project_dir / "state.json").read_bytes()
        (self.project_dir / "architecture.md").write_text(CYCLE_MD, encoding="utf-8")
        rc, out = self.approve()
        self.assertEqual(rc, 1)
        self.assertIn("NOT APPROVED", out)
        self.assertIn("State unchanged", out)
        after = (self.project_dir / "state.json").read_bytes()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
