"""Unit tests for the architecture markdown parser (oc2.architecture.parse)."""
import os
import sys
import unittest

# Make `tools/` importable so `import oc2.*` works under any test runner.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.architecture import ParseError, parse  # noqa: E402

# A full, valid 4-subsystem architecture exercising the whole schema.
VALID_MD = """# Architecture: Daily Snapshot Reporter

## Overview
A tool that reads the work-items register each morning and produces a daily
brief markdown combining the top-10 and a per-category breakdown.

## Subsystems

### register-reader
**Purpose:** Load the work-items CSV and expose typed rows to the rest of the
system.
**Inputs:**
- WORK_ITEMS_REGISTER csv path
- optional column overrides
**Outputs:**
- list of typed row dicts
**Depends on:** none
**Owns state:** stateless
**Failure modes:**
- file missing
- malformed CSV row

### top10-runner
**Purpose:** Invoke the existing top-10 selector and capture its output.
**Inputs:**
- typed rows from register-reader
**Outputs:**
- ordered top-10 list
**Depends on:** register-reader
**Owns state:** stateless
**Failure modes:**
- selector raises

### breakdown-generator
**Purpose:** Compute per-category counts and simple trend deltas.
**Inputs:**
- typed rows from register-reader
**Outputs:**
- per-category count table
**Depends on:** register-reader
**Owns state:** reads yesterday's snapshot from data/breakdown_prev.json
**Failure modes:**
- empty register
- category field absent

### output-formatter
**Purpose:** Combine the top-10 and breakdown into one daily-brief markdown.
**Inputs:**
- top-10 list
- per-category table
**Outputs:**
- daily_brief.md
**Depends on:** top10-runner, breakdown-generator
**Owns state:** writes daily_brief.md
**Failure modes:**
- write permission denied

## Data flow
register-reader loads rows; top10-runner and breakdown-generator both consume
them; output-formatter merges the two results into the final brief.

## Cross-cutting failure modes
If the register file is missing, the whole run aborts with a clear message.

## Integration points
Rows are plain dicts with string keys. The top-10 list and breakdown table are
passed in-memory; only output-formatter touches the filesystem for output.
"""


class TestParseValidFull(unittest.TestCase):
    def setUp(self):
        self.arch = parse(VALID_MD)

    def test_title(self):
        self.assertEqual(self.arch.project_name, "Daily Snapshot Reporter")

    def test_subsystem_count_and_names(self):
        names = [s.name for s in self.arch.subsystems]
        self.assertEqual(
            names,
            ["register-reader", "top10-runner", "breakdown-generator", "output-formatter"],
        )

    def test_top_level_sections_captured(self):
        self.assertIn("work-items register", self.arch.overview.lower())
        self.assertIn("register-reader loads rows", self.arch.data_flow)
        self.assertIn("register file is missing", self.arch.cross_cutting_failure_modes)
        self.assertIn("plain dicts", self.arch.integration_points)

    def test_purpose_prose(self):
        reader = self.arch.by_name()["register-reader"]
        self.assertTrue(reader.purpose.startswith("Load the work-items CSV"))

    def test_inputs_outputs_bullets(self):
        reader = self.arch.by_name()["register-reader"]
        self.assertEqual(
            reader.inputs, ["WORK_ITEMS_REGISTER csv path", "optional column overrides"]
        )
        self.assertEqual(reader.outputs, ["list of typed row dicts"])

    def test_failure_modes_bullets(self):
        reader = self.arch.by_name()["register-reader"]
        self.assertEqual(reader.failure_modes, ["file missing", "malformed CSV row"])

    def test_owns_state_prose_and_stateless(self):
        by = self.arch.by_name()
        self.assertEqual(by["register-reader"].owns_state, "stateless")
        self.assertIn("breakdown_prev.json", by["breakdown-generator"].owns_state)

    def test_depends_on_none_is_empty(self):
        self.assertEqual(self.arch.by_name()["register-reader"].depends_on, [])

    def test_depends_on_single(self):
        self.assertEqual(self.arch.by_name()["top10-runner"].depends_on, ["register-reader"])

    def test_depends_on_comma_separated(self):
        self.assertEqual(
            self.arch.by_name()["output-formatter"].depends_on,
            ["top10-runner", "breakdown-generator"],
        )

    def test_all_required_fields_present(self):
        for s in self.arch.subsystems:
            self.assertEqual(
                s.present_fields,
                {"purpose", "inputs", "outputs", "depends_on", "owns_state", "failure_modes"},
                msg=f"{s.name} missing fields",
            )


class TestParseEdgeCases(unittest.TestCase):
    def test_missing_title_raises(self):
        with self.assertRaises(ParseError):
            parse("## Overview\nno title here\n")

    def test_colon_inside_and_outside_bold(self):
        md = """# Architecture: T

## Subsystems

### alpha
**Purpose:** inside-colon style.
**Inputs**: outside-colon style input
**Outputs:** out
**Depends on:** none
**Owns state:** stateless
**Failure modes:** boom
"""
        arch = parse(md)
        alpha = arch.by_name()["alpha"]
        self.assertEqual(alpha.purpose, "inside-colon style.")
        # `**Inputs**:` (colon after bold) must still be recognized as the label.
        self.assertEqual(alpha.inputs, ["outside-colon style input"])
        self.assertIn("inputs", alpha.present_fields)

    def test_inline_list_without_bullets_becomes_single_item(self):
        md = """# Architecture: T

## Subsystems

### alpha
**Purpose:** p
**Inputs:** a single inline input
**Outputs:** a single inline output
**Depends on:** none
**Owns state:** stateless
**Failure modes:** the one failure
"""
        alpha = parse(md).by_name()["alpha"]
        self.assertEqual(alpha.inputs, ["a single inline input"])
        self.assertEqual(alpha.failure_modes, ["the one failure"])

    def test_spec_sha256_stable_and_edit_sensitive(self):
        a1 = parse(VALID_MD).by_name()["register-reader"]
        a2 = parse(VALID_MD).by_name()["register-reader"]
        self.assertEqual(a1.spec_sha256(), a2.spec_sha256())
        edited = VALID_MD.replace("file missing", "file vanished")
        a3 = parse(edited).by_name()["register-reader"]
        self.assertNotEqual(a1.spec_sha256(), a3.spec_sha256())
        # an unrelated subsystem's spec must be unaffected by that edit
        b1 = parse(VALID_MD).by_name()["top10-runner"]
        b3 = parse(edited).by_name()["top10-runner"]
        self.assertEqual(b1.spec_sha256(), b3.spec_sha256())


if __name__ == "__main__":
    unittest.main()
