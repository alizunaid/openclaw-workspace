"""Unit + integration tests for the `oc2 smoke` integration runner.

These never invoke ocb or the model. They stand up a SYNTHETIC built project in
a tmpdir — hand-written subsystem modules that mirror the real generated shape
(a `read_csv_file`, a re-exported `parse_csv`, a formatter, a writer) — and
drive `run_smoke` against it. That isolates the runner's own logic (topo import
order, isolated import, entry discovery, the chain, the on-disk assertion,
report assembly) from LLM variance.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from oc2 import smoke  # noqa: E402
from oc2.build import build_report_markdown  # noqa: E402
from oc2.smoke import (  # noqa: E402
    EXPECTED_CELLS,
    SmokeResult,
    _import_entry,
    _name_hints,
    _resolve_entry_file,
    discover_entry,
    run_smoke,
)

TOPO = ["file-reader", "csv-parser", "markdown-formatter", "file-writer"]

# --- synthetic subsystem sources (mirror the real generated contract) -------

SRC_FILE_READER = '''\
import sys

def read_csv_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    print("file_reader.py is functioning correctly.")
    sys.exit(0)
'''

# csv-parser splits the transform into utils.py and re-exports it — exactly the
# shape S6/S7 observed. The smoke runner must find parse_csv via the namespace.
SRC_CSV_UTILS = '''\
import csv

def parse_csv(raw):
    return [row for row in csv.reader(raw.splitlines())]
'''

SRC_CSV_PARSER = '''\
import sys
from utils import parse_csv

def main():
    if len(sys.argv) == 1:
        print("CSV parser ready and functional.")
        sys.exit(0)

if __name__ == "__main__":
    main()
'''

SRC_MD_FORMATTER = '''\
import sys

def format_markdown(rows):
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |",
             "| " + " | ".join("---" for _ in rows[0]) + " |"]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\\n".join(lines) + "\\n"

if __name__ == "__main__":
    print("markdown_formatter.py ready.")
    sys.exit(0)
'''

# file-writer ALSO ships a utils.py (with a different symbol) to prove import
# isolation: if csv-parser's utils leaked into the module cache, file-writer's
# `from utils import slugify` would get the wrong module and fail.
SRC_FW_UTILS = '''\
def slugify(s):
    return s.strip()
'''

SRC_FILE_WRITER = '''\
import sys
from utils import slugify

def write_markdown(content, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(slugify(content) if False else content)
    return out_path

if __name__ == "__main__":
    print("file_writer.py ready.")
    sys.exit(0)
'''


def _write_subsystem(root: Path, name: str, files: dict):
    d = root / "subsystems" / name
    d.mkdir(parents=True, exist_ok=True)
    for fn, src in files.items():
        (d / fn).write_text(src, encoding="utf-8")
    return d


def make_built_project(root: Path, *, formatter=SRC_MD_FORMATTER,
                       writer=SRC_FILE_WRITER):
    """Lay down a synthetic all-done csvmd project under root/."""
    _write_subsystem(root, "file-reader", {"file_reader.py": SRC_FILE_READER})
    _write_subsystem(root, "csv-parser",
                     {"csv_parser.py": SRC_CSV_PARSER, "utils.py": SRC_CSV_UTILS})
    _write_subsystem(root, "markdown-formatter",
                     {"markdown_formatter.py": formatter})
    _write_subsystem(root, "file-writer",
                     {"file_writer.py": writer, "utils.py": SRC_FW_UTILS})
    return root


class SmokeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oc2_smoke_test_"))
        self._mods_before = set(sys.modules)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        # Smoke must not leak subsystem modules into the importer's cache.
        leaked = set(sys.modules) - self._mods_before
        self.assertFalse(
            leaked & {"file_reader", "csv_parser", "markdown_formatter",
                      "file_writer", "utils"},
            msg=f"leaked subsystem modules: {leaked}",
        )


# --- pure helper tests ------------------------------------------------------

class TestNameHints(unittest.TestCase):
    def test_reader_stem(self):
        self.assertIn("read", _name_hints("file-reader"))

    def test_parser_stem_is_substring_of_parse(self):
        # 'pars' is a substring of 'parse_csv'
        self.assertTrue(any(h in "parse_csv" for h in _name_hints("csv-parser")))

    def test_formatter_stem(self):
        self.assertIn("format", _name_hints("markdown-formatter"))

    def test_writer_stem_is_substring_of_write(self):
        self.assertTrue(any(h in "write_markdown" for h in _name_hints("file-writer")))


class TestDiscoverEntry(unittest.TestCase):
    def _mod(self, **fns):
        import types
        m = types.ModuleType("fake")
        for k, v in fns.items():
            setattr(m, k, v)
        return m

    def test_picks_single_non_main_callable(self):
        m = self._mod(main=lambda: None, read_csv_file=lambda p: "x")
        name, fn = discover_entry(m, "file-reader", want_arity=1)
        self.assertEqual(name, "read_csv_file")

    def test_name_hint_disambiguates_over_distractor(self):
        m = self._mod(helper=lambda a, b: None, write_markdown=lambda c, p: None)
        name, _ = discover_entry(m, "file-writer", want_arity=2)
        self.assertEqual(name, "write_markdown")

    def test_no_candidate_raises_lookup(self):
        m = self._mod(main=lambda: None)
        with self.assertRaises(LookupError):
            discover_entry(m, "file-writer", want_arity=2)


class TestImportIsolation(SmokeTestBase):
    def test_entry_module_imports_without_firing_main(self):
        # If the __main__ guard fired on import it would sys.exit; reaching the
        # assertion proves it did not.
        d = _write_subsystem(self.tmp, "file-reader",
                             {"file_reader.py": SRC_FILE_READER})
        mod = _import_entry(d, "file-reader")
        self.assertTrue(hasattr(mod, "read_csv_file"))

    def test_prefers_canonical_entry_module(self):
        # When the canonical <name>.py exists, it wins even over a hint-y slug.
        d = self.tmp / "subsystems" / "file-writer"
        d.mkdir(parents=True)
        (d / "file_writer.py").write_text("def write_x(c, p): pass\n", encoding="utf-8")
        (d / "build_subsystem_file_writer_slug.py").write_text("y = 1\n", encoding="utf-8")
        self.assertEqual(_resolve_entry_file(d, "file-writer").name, "file_writer.py")

    def test_discovers_slugged_entry_module_when_canonical_absent(self):
        # The S14 degrade shape: ocb slugged the filename from the prompt header,
        # so the canonical file_writer.py does NOT exist. Smoke must DISCOVER the
        # slugged module rather than hard-fail.
        d = self.tmp / "subsystems" / "file-writer"
        d.mkdir(parents=True)
        (d / "build_subsystem_file_writer_purpose_writ.py").write_text(
            "def write_markdown_report(c, p):\n    open(p, 'w').write(c)\n",
            encoding="utf-8")
        resolved = _resolve_entry_file(d, "file-writer")
        self.assertEqual(resolved.name, "build_subsystem_file_writer_purpose_writ.py")
        mod = _import_entry(d, "file-writer")
        self.assertTrue(hasattr(mod, "write_markdown_report"))

    def test_discovery_skips_test_and_helper_modules(self):
        # The entry must be the non-helper module even when tests/utils sit beside it.
        d = self.tmp / "subsystems" / "file-writer"
        d.mkdir(parents=True)
        (d / "writer_main.py").write_text("def write_md(c, p): pass\n", encoding="utf-8")
        (d / "test_writer.py").write_text("def test_x(): pass\n", encoding="utf-8")
        (d / "writer_utils.py").write_text("def helper(): pass\n", encoding="utf-8")
        self.assertEqual(_resolve_entry_file(d, "file-writer").name, "writer_main.py")

    def test_no_py_files_at_all_raises(self):
        # The genuine "not built" case still raises (only when there is NOTHING).
        d = self.tmp / "subsystems" / "file-reader"
        d.mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            _import_entry(d, "file-reader")


# --- end-to-end chain -------------------------------------------------------

class TestRunSmokeHappyPath(SmokeTestBase):
    def test_chain_composes_and_cells_survive(self):
        make_built_project(self.tmp)
        result = run_smoke(self.tmp, TOPO)
        self.assertTrue(result.ok, msg=f"{result.summary} / {result.findings}")
        # Entries discovered in topo order.
        self.assertEqual(list(result.entries.keys()), TOPO)
        self.assertEqual(result.entries["file-reader"], "read_csv_file")
        self.assertEqual(result.entries["csv-parser"], "parse_csv")
        self.assertEqual(result.entries["markdown-formatter"], "format_markdown")
        self.assertEqual(result.entries["file-writer"], "write_markdown")
        # The quoted-comma cells survived the whole pipeline.
        self.assertIn("Bob, Jr.", result.markdown)
        self.assertIn("Los Angeles, CA", result.markdown)
        for cell in EXPECTED_CELLS:
            self.assertIn(cell, result.markdown)

    def test_tmp_output_is_cleaned_up(self):
        make_built_project(self.tmp)
        result = run_smoke(self.tmp, TOPO)
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.out_path)
        self.assertFalse(Path(result.out_path).exists(),
                         msg="smoke tmp output should be removed")


class TestRunSmokeFindings(SmokeTestBase):
    def test_missing_subsystem_dir_is_a_finding(self):
        make_built_project(self.tmp)
        import shutil
        shutil.rmtree(self.tmp / "subsystems" / "file-writer")
        result = run_smoke(self.tmp, TOPO)
        self.assertFalse(result.ok)
        self.assertTrue(any("file-writer" in f for f in result.findings))

    def test_import_error_is_a_finding_not_a_crash(self):
        make_built_project(self.tmp)
        (self.tmp / "subsystems" / "markdown-formatter" /
         "markdown_formatter.py").write_text(
            "import sys\nthis is not valid python\n", encoding="utf-8")
        result = run_smoke(self.tmp, TOPO)
        self.assertFalse(result.ok)
        self.assertTrue(any("markdown-formatter" in f and "import" in f.lower()
                            for f in result.findings))

    def test_no_entry_function_is_a_finding(self):
        # A formatter module that only has `main` — nothing to compose.
        make_built_project(
            self.tmp,
            formatter='import sys\ndef main():\n    sys.exit(0)\n')
        result = run_smoke(self.tmp, TOPO)
        self.assertFalse(result.ok)
        self.assertTrue(any("markdown-formatter" in f for f in result.findings))

    def test_quoting_break_is_caught_by_cell_assertion(self):
        # A formatter that naively splits on every comma would shred the quoted
        # cells. Simulate by collapsing rows through str(),','.join — the quoted
        # cell's internal comma becomes a column separator and the cell vanishes.
        broken = (
            "import sys\n"
            "def format_markdown(rows):\n"
            "    out = []\n"
            "    for row in rows:\n"
            "        flat = ','.join(row).split(',')\n"   # re-split: destroys quoting
            "        out.append('| ' + ' | '.join(flat) + ' |')\n"
            "    return '\\n'.join(out) + '\\n'\n"
            "if __name__ == '__main__':\n"
            "    sys.exit(0)\n"
        )
        make_built_project(self.tmp, formatter=broken)
        result = run_smoke(self.tmp, TOPO)
        self.assertFalse(result.ok)
        self.assertTrue(any("Bob, Jr." in f or "missing" in f.lower()
                            for f in result.findings))

    def test_writer_that_writes_nothing_is_a_finding(self):
        make_built_project(
            self.tmp,
            writer=("import sys\n"
                    "def write_markdown(content, out_path):\n"
                    "    return out_path\n"   # never writes the file
                    "if __name__ == '__main__':\n"
                    "    sys.exit(0)\n"))
        result = run_smoke(self.tmp, TOPO)
        self.assertFalse(result.ok)
        self.assertTrue(any("no file" in f.lower() or "did not create" in f.lower()
                            for f in result.findings))


# --- report assembly --------------------------------------------------------

class TestBuildReport(unittest.TestCase):
    def _state(self):
        return {"subsystems": {
            "file-reader": {"status": "done", "ocb_run_id": "run_1",
                            "built_at": "2026-05-30T05:08:35Z",
                            "generated_files": ["file_reader.py", "main.py"]},
            "csv-parser": {"status": "done", "ocb_run_id": "run_2",
                           "built_at": "2026-05-30T23:43:19Z",
                           "generated_files": ["csv_parser.py", "utils.py"]},
            "markdown-formatter": {"status": "done", "ocb_run_id": "run_3",
                                   "built_at": "2026-05-31T01:00:00Z",
                                   "generated_files": ["markdown_formatter.py"]},
            "file-writer": {"status": "done", "ocb_run_id": "run_4",
                            "built_at": "2026-05-31T01:05:00Z",
                            "generated_files": ["file_writer.py"]},
        }}

    def test_report_lists_every_subsystem_with_run_id(self):
        md = build_report_markdown(
            self._state(), TOPO, "csvmd",
            "chain composes", True,
            smoke_entries={"file-reader": "read_csv_file"},
            elapsed_seconds=42.0)
        for n in TOPO:
            self.assertIn(f"`{n}`", md)
        self.assertIn("run_4", md)
        self.assertIn("**Verdict: PASS**", md)
        self.assertIn("42.0s", md)

    def test_report_records_failure_findings(self):
        md = build_report_markdown(
            self._state(), TOPO, "csvmd",
            "output missing 2 cell(s)", False,
            smoke_findings=["on-disk markdown is missing expected cell(s): "
                            "['Bob, Jr.']"])
        self.assertIn("**Verdict: FAIL**", md)
        self.assertIn("Bob, Jr.", md)
        self.assertIn("Findings:", md)


if __name__ == "__main__":
    unittest.main()
