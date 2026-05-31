"""S10 — project-context suppression (the Nexadose-leak fix).

Tier 2 builds + designs must NOT inherit the operator's default (nexadose)
project context. Both call sites scope the slug to the Tier 2 project's OWN
name, so oc_project finds no projects/<name>.md and returns an empty context:
  - build: OPENCLAW_PROJECT set in the ocb subprocess env (_ocb_subprocess_env)
  - design: _load_project_context(name) resolves that slug explicitly

These tests import oc_project (Tier 1) read-only to encode the proof as a
regression. They depend on projects/nexadose.md existing (the operator's real
project) and projects/numstat.md NOT existing (a Tier 2 name with no context
file) — both true in this repo.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.build import _ocb_subprocess_env  # noqa: E402
from oc2.design import _build_design_system_prompt, _load_project_context  # noqa: E402

# A Tier 2 name with no projects/<slug>.md → load_project returns empty context.
FRESH_SLUG = "numstat"
NEXADOSE_TOKENS = ["nexadose", "WORK_ITEMS", "work_item", "canonical_key"]


class TestBuildEnvLever(unittest.TestCase):
    def test_sets_openclaw_project_to_project_name(self):
        env = _ocb_subprocess_env(Path("/abs/tier2_projects/numstat"))
        self.assertEqual(env["OPENCLAW_PROJECT"], "numstat")

    def test_uses_dir_basename(self):
        env = _ocb_subprocess_env(Path("tier2_projects/csvmd"))
        self.assertEqual(env["OPENCLAW_PROJECT"], "csvmd")

    def test_copies_parent_env(self):
        os.environ["OC2_TEST_SENTINEL"] = "keep-me"
        try:
            env = _ocb_subprocess_env(Path("tier2_projects/numstat"))
            self.assertEqual(env.get("OC2_TEST_SENTINEL"), "keep-me")
        finally:
            del os.environ["OC2_TEST_SENTINEL"]

    def test_does_not_mutate_parent_env(self):
        before = dict(os.environ)
        _ocb_subprocess_env(Path("tier2_projects/numstat"))
        self.assertEqual(dict(os.environ), before)

    def test_lever_yields_nexadose_free_context(self):
        """The proof, as a regression: ocb loading under the lever's slug gets
        an empty, nexadose-free context."""
        from oc_project import load_project, resolve_slug
        env = _ocb_subprocess_env(Path("tier2_projects/numstat"))
        info = load_project(resolve_slug(env["OPENCLAW_PROJECT"]))
        self.assertEqual(info["raw_context"], "")
        blob = (info["raw_context"] + " " + info["project_name"]).lower()
        for tok in NEXADOSE_TOKENS:
            self.assertNotIn(tok.lower(), blob)


class TestDesignSuppression(unittest.TestCase):
    def test_fresh_slug_yields_empty_context(self):
        ctx = _load_project_context(FRESH_SLUG)
        self.assertEqual(ctx["raw_context"], "")
        self.assertEqual(ctx["project_name"], FRESH_SLUG)

    def test_design_system_prompt_has_no_nexadose(self):
        ctx = _load_project_context(FRESH_SLUG)
        prompt = _build_design_system_prompt(FRESH_SLUG, ctx)
        low = prompt.lower()
        for tok in NEXADOSE_TOKENS + ["nexadose-rx"]:
            self.assertNotIn(tok.lower(), low)
        # still names the project correctly (from the Tier 2 name, not nexadose).
        self.assertIn("Project: numstat", prompt)


class TestLeverChangesBehaviour(unittest.TestCase):
    """Contrast the un-scoped default (leaks nexadose) against the scoped slug
    (empty) — this is the bug being fixed."""

    def test_default_leaks_but_scoped_is_clean(self):
        from oc_project import load_project, resolve_slug
        saved = os.environ.pop("OPENCLAW_PROJECT", None)
        try:
            default = load_project(resolve_slug(None))   # falls through to nexadose
            scoped = load_project(resolve_slug("numstat"))
        finally:
            if saved is not None:
                os.environ["OPENCLAW_PROJECT"] = saved
        # The un-scoped default DOES inherit nexadose (the leak we fixed)...
        self.assertNotEqual(default["raw_context"], "",
                            msg="expected the un-scoped default to load nexadose context")
        self.assertIn("nexadose", default["project_slug"].lower())
        # ...while the scoped slug yields nothing.
        self.assertEqual(scoped["raw_context"], "")


if __name__ == "__main__":
    unittest.main()
