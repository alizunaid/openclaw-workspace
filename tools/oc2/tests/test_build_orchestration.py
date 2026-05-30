"""Integration tests for `oc2 build` orchestration.

We drive `build_project(project_dir, name, only, ocb_runner)` directly against
a tmpdir with a FAKE ocb_runner so the suite never invokes Tier 1 or the real
model. The fake writes synthetic .py files into a fake run dir + a fake state
log so the orchestrator's discovery / copy / gate-extraction paths run end-to-
end against real on-disk artifacts.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2.approve import approve_project  # noqa: E402
from oc2.build import OcbResult, build_project  # noqa: E402
from oc2.state import read_state, write_state  # noqa: E402

# Same csvmd-shape fixture S3 used: linear chain of 4 subsystems so the
# halt-on-failure + transitive-dependent test is straightforward to reason about.
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


def _silent(fn, *a, **kw):
    """Run a function with stdout captured. Returns (return_value, captured)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rv = fn(*a, **kw)
    return rv, buf.getvalue()


def make_fake_runner(fake_root: Path, behavior: dict):
    """Return an ocb_runner that:
      - for each invocation, writes synthetic .py files into a fake run dir
        under fake_root/runs/run_<seq>/
      - writes a fake state log under fake_root/logs/oc_build_<seq>.json
      - returns OcbResult(success=...) per the `behavior` map keyed by
        subsystem name. Default behavior is success.

    `behavior[name]` may be one of:
      - 'ok' (default if absent) -> success with one synthetic file
      - 'fail_<phase>'           -> failure; the state log records that phase
                                    with status=failed so _extract_failed_gate
                                    surfaces the phase name
    """
    runs_dir = fake_root / "runs"
    logs_dir = fake_root / "logs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    # Seed the counter from any prior runner's output so a second runner in
    # the same test doesn't collide on run_<seq> names.
    counter = {"n": len(list(runs_dir.glob("run_*")))}

    def runner(prompt, project_dir, sub_name):
        counter["n"] += 1
        seq = counter["n"]
        run_id = f"run_{1700000000 + seq}"
        run_dir = runs_dir / run_id
        run_dir.mkdir()
        log_path = logs_dir / f"oc_build_{1700000000 + seq}.json"
        action = behavior.get(sub_name, "ok")

        if action == "ok":
            # One canonical generated file so _copy_generated has something
            # to find. Filename mirrors a plausible ocb output.
            (run_dir / f"{sub_name.replace('-', '_')}.py").write_text(
                f"# generated for {sub_name}\n", encoding="utf-8"
            )
            log_path.write_text(json.dumps({
                "run_id": run_id,
                "phases": {"manifest": {"status": "ok"}},
            }), encoding="utf-8")
            return OcbResult(success=True, run_id=run_id,
                             run_dir=run_dir, log_path=log_path)

        # Failure variants: action looks like 'fail_<phase>'.
        phase = action.split("_", 1)[1] if action.startswith("fail_") else "manifest"
        log_path.write_text(json.dumps({
            "run_id": run_id,
            "phases": {phase: {"status": "failed",
                               "detail": f"synthetic failure in {phase}"}},
        }), encoding="utf-8")
        # On real ocb failures the run_dir often still exists (partial work
        # was written before the gate fired). Mirror that here.
        return OcbResult(success=False, run_id=run_id,
                         run_dir=run_dir, log_path=log_path,
                         error=f"phase=`{phase}` status=failed")

    return runner


class BuildTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oc2_build_test_"))
        self.project_dir = self.tmp / "csvmd"
        self.project_dir.mkdir()
        (self.project_dir / "architecture.md").write_text(FIXTURE_MD, encoding="utf-8")
        # Approve so state.json carries the pending subsystems map.
        _silent(approve_project, self.project_dir, "csvmd")
        self.fake_root = self.tmp / "_fake_ocb"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, only=None, behavior=None):
        runner = make_fake_runner(self.fake_root, behavior or {})
        return _silent(build_project, self.project_dir, "csvmd",
                       only=only, ocb_runner=runner)


class TestHappyPath(BuildTestBase):
    def test_all_subsystems_build_in_topological_order(self):
        rc, out = self.build()
        self.assertEqual(rc, 0, msg=out)
        s = read_state(self.project_dir)
        for name in ("file-reader", "csv-parser",
                     "markdown-formatter", "file-writer"):
            entry = s["subsystems"][name]
            self.assertEqual(entry["status"], "done", msg=name)
            self.assertTrue(entry["ocb_run_id"].startswith("run_"))
            self.assertIsNotNone(entry["built_at"])
            self.assertIsNone(entry["error"])
            # Generated file copied into subsystems/<name>/
            files = list((self.project_dir / "subsystems" / name).glob("*.py"))
            self.assertEqual(len(files), 1, msg=name)
        # history: build_start, build_complete (counts)
        events = [h["event"] for h in s["history"]]
        self.assertIn("build_start", events)
        self.assertIn("build_complete", events)
        last = [h for h in s["history"] if h["event"] == "build_complete"][-1]
        self.assertEqual(last["built"], ["file-reader", "csv-parser",
                                         "markdown-formatter", "file-writer"])


class TestHaltOnFailure(BuildTestBase):
    def test_failure_at_csv_parser_halts_build(self):
        rc, out = self.build(behavior={"csv-parser": "fail_topo_gen"})
        self.assertEqual(rc, 1, msg=out)
        s = read_state(self.project_dir)
        # file-reader built before the failure.
        self.assertEqual(s["subsystems"]["file-reader"]["status"], "done")
        # csv-parser failed with the gate from the fake state log.
        cp = s["subsystems"]["csv-parser"]
        self.assertEqual(cp["status"], "failed")
        self.assertIn("topo_gen", cp["error"])
        # Dependents stay pending (no skipped/blocked status enum).
        self.assertEqual(s["subsystems"]["markdown-formatter"]["status"], "pending")
        self.assertEqual(s["subsystems"]["file-writer"]["status"], "pending")
        # User-facing message names the blocked dependents.
        self.assertIn("PAUSED", out)
        self.assertIn("markdown-formatter", out)
        self.assertIn("file-writer", out)
        # history records the pause with the failure + blocked set.
        pause = [h for h in s["history"] if h["event"] == "build_pause"][-1]
        self.assertEqual(pause["failed"], "csv-parser")
        self.assertEqual(pause["blocked"],
                         ["file-writer", "markdown-formatter"])
        self.assertEqual(pause["succeeded"], ["file-reader"])


class TestResumeIdempotent(BuildTestBase):
    def test_done_subsystems_skipped_on_resume(self):
        # First run fully succeeds.
        rc1, _ = self.build()
        self.assertEqual(rc1, 0)
        # Second run with the SAME runner that would fail every subsystem if
        # called — proves done subsystems are not re-invoked.
        bad_runner = make_fake_runner(self.fake_root,
                                      behavior={n: "fail_manifest" for n in
                                                ["file-reader", "csv-parser",
                                                 "markdown-formatter",
                                                 "file-writer"]})
        rc2, out = _silent(build_project, self.project_dir, "csvmd",
                           ocb_runner=bad_runner)
        self.assertEqual(rc2, 0)
        self.assertIn("nothing to build", out)


class TestOnlyEscapeHatch(BuildTestBase):
    def test_only_rebuilds_just_one_subsystem(self):
        # First: build everything to done.
        self.build()
        # Now --only csv-parser with deps satisfied. file-reader is done; deps OK.
        # Use a "different" run that would fail for everyone else if asked.
        bad_runner = make_fake_runner(self.fake_root, behavior={
            "file-reader": "fail_manifest",
            "markdown-formatter": "fail_manifest",
            "file-writer": "fail_manifest",
            # csv-parser succeeds
        })
        rc, out = _silent(build_project, self.project_dir, "csvmd",
                          only="csv-parser", ocb_runner=bad_runner)
        self.assertEqual(rc, 0, msg=out)
        s = read_state(self.project_dir)
        # Only csv-parser was touched: its ocb_run_id changed (a fresh run).
        self.assertEqual(s["subsystems"]["csv-parser"]["status"], "done")
        # The others remain `done` with their prior run ids untouched.
        self.assertEqual(s["subsystems"]["file-reader"]["status"], "done")
        self.assertEqual(s["subsystems"]["markdown-formatter"]["status"], "done")
        self.assertEqual(s["subsystems"]["file-writer"]["status"], "done")

    def test_only_unknown_subsystem_refused(self):
        rc, out = _silent(build_project, self.project_dir, "csvmd",
                          only="ghost",
                          ocb_runner=make_fake_runner(self.fake_root, {}))
        self.assertEqual(rc, 1)
        self.assertIn("not a subsystem", out)

    def test_only_refuses_when_deps_not_done(self):
        # No prior build; csv-parser's dep file-reader is pending.
        rc, out = _silent(build_project, self.project_dir, "csvmd",
                          only="csv-parser",
                          ocb_runner=make_fake_runner(self.fake_root, {}))
        self.assertEqual(rc, 1)
        self.assertIn("depends on", out)
        self.assertIn("file-reader", out)
        # State must be unchanged on refusal: every subsystem still pending.
        s = read_state(self.project_dir)
        for n in s["subsystems"]:
            self.assertEqual(s["subsystems"][n]["status"], "pending", msg=n)

    def test_only_resets_a_failed_subsystem_and_rebuilds_it(self):
        # Bring file-reader to done, csv-parser to failed.
        self.build(behavior={"csv-parser": "fail_manifest"})
        s = read_state(self.project_dir)
        self.assertEqual(s["subsystems"]["csv-parser"]["status"], "failed")
        # --only csv-parser with a runner that now succeeds for it.
        rc, out = _silent(build_project, self.project_dir, "csvmd",
                          only="csv-parser",
                          ocb_runner=make_fake_runner(self.fake_root, {}))
        self.assertEqual(rc, 0, msg=out)
        after = read_state(self.project_dir)
        self.assertEqual(after["subsystems"]["csv-parser"]["status"], "done")
        self.assertIsNone(after["subsystems"]["csv-parser"]["error"])


class TestInProgressRecovery(BuildTestBase):
    def test_pre_existing_in_progress_becomes_failed_or_killed_and_blocks(self):
        # Simulate a crashed prior session: one subsystem stuck in_progress.
        s = read_state(self.project_dir)
        s["subsystems"]["file-reader"]["status"] = "in_progress"
        s["subsystems"]["file-reader"]["started_at"] = "2026-05-29T01:00:00Z"
        write_state(self.project_dir, s)
        rc, out = self.build()
        # Recovery converts in_progress -> failed_or_killed, then the topo
        # walker hits that subsystem and HALTS so we don't waste ocb runs on
        # downstream subsystems whose deps are missing.
        self.assertEqual(rc, 1)
        self.assertIn("failed_or_killed", out)
        self.assertIn("--only file-reader", out)  # the recommended next step
        after = read_state(self.project_dir)
        self.assertEqual(after["subsystems"]["file-reader"]["status"],
                         "failed_or_killed")
        # Downstream subsystems must NOT have been touched.
        for n in ("csv-parser", "markdown-formatter", "file-writer"):
            self.assertEqual(after["subsystems"][n]["status"], "pending", msg=n)

    def test_recovery_then_only_unblocks_build(self):
        # Same crash setup, but the user follows the printed advice and
        # retries with --only.
        s = read_state(self.project_dir)
        s["subsystems"]["file-reader"]["status"] = "in_progress"
        write_state(self.project_dir, s)
        # First call halts at the failed_or_killed (after recovery).
        self.build()
        # Now --only file-reader rebuilds it and resumes works.
        rc, out = _silent(build_project, self.project_dir, "csvmd",
                          only="file-reader",
                          ocb_runner=make_fake_runner(self.fake_root, {}))
        self.assertEqual(rc, 0, msg=out)
        after = read_state(self.project_dir)
        self.assertEqual(after["subsystems"]["file-reader"]["status"], "done")


class TestNotApprovedRefused(BuildTestBase):
    def test_unapproved_project_refused(self):
        s = read_state(self.project_dir)
        s["approval"]["approved"] = False
        write_state(self.project_dir, s)
        rc, out = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("not approved", out)


class TestPostApprovalEditRefused(BuildTestBase):
    def test_invalid_arch_after_approval_refuses_build(self):
        # Inject a cycle into the architecture WITHOUT re-approving.
        path = self.project_dir / "architecture.md"
        md = path.read_text()
        # Make file-reader depend on file-writer (creates a cycle).
        md = md.replace(
            "### file-reader\n**Purpose:** Read raw CSV bytes from disk.\n**Inputs:**\n- CSV path\n**Outputs:**\n- raw CSV string\n**Depends on:** none",
            "### file-reader\n**Purpose:** Read raw CSV bytes from disk.\n**Inputs:**\n- CSV path\n**Outputs:**\n- raw CSV string\n**Depends on:** file-writer",
        )
        path.write_text(md, encoding="utf-8")
        rc, out = self.build()
        self.assertEqual(rc, 1)
        self.assertIn("no longer validates", out)


if __name__ == "__main__":
    unittest.main()
