"""Unit tests for the structural validator (oc2.architecture.validate)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.architecture import parse, validate  # noqa: E402


def sub_block(name, *, purpose="does a thing", inputs=("in",), outputs=("out",),
              depends_on=(), owns_state="stateless", failure_modes=("boom",), omit=()):
    """Assemble one `### subsystem` markdown block. `omit` drops field labels."""
    lines = [f"### {name}"]
    if "purpose" not in omit:
        lines.append(f"**Purpose:** {purpose}")
    if "inputs" not in omit:
        lines.append("**Inputs:**")
        lines += [f"- {x}" for x in inputs]
    if "outputs" not in omit:
        lines.append("**Outputs:**")
        lines += [f"- {x}" for x in outputs]
    if "depends_on" not in omit:
        lines.append(f"**Depends on:** {', '.join(depends_on) if depends_on else 'none'}")
    if "owns_state" not in omit:
        lines.append(f"**Owns state:** {owns_state}")
    if "failure_modes" not in omit:
        lines.append("**Failure modes:**")
        lines += [f"- {x}" for x in failure_modes]
    return "\n".join(lines)


def doc(*blocks, name="Test Project"):
    return (
        f"# Architecture: {name}\n\n"
        "## Overview\nov\n\n"
        "## Subsystems\n\n"
        + "\n\n".join(blocks)
        + "\n\n## Data flow\ndf\n\n"
        "## Cross-cutting failure modes\ncc\n\n"
        "## Integration points\nip\n"
    )


def validate_md(md):
    return validate(parse(md))


# A minimal valid 2-subsystem baseline used as the starting point for mutations.
VALID = doc(
    sub_block("alpha"),
    sub_block("beta", depends_on=["alpha"]),
)


class TestValidPasses(unittest.TestCase):
    def test_ok_no_errors(self):
        res = validate_md(VALID)
        self.assertTrue(res.ok, msg=res.errors)
        self.assertEqual(res.errors, [])

    def test_topo_order_deps_before_dependents(self):
        res = validate_md(VALID)
        self.assertEqual(res.topo_order, ["alpha", "beta"])

    def test_four_subsystem_topo_with_tiebreak(self):
        md = doc(
            sub_block("register-reader"),
            sub_block("top10-runner", depends_on=["register-reader"]),
            sub_block("breakdown-generator", depends_on=["register-reader"]),
            sub_block("output-formatter", depends_on=["top10-runner", "breakdown-generator"]),
        )
        res = validate_md(md)
        self.assertTrue(res.ok, msg=res.errors)
        order = res.topo_order
        # deps strictly precede dependents
        self.assertLess(order.index("register-reader"), order.index("top10-runner"))
        self.assertLess(order.index("register-reader"), order.index("breakdown-generator"))
        self.assertLess(order.index("top10-runner"), order.index("output-formatter"))
        self.assertLess(order.index("breakdown-generator"), order.index("output-formatter"))
        # alphabetical tiebreak among the two ready-at-once subsystems
        self.assertLess(order.index("breakdown-generator"), order.index("top10-runner"))


class TestHardErrors(unittest.TestCase):
    def _assert_error_contains(self, md, needle):
        res = validate_md(md)
        self.assertFalse(res.ok)
        self.assertIsNone(res.topo_order, msg="topo_order must be None when invalid")
        self.assertTrue(
            any(needle in e for e in res.errors),
            msg=f"expected an error containing {needle!r}, got {res.errors}",
        )

    def test_cycle(self):
        md = doc(
            sub_block("alpha", depends_on=["beta"]),
            sub_block("beta", depends_on=["alpha"]),
        )
        self._assert_error_contains(md, "cycle")

    def test_self_dependency(self):
        md = doc(
            sub_block("alpha", depends_on=["alpha"]),
            sub_block("beta"),
        )
        self._assert_error_contains(md, "self-dependency")

    def test_missing_field(self):
        md = doc(
            sub_block("alpha", omit=["failure_modes"]),
            sub_block("beta"),
        )
        self._assert_error_contains(md, "missing required field 'failure_modes'")

    def test_dangling_dependency(self):
        md = doc(
            sub_block("alpha", depends_on=["ghost"]),
            sub_block("beta"),
        )
        self._assert_error_contains(md, "does not exist")

    def test_too_few_subsystems(self):
        md = doc(sub_block("alpha"))
        self._assert_error_contains(md, "too few subsystems")

    def test_too_many_subsystems(self):
        blocks = [sub_block(f"node-{i}") for i in range(11)]
        self._assert_error_contains(doc(*blocks), "too many subsystems")

    def test_reserved_name_main(self):
        md = doc(sub_block("main"), sub_block("beta"))
        self._assert_error_contains(md, "reserved name")

    def test_bad_name_not_kebab(self):
        md = doc(sub_block("Foo_Bar"), sub_block("beta"))
        self._assert_error_contains(md, "kebab-case")

    def test_empty_purpose(self):
        md = doc(sub_block("alpha", purpose=""), sub_block("beta"))
        self._assert_error_contains(md, "'purpose' is empty")


class TestSoftWarnings(unittest.TestCase):
    def test_too_many_deps_warns_but_ok(self):
        deps = [f"d{i}" for i in range(5)]
        blocks = [sub_block(d) for d in deps]
        blocks.append(sub_block("hub", depends_on=deps))
        res = validate_md(doc(*blocks))
        self.assertTrue(res.ok, msg=res.errors)
        self.assertTrue(any("under-decomposed" in w and "hub" in w for w in res.warnings))

    def test_no_inputs_no_outputs_warns_but_ok(self):
        md = doc(
            sub_block("alpha", inputs=(), outputs=()),
            sub_block("beta", depends_on=["alpha"]),
        )
        res = validate_md(md)
        self.assertTrue(res.ok, msg=res.errors)
        self.assertTrue(any("no inputs and no outputs" in w for w in res.warnings))

    def test_subsystem_named_after_project_warns(self):
        md = doc(
            sub_block("test-project"),
            sub_block("beta", depends_on=["test-project"]),
            name="Test Project",
        )
        res = validate_md(md)
        self.assertTrue(res.ok, msg=res.errors)
        self.assertTrue(any("named after the whole project" in w for w in res.warnings))

    def test_missing_optional_section_warns(self):
        md = VALID.replace("## Integration points\nip\n", "")
        res = validate_md(md)
        self.assertTrue(res.ok, msg=res.errors)
        self.assertTrue(any("Integration points" in w for w in res.warnings))


if __name__ == "__main__":
    unittest.main()
