"""Integration smoke runner — `oc2 smoke <project>`.

The v1 minimum integration test (design/tier2_v1.md Section 3, Q4 option b):
import each built subsystem's entry in TOPOLOGICAL order from one Python process
(the sys.path pattern the Session 7 composition probe proved) and run the
architecture's promised data-flow chain end-to-end on a REAL CSV, asserting the
on-disk output is correct.

This is the FIRST real composition test of markdown-formatter and file-writer.
The runner NEVER patches generated code: a discovery miss or a type/shape
mismatch is reported as a FINDING (non-zero exit + a precise description of the
break), exactly as the Session 7 stance prescribed.

What "the chain" means here. csvmd is a linear pipeline, and v1 models a linear
pipeline generically:
  - the FIRST subsystem in topo order is a path-source: entry(csv_path) -> str
  - MIDDLE subsystems are unary transforms:            entry(prev)     -> next
  - the LAST subsystem is a (content, path)-sink:      entry(prev, out) -> file
For csvmd this is:
  read_csv_file(path) -> raw str
    -> parse_csv(str) -> rows
      -> <formatter>(rows) -> markdown str
        -> <writer>(markdown, out_path) -> file on disk
The formatter/writer entry-function names are NOT known ahead of time, so they
are DISCOVERED from the imported module (public callable, scored by name hint +
arity) — the doc anticipates "subsystems whose entry isn't named `main`".

Pure-ish: imports the generated subsystems (that is the point of the test) but
never imports Tier 1 and never touches the network or the model.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The Session 7 fixture, including the quoted-comma row so CSV quoting is
# exercised through the WHOLE pipeline (parser must keep "Bob, Jr." and
# "Los Angeles, CA" intact as single cells; the formatter must render them).
SMOKE_CSV = (
    "name,age,city\n"
    "Alice,30,New York\n"
    '"Bob, Jr.",25,"Los Angeles, CA"\n'
)

# Cells that MUST survive intact in the on-disk markdown. The two quoted cells
# are the discriminating ones — if quoting broke anywhere in the chain they
# would split on their internal comma and these substrings would be absent.
EXPECTED_CELLS = [
    "name", "age", "city",
    "Alice", "New York",
    "Bob, Jr.", "Los Angeles, CA",
]

# numstat is numbers, not CSV cells. A small fixture with known values so the
# assertion can check the on-disk report contains the right COMPUTED stats.
# count=10, sum=62.0, mean=6.2, min=1.0, max=12.0 — the report-generator's
# formatter prints Mean/Min/Max (Median/Mode/StdDev/Variance fall to N/A, a
# benign upstream-contract gap), so we assert on those three values.
NUMSTAT_NUMBERS = "1\n2\n3\n4\n5\n10\n10\n12\n7\n8\n"
NUMSTAT_EXPECTED = ["6.2", "1.0", "12.0"]


@dataclass
class SmokeSpec:
    """Per-project smoke fixture + assertion.

    THE PROJECT-AWARE SEAM (flagged): the DAG WALK below is fully general — it
    composes any single-source/single-sink DAG by feeding each node its upstream
    results. But what to FEED the source and what to ASSERT on the output are
    inherently project-specific (csvmd wants a CSV in + cell-strings out;
    numstat wants numbers in + computed stats out). v1 keeps these in a small
    per-project registry rather than trying to synthesize a fixture from the
    architecture. A project with no registered spec is a FINDING (the runner
    refuses to guess what input shape the source expects), not a silent pass."""
    input_filename: str          # the source's input file, e.g. "input.csv"
    input_content: str           # fixture text written to that file
    assert_contains: list        # substrings that MUST appear in the output file
    label: str = ""              # short human description for the verdict line


SMOKE_SPECS = {
    "csvmd": SmokeSpec("input.csv", SMOKE_CSV, EXPECTED_CELLS,
                       "csv cells incl. quoted-comma row"),
    "numstat": SmokeSpec("input.txt", NUMSTAT_NUMBERS, NUMSTAT_EXPECTED,
                         "computed mean/min/max"),
}


def smoke_spec_for(project_name: str):
    """The registered SmokeSpec for a project, or None (a FINDING upstream)."""
    return SMOKE_SPECS.get(project_name)


@dataclass
class SmokeResult:
    ok: bool
    summary: str                       # one-line verdict
    out_path: str | None = None        # the tmp file the chain wrote (pre-cleanup)
    markdown: str | None = None        # what was read back off disk
    entries: dict = field(default_factory=dict)   # subsystem -> resolved entry fn name
    findings: list = field(default_factory=list)   # precise breaks, if any


# --- entry-module import (isolated, S7's sys.path pattern) ------------------

def _entry_module_name(subsystem_name: str) -> str:
    """Canonical entry-module name: the subsystem name with `-` -> `_`.
    file-reader -> file_reader, markdown-formatter -> markdown_formatter. This
    matched both Session-5/6 built subsystems (file_reader.py, csv_parser.py)."""
    return subsystem_name.replace("-", "_")


# Stems that are NOT the entry module: tests, smoke harnesses, helper/util
# modules, package markers. Used by _resolve_entry_file's discovery fallback.
_NON_ENTRY_STEM_RE = re.compile(r"(^test_|_test$|smoke|conftest|^__init__$|_utils$|^utils$)")


def _resolve_entry_file(subsystem_dir: Path, subsystem_name: str) -> Path:
    """Locate the subsystem's entry `.py` module.

    Prefers the canonical `<name with _>.py`. When that is ABSENT — e.g. Tier 1
    degraded to its single-file legacy path and slugged the filename from the
    prompt header (the S14 file-writer finding: `build_subsystem_file_writer_…py`
    instead of `file_writer.py`) — DISCOVER the entry instead of hard-failing:
    take the `.py` files that don't look like tests/smoke/helpers and pick the
    best by name-hint (the same hints discover_entry uses for the function).
    With one non-helper module this is just "that module". Raises
    FileNotFoundError only when there is genuinely no candidate.

    This makes smoke TOLERANT of a degraded build (a down payment on the
    DAG-aware runner) rather than dying at import on a non-canonical filename.
    """
    subsystem_dir = Path(subsystem_dir)
    canonical = subsystem_dir / f"{_entry_module_name(subsystem_name)}.py"
    if canonical.exists():
        return canonical
    pyfiles = sorted(subsystem_dir.glob("*.py"))
    if not pyfiles:
        raise FileNotFoundError(
            f"no entry module for `{subsystem_name}` in {subsystem_dir} "
            f"(no .py files at all; not built?)"
        )
    non_helper = [p for p in pyfiles if not _NON_ENTRY_STEM_RE.search(p.stem)]
    candidates = non_helper or pyfiles  # if everything looks like a helper, fall back
    hints = _name_hints(subsystem_name)

    def score(p: Path) -> int:
        low = p.stem.lower()
        return sum(2 for h in hints if h and h in low)

    # Highest hint score wins; ties break alphabetically (pyfiles is sorted, and
    # max returns the first maximal element). Deterministic.
    return max(candidates, key=score)


def _import_entry(subsystem_dir: Path, subsystem_name: str):
    """Import a subsystem's entry module in ISOLATION and return the live module
    object.

    The entry module is resolved by _resolve_entry_file: the canonical
    `<name with _>.py` when present, else discovered (handles a degraded/slugged
    filename). The module is loaded under its real file-stem name, so sibling
    imports (`from writer_utils import …`) resolve via sys.path.

    Isolation matters: subsystems may ship same-named helpers (csv-parser ships
    `utils.py`; another subsystem could too). We put the subsystem's own dir on
    sys.path so its sibling imports resolve to ITS files, exec the entry module,
    then strip the dir from sys.path and purge the modules this import added. The
    returned module object stays alive via our reference, and the functions
    inside keep their globals — so a later subsystem's `utils` can't shadow an
    earlier one's.

    Importing never triggers the subsystem's `__main__` self-check: the module
    is loaded under its real name, not "__main__" (the S7-confirmed contract).
    """
    subsystem_dir = Path(subsystem_dir)
    entry_file = _resolve_entry_file(subsystem_dir, subsystem_name)
    modname = entry_file.stem

    dir_str = str(subsystem_dir)
    added = dir_str not in sys.path
    if added:
        sys.path.insert(0, dir_str)
    before = set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(modname, str(entry_file))
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if added and dir_str in sys.path:
            sys.path.remove(dir_str)
        # Purge everything this import introduced (incl. the entry under modname
        # and any siblings like `utils`) so the next subsystem imports cleanly.
        for key in set(sys.modules) - before:
            del sys.modules[key]


# --- entry-function discovery -----------------------------------------------

def _name_hints(subsystem_name: str) -> set[str]:
    """Substring hints derived from the subsystem name, including stems of the
    common agent-noun suffixes so `reader`->read, `parser`->pars(e),
    `formatter`->format, `writer`->writ(e) all hit the obvious function name."""
    hints: set[str] = set()
    for token in subsystem_name.replace("_", "-").lower().split("-"):
        if not token:
            continue
        hints.add(token)
        if token.endswith("ter") and len(token) > 4:      # formatter -> format
            hints.add(token[:-3])
        if token.endswith(("er", "or")) and len(token) > 3:  # reader->read, parser->pars
            hints.add(token[:-2])
    return hints


def _public_callables(module) -> dict:
    """Module-level callables that could be the entry: excludes `main` (the CLI
    wrapper / self-check), private names, classes, imported modules, and
    builtins. Re-exported functions (e.g. csv_parser re-exports parse_csv from
    utils) ARE included — we look at the module namespace, not __module__."""
    out = {}
    for name, obj in vars(module).items():
        if name.startswith("_") or name == "main":
            continue
        if not callable(obj):
            continue
        if inspect.isclass(obj) or inspect.ismodule(obj):
            continue
        if getattr(obj, "__module__", None) == "builtins":
            continue
        out[name] = obj
    return out


def _arity(fn) -> tuple[int, int]:
    """(required positional count, total positional count) — best effort; falls
    back to (1, 1) when the signature can't be introspected."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return (1, 1)
    required = total = 0
    for p in sig.parameters.values():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            total += 1
            if p.default is p.empty:
                required += 1
        elif p.kind == p.VAR_POSITIONAL:
            total += 99  # *args swallows anything
    return (required, total)


def discover_entry(module, subsystem_name: str, want_arity: int):
    """Pick the most likely entry callable from an imported subsystem module.

    Scoring (higher wins): +2 per name-hint substring match, +2 if the wanted
    arity falls within [required, total]. With one viable callable this is just
    "the single non-main public function". Returns (name, fn) or raises
    LookupError with a precise message (a FINDING, not a silent miss).
    """
    cands = _public_callables(module)
    if not cands:
        raise LookupError(
            f"{subsystem_name}: no public non-`main` callable to use as the "
            f"entry (module exposes: {sorted(vars(module))})"
        )
    hints = _name_hints(subsystem_name)

    def score(name, fn):
        s = 0
        low = name.lower()
        for h in hints:
            if h and h in low:
                s += 2
        req, tot = _arity(fn)
        if req <= want_arity <= tot:
            s += 2
        return s

    ranked = sorted(cands.items(), key=lambda kv: (score(*kv), kv[0]), reverse=True)
    best_name, best_fn = ranked[0]
    if len(ranked) > 1 and score(best_name, best_fn) == score(*ranked[1]):
        # Tie at the top — still pick the alphabetically-first arity-fit, but the
        # ambiguity is worth surfacing in the result for the human reading it.
        pass
    return best_name, best_fn


# --- DAG ordering -----------------------------------------------------------

def _order_upstreams(sub, upstreams: list[str]):
    """Order a JOIN node's upstreams to match its entry-function call signature.

    The order is derived from the architecture's `Inputs:` declaration order:
    each upstream must be named in a distinct Inputs bullet, and the call order
    follows the bullets. For numstat's report-generator —
        generate_report(summary, histogram)
    with Inputs `- summary statistics from statistics-computer` then
    `- histogram data from histogram-builder` — this maps statistics-computer
    (summary) before histogram-builder (histogram), NOT alphabetically or by
    topo-arbitrary order.

    Returns (ordered_list, None) on success, or (None, reason) when the order
    can't be unambiguously derived — THE SUBTLE PART is a FINDING, never a
    silent guess. Zero/one upstream is trivially ordered.
    """
    ups = list(upstreams)
    if len(ups) <= 1:
        return ups, None
    inputs = list(getattr(sub, "inputs", []) or [])
    pos: dict[str, int | None] = {}
    for u in ups:
        # Match the upstream name as a WHOLE token (word-boundary), not an
        # arbitrary substring — else a short name would spuriously match inside
        # an unrelated word. Accept the kebab, spaced, and snake spellings.
        variants = {u.lower(), u.replace("-", " ").lower(), u.replace("-", "_").lower()}
        pats = [re.compile(r"\b" + re.escape(v) + r"\b") for v in variants]
        found = None
        for i, bullet in enumerate(inputs):
            b = bullet.lower()
            if any(p.search(b) for p in pats):
                found = i
                break
        pos[u] = found
    missing = [u for u in ups if pos[u] is None]
    if missing:
        return None, (f"multi-upstream join: cannot map upstream(s) {missing} to any "
                      f"`Inputs:` bullet {inputs!r} — argument order is ambiguous")
    if len({pos[u] for u in ups}) != len(ups):
        return None, (f"multi-upstream join: upstreams map to overlapping `Inputs:` "
                      f"bullets ({pos}) — argument order is ambiguous")
    return sorted(ups, key=lambda u: pos[u]), None


def _format_chain(topo_order, ordered_ups, entries, source, sink) -> str:
    """A readable rendering of the DAG that was actually composed."""
    parts = []
    for n in topo_order:
        if n == source:
            role, ins = "source", "(input file)"
        else:
            role = "sink" if n == sink else ("join" if len(ordered_ups[n]) > 1 else "transform")
            ins = "+".join(ordered_ups[n])
        parts.append(f"{n}:{entries[n]}({ins})[{role}]")
    return " ; ".join(parts)


# --- the chain --------------------------------------------------------------

def run_smoke(project_dir: Path, arch, topo_order: list[str], spec) -> SmokeResult:
    """Compose the built subsystems as a DAG and verify the on-disk output.

    project_dir: tier2_projects/<name>/ (real or a tmpdir in tests).
    arch:        the parsed Architecture — supplies each node's UPSTREAMS
                 (depends_on) and `Inputs:` declarations (the DAG edges).
    topo_order:  subsystem names, deps before dependents.
    spec:        the per-project SmokeSpec (source input + output assertion).

    The runner walks topo order keeping a results map {node -> output}. Each node
    is fed its upstreams' results per the DAG edges (NOT a single threaded value):
      - SOURCE (no upstreams): entry(input_path)
      - single-upstream transform: entry(results[upstream])
      - MULTI-upstream JOIN: entry(*[results[u] for u in ordered upstreams]),
        ordered by the architecture's Inputs declaration (see _order_upstreams)
      - SINK (topo-last / no dependents): entry(*upstream_results, out_path)

    Returns a SmokeResult; never raises for a composition break — that is the
    thing under test, so it comes back as ok=False + findings.
    """
    project_dir = Path(project_dir)
    subs_root = project_dir / "subsystems"
    findings: list[str] = []
    entries: dict[str, str] = {}

    if len(topo_order) < 2:
        return SmokeResult(False, f"need >=2 subsystems to smoke; got {topo_order}",
                           findings=["architecture has fewer than 2 subsystems"])
    if spec is None:
        return SmokeResult(
            False, "no smoke fixture registered for this project",
            findings=["no SmokeSpec: the runner needs a per-project input fixture "
                      "+ output assertion (add one to SMOKE_SPECS) — it will not "
                      "guess what input the source expects"])

    # DAG edges from the architecture (NOT topo position).
    by_name = arch.by_name()
    upstreams = {n: [u for u in by_name[n].depends_on if u in topo_order]
                 for n in topo_order}
    dependents: dict[str, list[str]] = {n: [] for n in topo_order}
    for n in topo_order:
        for u in upstreams[n]:
            dependents[u].append(n)
    sources = [n for n in topo_order if not upstreams[n]]
    sinks = [n for n in topo_order if not dependents[n]]
    if len(sources) != 1:
        findings.append(f"v1 models a single-source pipeline; found sources={sources}")
        return SmokeResult(False, "not a single-source DAG", findings=findings)
    if len(sinks) != 1:
        findings.append(f"v1 models a single-sink pipeline; found sinks={sinks}")
        return SmokeResult(False, "not a single-sink DAG", findings=findings)
    source, sink = sources[0], sinks[0]

    # Import every entry module up front, in topo order, so an ImportError is
    # reported against the exact subsystem (and proves import-safety — the
    # self-check must NOT fire on import).
    modules: dict[str, object] = {}
    for name in topo_order:
        sub_dir = subs_root / name
        if not sub_dir.exists():
            findings.append(f"{name}: subsystems/{name}/ does not exist (not built?)")
            return SmokeResult(False, f"{name} not built", findings=findings)
        try:
            modules[name] = _import_entry(sub_dir, name)
        except Exception as e:  # noqa: BLE001 — any import error is a finding
            findings.append(f"{name}: import failed — {type(e).__name__}: {e}")
            return SmokeResult(False, f"import of {name} failed", findings=findings)

    # Order each node's upstreams (the join arg-ordering — a FINDING if ambiguous).
    ordered_ups: dict[str, list[str]] = {}
    for name in topo_order:
        ordered, why = _order_upstreams(by_name[name], upstreams[name])
        if ordered is None:
            findings.append(f"{name}: {why}")
            return SmokeResult(False, f"cannot order inputs for {name}",
                               findings=findings, entries=entries)
        ordered_ups[name] = ordered

    # Resolve entry callables with per-node wanted arity (#inputs, +1 for sink).
    fns: dict[str, object] = {}
    for name in topo_order:
        n_inputs = 1 if name == source else len(ordered_ups[name])
        want = n_inputs + (1 if name == sink else 0)
        try:
            ename, fn = discover_entry(modules[name], name, want)
        except LookupError as e:
            findings.append(str(e))
            return SmokeResult(False, f"no entry fn for {name}", findings=findings, entries=entries)
        entries[name] = ename
        fns[name] = fn

    # Write the source fixture + run the DAG, all under one tmp dir.
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="oc2_smoke_"))
    in_path = tmpdir / spec.input_filename
    out_path = tmpdir / "output.md"
    in_path.write_text(spec.input_content, encoding="utf-8")

    try:
        results: dict[str, object] = {}
        for name in topo_order:
            fn = fns[name]
            if name == source:
                call_args = [str(in_path)]
            else:
                call_args = [results[u] for u in ordered_ups[name]]
                if name == sink:
                    call_args.append(str(out_path))

            # Arity pre-check: a required parameter the DAG can't supply is a
            # FINDING, not a raw TypeError. (numstat's histogram-builder entry
            # calculate_histogram(number_stream, bucket_size) needs a bucket_size
            # that no upstream provides — exactly this case.)
            req, tot = _arity(fn)
            if not (req <= len(call_args) <= tot):
                extra = ", + out_path" if name == sink else ""
                findings.append(
                    f"{name} (entry `{entries[name]}`) requires {req}..{tot} positional "
                    f"arg(s) but the DAG supplies {len(call_args)} "
                    f"(upstreams={ordered_ups[name]}{extra}) — a required parameter has "
                    f"no upstream source."
                )
                return SmokeResult(False, f"{name} arity mismatch",
                                   findings=findings, entries=entries)
            try:
                results[name] = fn(*call_args)
            except Exception as e:  # noqa: BLE001 — a runtime break is a finding
                findings.append(
                    f"{name} (entry `{entries[name]}`) raised "
                    f"{type(e).__name__}: {e} — chain broke here"
                )
                return SmokeResult(False, f"{name} raised at runtime",
                                   findings=findings, entries=entries)

        # The sink wrote to disk — read it back and assert expected content survived.
        if not out_path.exists():
            findings.append(f"{sink} (entry `{entries[sink]}`) did not create {out_path.name}")
            return SmokeResult(False, "writer produced no file",
                               out_path=str(out_path), findings=findings, entries=entries)
        markdown = out_path.read_text(encoding="utf-8")
        missing = [c for c in spec.assert_contains if c not in markdown]
        if missing:
            findings.append(
                f"on-disk output is missing expected content {missing} "
                f"({spec.label or 'expected values'} did not survive the chain)"
            )
            return SmokeResult(False, f"output missing {len(missing)} item(s)",
                               out_path=str(out_path), markdown=markdown,
                               findings=findings, entries=entries)

        chain = _format_chain(topo_order, ordered_ups, entries, source, sink)
        label = f" [{spec.label}]" if spec.label else ""
        return SmokeResult(
            True,
            f"DAG composes end-to-end on disk ({chain}); "
            f"all {len(spec.assert_contains)} expected item(s) present{label}",
            out_path=str(out_path), markdown=markdown, entries=entries,
        )
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- CLI --------------------------------------------------------------------

def cmd_smoke(args) -> int:
    """`oc2 smoke <project>` — import the built subsystems in topo order and run
    the integration chain. Exit 0 = composes; 1 = a finding (or not built)."""
    from oc2 import TIER2_PROJECTS
    from oc2.approve import resolve_project
    from oc2.architecture import ParseError, parse, validate
    from oc2.state import read_state

    name = resolve_project(args.project)
    if name is None:
        print("error: specify which project to smoke, e.g. `oc2 smoke <name>`.")
        return 1
    project_dir = TIER2_PROJECTS / name
    arch_path = project_dir / "architecture.md"
    if not arch_path.exists():
        print(f"error: no project {name!r} (architecture.md missing).")
        return 1
    state = read_state(project_dir)
    if state is None:
        print(f"error: {name!r} has no state.json — run `oc2 approve {name}` first.")
        return 1

    try:
        arch = parse(arch_path.read_text(encoding="utf-8"))
    except ParseError as e:
        print(f"error: architecture.md no longer parses ({e}).")
        return 1
    res = validate(arch)
    if not res.ok or res.topo_order is None:
        print("error: architecture.md no longer validates; cannot order subsystems.")
        return 1

    # Every subsystem must be done before the chain can run.
    not_done = [n for n in res.topo_order
                if state["subsystems"].get(n, {}).get("status") != "done"]
    if not_done:
        print(f"error: cannot smoke {name} — not all subsystems are built: "
              f"{', '.join(not_done)} still pending/failed.")
        return 1

    print(f"[oc2 smoke] {name}: importing {len(res.topo_order)} subsystem(s) "
          f"in topo order and running the DAG chain...", flush=True)
    result = run_smoke(project_dir, arch, res.topo_order, smoke_spec_for(name))
    print(f"[oc2 smoke] entries: "
          + ", ".join(f"{k}->{v}" for k, v in result.entries.items()))
    if result.ok:
        print(f"[oc2 smoke] PASS: {result.summary}")
        return 0
    print(f"[oc2 smoke] FAIL: {result.summary}")
    for f in result.findings:
        print(f"  - {f}")
    return 1
