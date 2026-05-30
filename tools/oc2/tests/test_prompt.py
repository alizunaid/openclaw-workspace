"""Unit tests for oc2.prompt.build_task_prompt — pure, deterministic."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.architecture import Subsystem  # noqa: E402
from oc2.prompt import build_task_prompt  # noqa: E402


def mk_sub(name="csv-parser",
           purpose="Parse the raw CSV string into structured rows.",
           inputs=("raw CSV string from file-reader",),
           outputs=("list of rows, each row is a list of cells",),
           depends_on=("file-reader",),
           owns_state="stateless",
           failure_modes=("malformed CSV (mismatched columns)",)) -> Subsystem:
    return Subsystem(
        name=name, purpose=purpose,
        inputs=list(inputs), outputs=list(outputs),
        depends_on=list(depends_on),
        owns_state=owns_state, failure_modes=list(failure_modes),
    )


class TestBuildTaskPrompt(unittest.TestCase):
    def test_includes_name_and_purpose_verbatim(self):
        s = mk_sub()
        out = build_task_prompt(s, {"file-reader": "subsystems/file-reader/"},
                                integration_points="rows are list[list[str]]",
                                subsystem_dir="subsystems/csv-parser/")
        self.assertIn("Build subsystem `csv-parser`", out)
        self.assertIn("Parse the raw CSV string into structured rows.", out)

    def test_inputs_and_outputs_are_bulleted(self):
        s = mk_sub(inputs=("a", "b"), outputs=("x", "y"))
        out = build_task_prompt(s, {}, "ip", "subsystems/x/")
        # Inputs block (bulleted).
        self.assertIn("Inputs:\n  - a\n  - b", out)
        # Outputs block (bulleted).
        self.assertIn("Outputs:\n  - x\n  - y", out)

    def test_owns_state_line_present(self):
        s = mk_sub(owns_state="reads data/foo.json")
        out = build_task_prompt(s, {}, "ip", "subsystems/x/")
        self.assertIn("State this subsystem owns: reads data/foo.json", out)

    def test_empty_owns_state_defaults_to_stateless(self):
        s = mk_sub(owns_state="")
        out = build_task_prompt(s, {}, "ip", "subsystems/x/")
        self.assertIn("State this subsystem owns: stateless", out)

    def test_dep_paths_block_sorted_and_rendered(self):
        s = mk_sub(depends_on=("alpha", "beta"))
        out = build_task_prompt(s, {"beta": "subsystems/beta/",
                                    "alpha": "subsystems/alpha/"},
                                "ip", "subsystems/x/")
        # Sorted by name — alpha must come before beta in the output.
        i_alpha = out.index("`alpha`")
        i_beta = out.index("`beta`")
        self.assertLess(i_alpha, i_beta)
        self.assertIn("source at `subsystems/alpha/`", out)
        self.assertIn("source at `subsystems/beta/`", out)

    def test_empty_deps_block_is_explicit(self):
        s = mk_sub(depends_on=())
        out = build_task_prompt(s, {}, "ip", "subsystems/x/")
        self.assertIn("no upstream dependencies", out)
        # And NO stray "source at" lines.
        self.assertNotIn("source at `", out)

    def test_integration_points_included_verbatim(self):
        marker = "Rows are list[list[str]]; markdown is one string."
        s = mk_sub()
        out = build_task_prompt(s, {"file-reader": "subsystems/file-reader/"},
                                integration_points=marker,
                                subsystem_dir="subsystems/csv-parser/")
        self.assertIn(marker, out)

    def test_failure_modes_bulleted(self):
        s = mk_sub(failure_modes=("file missing", "permission denied"))
        out = build_task_prompt(s, {}, "ip", "subsystems/x/")
        self.assertIn("Failure modes this subsystem must handle", out)
        self.assertIn("- file missing", out)
        self.assertIn("- permission denied", out)

    def test_output_dir_instruction_present(self):
        s = mk_sub()
        out = build_task_prompt(s, {}, "ip", "subsystems/csv-parser/")
        self.assertIn("Output to subsystems/csv-parser/", out)

    def test_no_arg_entry_self_check_instruction_present(self):
        """Bridge for ocb's entry_execution gate: the no-args path must be a
        no-op self-check that exits 0, not a usage error. See S5-prep finding."""
        s = mk_sub()
        out = build_task_prompt(s, {}, "ip", "subsystems/x/")
        self.assertIn("must exit 0 when invoked with no arguments", out)
        self.assertIn("no-op self-check", out)
        self.assertIn("NOT a usage error", out)

    def test_deterministic_across_calls(self):
        s = mk_sub()
        a = build_task_prompt(s, {"file-reader": "subsystems/file-reader/"},
                              "ip", "subsystems/x/")
        b = build_task_prompt(s, {"file-reader": "subsystems/file-reader/"},
                              "ip", "subsystems/x/")
        self.assertEqual(a, b)

    def test_dict_iteration_order_irrelevant(self):
        """The dep block is sorted, so any input ordering yields the same result."""
        s = mk_sub(depends_on=("a", "b", "c"))
        order1 = {"a": "p_a", "b": "p_b", "c": "p_c"}
        order2 = {"c": "p_c", "a": "p_a", "b": "p_b"}
        self.assertEqual(
            build_task_prompt(s, order1, "ip", "subsystems/x/"),
            build_task_prompt(s, order2, "ip", "subsystems/x/"),
        )


if __name__ == "__main__":
    unittest.main()
