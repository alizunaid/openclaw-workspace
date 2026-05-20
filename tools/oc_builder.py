#!/usr/bin/env python3
"""OpenClaw builder engine — multi-file coordinated codegen with self-heal.

Pipeline (multi-file mode):
  PHASE 1  Manifest planner. LLM returns JSON [{path, purpose, depends_on}].
           GATE 1: valid JSON, schema, DAG, <=6 files. Fail -> degrade to legacy.
  PHASE 2  Topological generation. Each file: original task + full manifest +
           source of already-generated deps. Per-file AST check, up to 3 retries.
           GATE 2: every file AST-compiles. Fail -> dump + exit 1.
  PHASE 3  Cross-file validation. Dry-import the entry-point in a subprocess;
           run any smoke-test files (purpose contains "test"/"smoke").
           GATE 3: dry-import ok and smoke tests exit 0. Fail -> dump + exit 1.
  PHASE 4  git add/commit/push to github-clean.

If the manifest planner fails or returns garbage, fall back to legacy single-file
mode (runtime self-heal, no auto-commit).
"""
import sys, subprocess, re, json, argparse, ast, time, atexit, shutil
from pathlib import Path
from datetime import datetime
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_project import resolve_slug, load_project

print("[builder] script loaded", flush=True)

WORKSPACE = Path("/root/.openclaw/workspace")
WORKSPACE_RESOLVED = WORKSPACE.resolve()
GENERATED_DIR = WORKSPACE / "tools" / "generated"
LOGS_DIR = WORKSPACE / "logs"
RUN_ARCHIVE_DIR = LOGS_DIR / "run_archive"
RUN_ARCHIVE_MAX = 50  # keep the N most-recent run_<ts>/ snapshots
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
MODEL = "qwen2.5-coder:32b"

MAX_RETRIES = 3
MAX_MANIFEST_FILES = 6
RUN_CAP_SECONDS = 1200
PER_LLM_TIMEOUT = 300
LEGACY_RUN_TIMEOUT = 120
DRY_IMPORT_TIMEOUT = 30
SMOKE_TEST_TIMEOUT = 60

INSPECTION_EXTS = {".csv", ".md", ".json", ".yaml", ".yml", ".txt", ".tsv", ".sh", ".py"}
INSPECTION_SUBDIRS = ("projects", "scripts", "tools", "tasks")
MAX_INSPECT_FILES = 5
HEAD_LINES = 30
TAIL_LINES = 30
MAX_CANDIDATES = 80
MAX_SAMPLE_BYTES = 12000

FILENAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.py$")
GIT_BRANCH = "github-clean"


# ---------------------------------------------------------------------------
# Shared helpers (inspection planner preserved from df64fb0)
# ---------------------------------------------------------------------------

def clean_code(raw):
    return re.sub(r"^```python\n?|^```\n?|```$", "", raw, flags=re.MULTILINE).strip()


def list_candidate_files():
    seen = []
    for p in sorted(WORKSPACE.iterdir()):
        if p.is_file() and p.suffix.lower() in INSPECTION_EXTS:
            seen.append(p.relative_to(WORKSPACE).as_posix())
    for sub in INSPECTION_SUBDIRS:
        d = WORKSPACE / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix.lower() in INSPECTION_EXTS:
                seen.append(p.relative_to(WORKSPACE).as_posix())
    return seen[:MAX_CANDIDATES]


def safe_resolve_inside_workspace(rel):
    if not rel or not isinstance(rel, str):
        return None
    try:
        p = (WORKSPACE / rel).resolve()
        p.relative_to(WORKSPACE_RESOLVED)
    except (ValueError, OSError):
        return None
    return p if p.is_file() else None


def sample_file(p):
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[unreadable: {e}]"
    if len(text) > MAX_SAMPLE_BYTES * 4:
        text = text[: MAX_SAMPLE_BYTES * 4]
    lines = text.splitlines()
    if len(lines) <= HEAD_LINES + TAIL_LINES:
        return text
    omitted = len(lines) - HEAD_LINES - TAIL_LINES
    head = "\n".join(lines[:HEAD_LINES])
    tail = "\n".join(lines[-TAIL_LINES:])
    return f"{head}\n... [{omitted} lines omitted] ...\n{tail}"


def plan_files_to_inspect(task, context, candidates):
    if not candidates:
        return []
    listing = "\n".join(candidates)
    planner_system = (
        "You are a file-selection planner. Given a user task, project context, and a "
        "list of workspace files, return a JSON array of up to 5 relative file paths "
        "(taken from the provided list) that should be inspected before writing code. "
        "Pick files whose contents (schema, columns, structure) the code generator "
        "needs to know. Return ONLY a JSON array of strings. No markdown. No commentary."
    )
    planner_user = (
        f"Task:\n{task}\n\n"
        f"Project context:\n{context}\n\n"
        f"Available files:\n{listing}\n\n"
        f"Return a JSON array of up to {MAX_INSPECT_FILES} relative paths."
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": planner_system},
                {"role": "user", "content": planner_user},
            ],
            timeout=PER_LLM_TIMEOUT,
        )
    except Exception as e:
        print(f"[builder] Planner LLM call failed: {e}", flush=True)
        return []
    raw = (resp.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?\n?|```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        picks = json.loads(raw[start : end + 1])
    except Exception:
        return []
    if not isinstance(picks, list):
        return []
    cleaned = []
    for x in picks:
        if isinstance(x, str) and x.strip():
            cleaned.append(x.strip())
        if len(cleaned) >= MAX_INSPECT_FILES:
            break
    return cleaned


def gather_inspections(task, context):
    candidates = list_candidate_files()
    picks = plan_files_to_inspect(task, context, candidates)
    print(f"[builder] Planner picked: {picks}", flush=True)
    samples = []
    for rel in picks:
        p = safe_resolve_inside_workspace(rel)
        if p is None:
            print(f"[builder] Skipping (outside workspace or missing): {rel}", flush=True)
            continue
        sample = sample_file(p)
        abs_path = str(p.resolve())
        samples.append(f"=== {rel}  (absolute path: {abs_path}) ===\n{sample}")
    return "\n\n".join(samples)


# ---------------------------------------------------------------------------
# Manifest planner (PHASE 1)
# ---------------------------------------------------------------------------

def _extract_first_json_array(raw):
    """Find the first balanced JSON array in raw text. Returns (parsed, err).

    Tolerant to trailing prose or stray closing brackets after the array.
    Uses raw_decode so we accept the first valid value and ignore the rest.
    """
    if not raw:
        return None, "empty response"
    raw = re.sub(r"^```(?:json)?\n?|```$", "", raw, flags=re.MULTILINE).strip()
    start = raw.find("[")
    if start == -1:
        return None, "no '[' in response"
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(raw[start:])
        return value, ""
    except json.JSONDecodeError as e:
        return None, f"JSONDecodeError: {e}"


def get_manifest(task, context, inspections):
    """Ask the LLM for a multi-file manifest. Returns (parsed_json|None, error_reason)."""
    system = (
        "You are a multi-file project planner for a Python codegen pipeline.\n"
        "Given a user task, project context, and inspected workspace samples, "
        "return a JSON array describing every file to create.\n"
        "Each entry MUST be an object with EXACTLY these keys:\n"
        '  {"path": "<filename.py>", "purpose": "<short purpose>", "depends_on": ["<other_filename.py>", ...], "exports": ["<symbol>", ...]}\n'
        "Rules:\n"
        f"- Maximum {MAX_MANIFEST_FILES} files.\n"
        "- `path` must be a plain Python filename (e.g. main.py). No slashes, no subdirs, must end .py.\n"
        "- `depends_on` lists OTHER files in THIS manifest that the file imports. Use the exact filename, not a module path.\n"
        "- Dependencies must form a DAG (no cycles, no self-dependency).\n"
        "- `exports` lists the function names, class names, and module-level constants this file MUST define at top level. "
        "This is a CONTRACT: downstream modules may import only the names listed here, and the file generator will be "
        "required to define exactly these names. Use the empty list `[]` for files that are run as scripts and define "
        "nothing for other files to import.\n"
        "- One file is fine if the task is simple — return a 1-entry array.\n"
        "- If you include a smoke test, put the word \"test\" or \"smoke\" in its purpose.\n"
        "\n"
        "SMOKE TEST RULES:\n"
        "- A smoke test file (any file with \"test\" or \"smoke\" in its purpose) MUST NOT import internal "
        "functions from the entry script. The entry script's declared exports are empty (it is run, "
        "not imported), so any `from entry_script import internal_func` will be rejected by the contract lint.\n"
        "- Instead, run the entry script as a subprocess and assert on its stdout, exit code, or output files.\n"
        "- Example pattern for a smoke test file:\n"
        "    import subprocess\n"
        "    result = subprocess.run([\"python3\", \"main.py\"], capture_output=True, text=True)\n"
        "    assert result.returncode == 0\n"
        "    assert \"expected substring\" in result.stdout\n"
        "- If a smoke test legitimately needs to test internal functions, those functions must be in a "
        "separate non-entry module with declared exports — not in the entry script itself.\n"
        "\n"
        "DATETIME RULES (when the task mentions dates, ages, \"days ago\", \"stale\", \"older than N days\", or timestamps):\n"
        "- Use `datetime.now(timezone.utc)` — NOT `datetime.now()` and NOT `datetime.utcnow()`.\n"
        "- When parsing ISO timestamps, the result of `datetime.fromisoformat(...)` may be tz-aware. To subtract "
        "from \"now\" safely, both sides must be tz-aware. Use `datetime.now(timezone.utc)` for \"now\".\n"
        "- Import: `from datetime import datetime, timezone`.\n"
        "- Never mix tz-naive and tz-aware datetimes in arithmetic — Python raises TypeError.\n"
        "\n"
        "- Return ONLY the JSON array. No markdown fences. No commentary.\n"
        'Example for a tiny multi-file task:\n'
        '[{"path":"utils.py","purpose":"helper module: add(a,b)","depends_on":[],"exports":["add"]},'
        '{"path":"main.py","purpose":"entry point — imports utils and prints add(2,3)","depends_on":["utils.py"],"exports":[]}]'
    )
    user_parts = [f"Task:\n{task}", "", f"Project context:\n{context}"]
    if inspections:
        user_parts.extend(["", f"Inspected workspace files:\n{inspections}"])
    user_parts.append("\nReturn the JSON manifest now.")
    user = "\n".join(user_parts)

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            timeout=PER_LLM_TIMEOUT,
        )
    except Exception as e:
        msg = f"LLM call failed: {e}"
        print(f"[builder] Manifest: {msg}", flush=True)
        return None, msg

    raw = (resp.choices[0].message.content or "").strip()
    value, err = _extract_first_json_array(raw)
    if value is None:
        print(f"[builder] Manifest: {err}", flush=True)
        return None, err
    return value, ""


def validate_manifest(manifest):
    """Validate a manifest. Returns (errors:list[str], topo_order:list[dict]|None).

    On success, errors is empty and topo_order is the manifest entries sorted in
    dependency order (deps before dependents).
    """
    errors = []
    if not isinstance(manifest, list):
        return ["manifest is not a JSON array"], None
    if len(manifest) == 0:
        return ["manifest is empty"], None
    if len(manifest) > MAX_MANIFEST_FILES:
        return [f"manifest has {len(manifest)} entries (max {MAX_MANIFEST_FILES})"], None

    seen_paths = set()
    normalized = []
    for i, entry in enumerate(manifest):
        if not isinstance(entry, dict):
            errors.append(f"entry[{i}] is not an object")
            continue
        path = entry.get("path")
        purpose = entry.get("purpose", "")
        deps = entry.get("depends_on", [])
        # `exports` is the symbol contract. Missing field = backward-compat: store
        # None to signal "no contract declared" so downstream gates can soft-skip.
        exports_raw = entry.get("exports", None)
        if not isinstance(path, str) or not FILENAME_RE.match(path):
            errors.append(f"entry[{i}]: invalid path {path!r} (must be bare filename ending .py)")
            continue
        if path in seen_paths:
            errors.append(f"entry[{i}]: duplicate path {path}")
            continue
        seen_paths.add(path)
        if not isinstance(purpose, str):
            purpose = str(purpose)
        if not isinstance(deps, list):
            errors.append(f"entry[{i}] ({path}): depends_on is not a list")
            continue
        clean_deps = []
        for d in deps:
            if isinstance(d, str) and d.strip():
                clean_deps.append(d.strip())
        if exports_raw is None:
            clean_exports = None
        elif isinstance(exports_raw, list):
            clean_exports = [s.strip() for s in exports_raw if isinstance(s, str) and s.strip()]
        else:
            errors.append(f"entry[{i}] ({path}): exports is not a list")
            continue
        normalized.append({
            "path": path,
            "purpose": purpose,
            "depends_on": clean_deps,
            "exports": clean_exports,
        })

    if errors:
        return errors, None

    paths = {e["path"] for e in normalized}
    for e in normalized:
        for d in e["depends_on"]:
            if d not in paths:
                errors.append(f"{e['path']}: depends_on {d!r} not in manifest")
            if d == e["path"]:
                errors.append(f"{e['path']}: self-dependency")
    if errors:
        return errors, None

    # Kahn's topological sort. Stable: pick alphabetically smallest at each step.
    in_degree = {e["path"]: 0 for e in normalized}
    graph = {e["path"]: [] for e in normalized}
    for e in normalized:
        for d in e["depends_on"]:
            graph[d].append(e["path"])
            in_degree[e["path"]] += 1
    queue = sorted([p for p, deg in in_degree.items() if deg == 0])
    order = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for nxt in sorted(graph[node]):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
        queue.sort()
    if len(order) != len(normalized):
        return ["dependency graph has a cycle"], None

    path_to_entry = {e["path"]: e for e in normalized}
    return [], [path_to_entry[p] for p in order]


def _is_test_entry(entry):
    """Detect test/smoke modules by filename OR purpose.

    The LLM is unreliable about including the words "test" or "smoke" in the
    purpose string. The determinism sweep produced a case (Task 5 run c) where
    a file named ``smoke_test.py`` had purpose "runs the main script with the
    real CSV and confirms it produces output without raising an exception" —
    no literal "test"/"smoke" — and was therefore picked as the entry-point.
    Filename is the more reliable signal. Keep the purpose check as a belt +
    suspenders for tests whose filename doesn't match convention.
    """
    purpose = (entry.get("purpose") or "").lower()
    name = (entry.get("path") or "").lower()
    if "test" in name or "smoke" in name:
        return True
    if "test" in purpose or "smoke" in purpose:
        return True
    return False


_ENTRY_POINT_PURPOSE_HINTS = ("entry point", "main script")


def find_entry_point(topo_order):
    """Pick the entry-point as the real production file, never a test.

    Order of preference:
      0. If a non-test module is named exactly main.py, pick it.
      1. Else if a non-test module's purpose contains "entry point" or "main
         script", pick the last such module in topo order.
      2. Else the last non-test file among DAG sinks (sinks = entries with no
         dependents).
      3. If every sink is a test, the last non-test file anywhere in topo order
         — tests typically depend on the real entry, so the deepest non-test is
         it.
      4. If the entire manifest is tests, fall back to the original behavior
         (last sink in topo order) and log a warning.

    Preferences 0 and 1 were added after the determinism sweep produced cases
    (Task 3 run b, Task 4 run a) where the LLM emitted helper modules with
    deps=[] that nothing depended on — making them DAG sinks — and the heuristic
    picked utils.py / formatter.py over the real main.
    """
    # Preference 0: a non-test module literally named main.py.
    for entry in topo_order:
        if entry["path"].lower() == "main.py" and not _is_test_entry(entry):
            return entry

    # Preference 1: a non-test module whose purpose declares it the entry.
    purpose_entry = None
    for entry in topo_order:
        if _is_test_entry(entry):
            continue
        purpose = (entry.get("purpose") or "").lower()
        if any(h in purpose for h in _ENTRY_POINT_PURPOSE_HINTS):
            purpose_entry = entry
    if purpose_entry is not None:
        return purpose_entry

    dependents = {e["path"]: 0 for e in topo_order}
    for e in topo_order:
        for d in e["depends_on"]:
            if d in dependents:
                dependents[d] += 1
    no_deps = [e for e in topo_order if dependents[e["path"]] == 0]
    non_test_no_deps = [e for e in no_deps if not _is_test_entry(e)]

    if non_test_no_deps:
        candidates = {e["path"] for e in non_test_no_deps}
        for entry in reversed(topo_order):
            if entry["path"] in candidates:
                return entry
        return non_test_no_deps[-1]

    non_test_anywhere = [e for e in topo_order if not _is_test_entry(e)]
    if non_test_anywhere:
        return non_test_anywhere[-1]

    print(
        "[builder] WARNING: manifest is entirely test/smoke files; "
        "falling back to last sink as entry-point.",
        flush=True,
    )
    if not no_deps:
        return topo_order[-1]
    no_deps_paths = {e["path"] for e in no_deps}
    for entry in reversed(topo_order):
        if entry["path"] in no_deps_paths:
            return entry
    return no_deps[-1]


# ---------------------------------------------------------------------------
# Per-file generation with AST self-heal (PHASE 2)
# ---------------------------------------------------------------------------

def split_manifest_sections(code, manifest, target_path):
    """Defense against LLM emitting multiple files concatenated with filename headers.

    If the response contains lines matching '<filename>' / '# <filename>' / '## <filename>'
    for filenames in the manifest, split by those headers and return only the section
    whose header matches target_path. Returns code unchanged if no headers found.
    """
    if not manifest or not code:
        return code
    paths_set = {e["path"] for e in manifest}

    def header_for(line):
        stripped = line.strip()
        if stripped in paths_set:
            return stripped
        for prefix in ("# ", "## ", "### ", "// "):
            if stripped.startswith(prefix):
                rest = stripped[len(prefix):].strip()
                if rest in paths_set:
                    return rest
        return None

    lines = code.splitlines(keepends=True)
    sections = []
    current_header = None
    current_body = []
    for line in lines:
        h = header_for(line)
        if h is not None:
            if current_header is not None or current_body:
                sections.append((current_header, "".join(current_body)))
            current_header = h
            current_body = []
        else:
            current_body.append(line)
    if current_header is not None or current_body:
        sections.append((current_header, "".join(current_body)))

    if not any(h is not None for h, _ in sections):
        return code
    for h, body in sections:
        if h == target_path:
            return body.strip() + "\n"
    return code


def ast_check(code):
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} at line {e.lineno}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


_DATETIME_TASK_KEYWORDS = ("stale", "days", "older than", "ago", "timestamp")


def _task_mentions_dates(task):
    if not task:
        return False
    t = task.lower()
    return any(kw in t for kw in _DATETIME_TASK_KEYWORDS)


def _build_per_file_system_prompt(entry, manifest, generated_sources, info, inspections, task=""):
    """Construct the system prompt used to generate one file in a multi-file project.

    Shared between the primary `generate_one_file` flow and the Fix-B retry path
    (`regenerate_for_main_guards`) so both see exactly the same manifest +
    contract + dep context.
    """
    manifest_by_path = {e["path"]: e for e in manifest}

    deps_blocks = []
    for d_path in entry["depends_on"]:
        d_code = generated_sources.get(d_path, "")
        d_exports = manifest_by_path.get(d_path, {}).get("exports")
        if d_exports is not None:
            exports_line = (
                f"DECLARED EXPORTS for {d_path} — you may import ONLY these names from {d_path}: "
                f"{d_exports}\n"
            )
        else:
            exports_line = ""
        deps_blocks.append(
            f"=== {d_path} (already generated, DO NOT re-define these symbols) ===\n"
            f"{exports_line}{d_code}"
        )
    deps_context = "\n\n".join(deps_blocks) if deps_blocks else "(no dependencies)"

    manifest_summary = json.dumps(
        [
            {
                "path": e["path"],
                "purpose": e["purpose"],
                "depends_on": e["depends_on"],
                "exports": e.get("exports"),
            }
            for e in manifest
        ],
        indent=2,
    )

    own_exports = entry.get("exports")
    if own_exports is not None and own_exports:
        contract_line = (
            f"REQUIRED EXPORTS for {entry['path']}: this file MUST define EVERY name in "
            f"{own_exports} at top level (as `def`, `class`, or module-level assignment). "
            f"Other names may exist as helpers; these are the contract."
        )
    elif own_exports is not None:
        contract_line = (
            f"REQUIRED EXPORTS for {entry['path']}: this file declares no public exports. "
            f"It is run as a script — nothing outside this file should import from it."
        )
    else:
        contract_line = ""

    system_parts = [
        f"You are a Python code generator for the {info['project_name']} project.",
        "You are generating ONE file as part of a coordinated multi-file project.",
        "",
        "PROJECT CONTEXT:",
        info["raw_context"],
    ]
    if inspections:
        system_parts.extend([
            "",
            "INSPECTED WORKSPACE FILES (head/tail samples — ground schemas and structure on these). "
            "When opening any of these files in your code, use the ABSOLUTE PATH shown in the header, "
            "not the bare filename. The generated script will be run from a temporary directory and "
            "bare filenames will not resolve:",
            inspections,
        ])
    system_parts.extend([
        "",
        "FULL MANIFEST (all files in this multi-file project):",
        manifest_summary,
        "",
        "ALREADY-GENERATED DEPENDENCIES (their source is shown — IMPORT them, do not redefine):",
        deps_context,
        "",
        f"FILE YOU ARE GENERATING NOW: {entry['path']}",
        f"PURPOSE: {entry['purpose']}",
    ])
    if contract_line:
        system_parts.extend(["", contract_line])
    if _is_test_entry(entry):
        system_parts.extend([
            "",
            "SMOKE TEST INSTRUCTIONS (this file is a smoke/test file):",
            "- Do NOT import internal functions from the entry script. The entry script's declared "
            "exports are empty; the cross-module lint will reject any `from <entry> import <name>`.",
            "- Use `subprocess.run([\"python3\", \"<entry>.py\"], capture_output=True, text=True)` to "
            "invoke the entry script, then assert on `result.returncode`, `result.stdout`, or any "
            "output files the entry produced.",
            "- If you genuinely need to unit-test helper functions, those live in a non-entry helper "
            "module with declared exports. Import from THAT module, not from the entry script.",
            "- See the planner system prompt for the canonical subprocess pattern.",
        ])
    if _task_mentions_dates(task):
        system_parts.extend([
            "",
            "DATETIME RULES (the task involves dates/ages/timestamps):",
            "- Use `datetime.now(timezone.utc)` for \"now\". Never `datetime.now()` (tz-naive) or `datetime.utcnow()` (deprecated, tz-naive).",
            "- `datetime.fromisoformat(...)` may return a tz-aware datetime. Both sides of any subtraction must be tz-aware, or Python raises TypeError.",
            "- Import: `from datetime import datetime, timezone`.",
        ])
    system_parts.extend([
        "",
        "CRITICAL OUTPUT FORMAT:",
        f"- Your ENTIRE response IS the raw source code of {entry['path']}. Nothing else.",
        f"- DO NOT prefix with the filename. DO NOT write '{entry['path']}' or '# {entry['path']}' as a header line.",
        "- DO NOT include the source of any OTHER file from the manifest. Those are already written and would conflict.",
        "- DO NOT include markdown fences (```), prose, comments explaining the manifest, or commentary.",
        "- The very first character of your response must be the first character of valid Python source (e.g. 'i' for 'import', 'f' for 'from', 'd' for 'def', '#' for a real comment).",
        "",
        "RULES:",
        f"- Include all imports at top.",
        "- Use stdlib only unless required.",
        f"- All files in the manifest live in the SAME directory. Import dependencies by module name (e.g. `from utils import add` for utils.py).",
        "- Do NOT re-define functions/classes that exist in already-generated dependencies — import them by module name.",
        "- Output must parse as valid Python (no syntax errors) AND import without NameError/ModuleNotFoundError.",
    ])
    return "\n".join(system_parts)


def generate_one_file(entry, manifest, generated_sources, task, info, inspections):
    """Generate one file with up to MAX_RETRIES AST-validated attempts.

    Returns (code:str, attempts:int, err:str). code is None on failure.
    """
    system = _build_per_file_system_prompt(entry, manifest, generated_sources, info, inspections, task=task)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]

    last_err = ""
    last_code = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[builder]   {entry['path']} attempt {attempt}/{MAX_RETRIES}", flush=True)
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages, timeout=PER_LLM_TIMEOUT,
            )
        except Exception as e:
            print(f"[builder]   LLM call failed: {e}", flush=True)
            last_err = f"LLM error: {e}"
            messages.append({"role": "user", "content": f"Previous call failed: {e}. Return the complete file now."})
            continue
        code = clean_code(resp.choices[0].message.content or "")
        code = split_manifest_sections(code, manifest, entry["path"])
        last_code = code
        ok, err = ast_check(code)
        if ok:
            return code, attempt, ""
        last_err = err
        print(f"[builder]   AST fail: {err}", flush=True)
        if attempt < MAX_RETRIES:
            messages.append({"role": "assistant", "content": code})
            messages.append({
                "role": "user",
                "content": (
                    f"That file failed to parse:\n{err}\n\n"
                    f"Return the complete corrected file for '{entry['path']}'. "
                    "Valid Python, no markdown fences, no commentary."
                ),
            })
    return None, MAX_RETRIES, last_err or (f"all {MAX_RETRIES} attempts failed")


# ---------------------------------------------------------------------------
# Cross-file validation (PHASE 3)
# ---------------------------------------------------------------------------

def _collect_top_level_names(tree):
    """Return the set of names a module makes available at its top level.

    Counted: function/class defs, top-level assignments (including unpacking
    and annotated assigns), and names introduced by ``import`` or
    ``from … import`` (so that downstream callers see those re-exports).
    Anything inside if/try/with/loops/functions is intentionally ignored —
    we only model what a static reader can see without executing the module.
    """
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, (ast.Tuple, ast.List)):
                    for elt in t.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name)
    return names


ALLOWED_TOP_LEVEL_CALLS = {
    "logging.getLogger",
    "Path",
    "NewType",
    "os.environ.get",
}

MAIN_GUARD_RETRY_BUDGET = 2


def _call_qualified_name(func_node):
    """Best-effort dotted name for a Call's `.func`. Returns None for chained
    or otherwise non-trivial call expressions we don't want to whitelist."""
    parts = []
    cur = func_node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def find_unguarded_top_level_calls(tree):
    """Return list of dicts describing bare top-level Call expressions.

    A "bare" call is one whose AST node is `ast.Expr` at module scope and whose
    `.value` is `ast.Call`. Calls whose result is assigned (`x = foo()` /
    annotated assigns) are NOT bare — those represent module-level
    initialization rather than work execution. Calls inside if/for/while/try/
    with/FunctionDef/ClassDef are also exempt because we only walk
    `tree.body` (top level), never recurse.

    Calls whose qualified name is in ALLOWED_TOP_LEVEL_CALLS are exempt.
    """
    violations = []
    for node in tree.body:
        if not isinstance(node, ast.Expr):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        qname = _call_qualified_name(node.value.func)
        if qname is not None and qname in ALLOWED_TOP_LEVEL_CALLS:
            continue
        descr = f"{qname}(...)" if qname else "<complex call>"
        violations.append({"lineno": node.lineno, "call": descr})
    return violations


def verify_main_guards(run_dir, manifest_entries, entry_point_path):
    """Confirm each non-entry module has no bare top-level Call expressions.

    Entry point is exempt — its purpose is to be run as a script, so
    top-level work is expected. All other modules are imported by the entry
    (directly or transitively) and must keep import-time side effects out of
    module scope so dry-import doesn't trigger application logic.

    Returns (ok, results). Results is a list of per-file dicts with keys
    `path`, `status` (one of "ok", "violations", "exempt_entry",
    "file_missing", "parse_error"), and `violations` (the list from
    `find_unguarded_top_level_calls`).
    """
    results = []
    all_ok = True
    for entry in manifest_entries:
        path = entry["path"]
        if path == entry_point_path:
            results.append({"path": path, "status": "exempt_entry", "violations": []})
            continue
        p = run_dir / path
        if not p.exists():
            results.append({"path": path, "status": "file_missing", "violations": []})
            all_ok = False
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError as e:
            results.append({
                "path": path, "status": "parse_error", "violations": [],
                "error": f"SyntaxError: {e.msg} at line {e.lineno}",
            })
            all_ok = False
            continue
        violations = find_unguarded_top_level_calls(tree)
        if violations:
            results.append({"path": path, "status": "violations", "violations": violations})
            all_ok = False
        else:
            results.append({"path": path, "status": "ok", "violations": []})
    return all_ok, results


def regenerate_for_main_guards(entry, manifest, generated_sources, task, info, inspections, prior_code, violations):
    """One LLM round-trip to fix unguarded top-level calls in a non-entry module.

    Returns (code|None, err). The caller decides whether to retry or hard-fail.
    """
    system = _build_per_file_system_prompt(entry, manifest, generated_sources, info, inspections, task=task)
    violation_lines = ", ".join(f"line {v['lineno']}: {v['call']}" for v in violations) or "(none captured)"
    corrective = (
        f"Your previous version of {entry['path']} had non-trivial function "
        f"call(s) at module scope: {violation_lines}. "
        "When this module is imported by another file, those calls execute "
        "during import — that is a bug. Wrap any executable code in "
        '`if __name__ == "__main__":` so that importing this module does not '
        "execute it. Helper functions and class definitions are fine at module "
        "scope; only the invocation of work needs to be guarded.\n\n"
        "Return the complete corrected file for "
        f"'{entry['path']}'. Valid Python, no markdown fences, no commentary."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
        {"role": "assistant", "content": prior_code},
        {"role": "user", "content": corrective},
    ]
    try:
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, timeout=PER_LLM_TIMEOUT,
        )
    except Exception as e:
        return None, f"LLM error: {e}"
    code = clean_code(resp.choices[0].message.content or "")
    code = split_manifest_sections(code, manifest, entry["path"])
    ok, err = ast_check(code)
    if not ok:
        return None, err
    return code, ""


def verify_export_contracts(run_dir, manifest_entries):
    """Confirm each manifest entry's declared `exports` are actually defined.

    Returns (ok, results) where results is a list of dicts, one per file. Each
    has keys: path, status ("ok" | "missing" | "no_contract" | "parse_error"),
    declared (the list of exports), defined (top-level names found in file),
    missing (list of declared names not defined).

    Entries with `exports=None` (older manifest format) are treated as
    "no_contract" — soft-skip, log a warning, no hard fail. This is the
    backward-compat path requested for graceful handling of model regressions.
    """
    results = []
    all_ok = True
    for entry in manifest_entries:
        path = entry["path"]
        declared = entry.get("exports")
        p = run_dir / path
        if not p.exists():
            results.append({"path": path, "status": "file_missing", "declared": declared,
                            "defined": [], "missing": []})
            all_ok = False
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError as e:
            results.append({
                "path": path, "status": "parse_error",
                "error": f"SyntaxError: {e.msg} at line {e.lineno}",
                "declared": declared, "defined": [], "missing": [],
            })
            all_ok = False
            continue
        defined = sorted(_collect_top_level_names(tree))
        if declared is None:
            results.append({"path": path, "status": "no_contract",
                            "declared": None, "defined": defined, "missing": []})
            continue
        missing = [name for name in declared if name not in defined]
        if missing:
            all_ok = False
            results.append({"path": path, "status": "missing", "declared": declared,
                            "defined": defined, "missing": missing})
        else:
            results.append({"path": path, "status": "ok", "declared": declared,
                            "defined": defined, "missing": []})
    return all_ok, results


def _actual_manifest_imports(tree, manifest_stems):
    """For a parsed module, return the set of manifest stems it actually imports
    via `import X` or `from X import ...`. Stdlib and third-party imports are
    not in `manifest_stems` so they are ignored.
    """
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in manifest_stems:
                    imports.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level > 0:
                continue
            source = node.module.split(".")[0]
            if source in manifest_stems:
                imports.add(source)
    return imports


def verify_dep_honesty(run_dir, manifest_entries):
    """Catch import-graph defects the manifest's declared depends_on DAG missed.

    The manifest validator already enforces that `depends_on` forms a DAG.
    But the LLM can emit *generated source* with imports that contradict the
    declared DAG — most commonly cycles between siblings or self-imports.
    Those slip past the manifest validator and crash dry-import with a
    partially-initialized-module error.

    This gate AST-walks every generated file, collects the set of
    manifest-internal modules it actually imports, then:

      * Hard-fails if any file imports itself.
      * Hard-fails if the actual import graph contains a cycle, listing the
        cycle path in the error message.
      * Logs a warning (but does not fail) when a file's actual imports
        include manifest modules not in its declared `depends_on`. The DAG
        may simply be under-declared, which is sloppy but not broken.

    Returns (ok, info) where `info` is a dict with keys:
      - results: list of per-file dicts (path, declared_deps, actual_imports,
                 self_import, undeclared)
      - cycle: list[str] | None  (the cycle path if one was detected)
    """
    manifest_paths = [e["path"] for e in manifest_entries]
    manifest_stems = {Path(p).stem: p for p in manifest_paths}
    results = []
    graph = {}  # stem -> set of stems it imports
    self_imports = []
    parse_errors = []

    for entry in manifest_entries:
        path = entry["path"]
        stem = Path(path).stem
        declared = set(Path(d).stem for d in entry.get("depends_on", []))
        p = run_dir / path
        if not p.exists():
            parse_errors.append({"module": path, "error": "file missing"})
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError as e:
            parse_errors.append({"module": path, "error": f"SyntaxError: {e.msg} at line {e.lineno}"})
            continue
        actual = _actual_manifest_imports(tree, manifest_stems)
        if stem in actual:
            self_imports.append(path)
        graph[stem] = actual - {stem}  # cycle-detect excludes self-edge
        undeclared = sorted(actual - declared - {stem})
        results.append({
            "path": path,
            "declared_deps": sorted(declared),
            "actual_imports": sorted(actual),
            "self_import": stem in actual,
            "undeclared": undeclared,
        })

    info = {"results": results, "cycle": None, "self_imports": self_imports,
            "parse_errors": parse_errors}

    if parse_errors:
        return False, info
    if self_imports:
        return False, info

    # Cycle detection via DFS — find any back-edge, return the cycle path.
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    parent = {}
    cycle = None

    def dfs(start):
        nonlocal cycle
        stack = [(start, iter(sorted(graph.get(start, ()))))]
        color[start] = GRAY
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in color:
                    continue
                if color[nxt] == WHITE:
                    parent[nxt] = node
                    color[nxt] = GRAY
                    stack.append((nxt, iter(sorted(graph.get(nxt, ())))))
                    advanced = True
                    break
                elif color[nxt] == GRAY:
                    # back-edge → cycle node..nxt..node
                    path = [nxt, node]
                    cur = node
                    while cur in parent and parent[cur] != nxt:
                        cur = parent[cur]
                        path.append(cur)
                    path.append(nxt)
                    cycle = list(reversed(path))
                    return True
            if not advanced:
                color[node] = BLACK
                stack.pop()
        return False

    for n in sorted(graph):
        if color[n] == WHITE:
            if dfs(n):
                info["cycle"] = cycle
                return False, info

    return True, info


def static_cross_module_lint(run_dir, manifest_entries):
    """Catch undefined-symbol imports across generated modules before dry-import.

    Source of truth for "what may be imported from module X":

      1. If X declared an `exports` list (Fix A contracts), only those names
         may be imported. Importing a file-internal helper that wasn't exported
         is a contract violation and is flagged.
      2. If X has `exports=None` (older manifest, missing field), fall back to
         the historical behavior: any top-level name in X is importable.

    Scope is otherwise unchanged from the original lint: stdlib and third-party
    imports are ignored, no signature/type checking, star imports are flagged.

    Returns (ok: bool, errors: list[dict]).
    """
    manifest_paths = [e["path"] for e in manifest_entries]
    manifest_stems = {Path(p).stem: p for p in manifest_paths}
    declared_exports = {Path(e["path"]).stem: e.get("exports") for e in manifest_entries}
    module_names = {}
    parse_errors = []
    trees = {}

    for fname in manifest_paths:
        p = run_dir / fname
        if not p.exists():
            parse_errors.append({"module": fname, "error": "file missing"})
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError as e:
            parse_errors.append({"module": fname, "error": f"SyntaxError: {e.msg} at line {e.lineno}"})
            continue
        trees[fname] = tree
        module_names[Path(fname).stem] = _collect_top_level_names(tree)

    if parse_errors:
        return False, parse_errors

    def importable_from(stem):
        declared = declared_exports.get(stem)
        if declared is None:
            return module_names[stem]
        return set(declared)

    def reason_prefix(stem):
        declared = declared_exports.get(stem)
        return "declared exports" if declared is not None else "top-level names"

    errors = []
    for fname, tree in trees.items():
        local_alias = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in manifest_stems:
                        local_alias[alias.asname or top] = top
            elif isinstance(node, ast.ImportFrom):
                if node.module is None or node.level > 0:
                    continue
                source = node.module.split(".")[0]
                if source not in manifest_stems:
                    continue
                allowed = importable_from(source)
                for alias in node.names:
                    if alias.name == "*":
                        errors.append({
                            "module": fname,
                            "missing": "*",
                            "from": manifest_stems[source],
                            "reason": f"star import from {source} defeats static checking",
                        })
                        continue
                    if alias.name not in allowed:
                        errors.append({
                            "module": fname,
                            "missing": alias.name,
                            "from": manifest_stems[source],
                            "reason": (
                                f"{manifest_stems[source]} {reason_prefix(source)} "
                                f"do not include {alias.name!r}"
                            ),
                        })

        if not local_alias:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id not in local_alias:
                    continue
                source = local_alias[node.value.id]
                allowed = importable_from(source)
                if node.attr not in allowed:
                    errors.append({
                        "module": fname,
                        "missing": node.attr,
                        "from": manifest_stems[source],
                        "reason": (
                            f"{manifest_stems[source]} {reason_prefix(source)} "
                            f"do not include {node.attr!r} (accessed as {node.value.id}.{node.attr})"
                        ),
                    })

    return len(errors) == 0, errors


def dry_import(filename, run_dir):
    modname = Path(filename).stem
    try:
        r = subprocess.run(
            [sys.executable, "-c", f"import {modname}"],
            cwd=str(run_dir),
            capture_output=True, text=True,
            timeout=DRY_IMPORT_TIMEOUT,
        )
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"dry-import timed out after {DRY_IMPORT_TIMEOUT}s"


def _run_script(filename, run_dir, label):
    try:
        r = subprocess.run(
            [sys.executable, str(filename)],
            cwd=str(run_dir),
            capture_output=True, text=True,
            timeout=SMOKE_TEST_TIMEOUT,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"{label} timed out after {SMOKE_TEST_TIMEOUT}s"


def execute_entry(filename, run_dir):
    return _run_script(filename, run_dir, "entry execution")


def run_smoke(filename, run_dir):
    return _run_script(filename, run_dir, "smoke test")


# ---------------------------------------------------------------------------
# Commit (PHASE 4)
# ---------------------------------------------------------------------------

def short_summary(task, max_len=60):
    cleaned = re.sub(r"\s+", " ", task).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def git_commit_and_push(run_dir, task):
    """Commit + push only when something non-ignored actually changed.

    Run artifacts live under tools/generated/run_*/ which is gitignored, so
    ordinary runs have nothing to commit. We never --force past the ignore;
    git add -A respects it. The audit trail is logs/oc_build_<ts>.json.
    """
    msg = f"ocb multifile: {short_summary(task)}"
    try:
        status = subprocess.run(
            ["git", "-C", str(WORKSPACE), "status", "--porcelain"],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        return {
            "status": "failed",
            "message": msg,
            "stderr": (e.stderr or "")[-2000:],
            "stdout": (e.stdout or "")[-2000:],
        }

    if not status.stdout.strip():
        return {
            "status": "skipped",
            "reason": "no changes to commit",
            "run_dir": str(run_dir),
        }

    try:
        subprocess.run(
            ["git", "-C", str(WORKSPACE), "add", "-A"],
            check=True, capture_output=True, text=True,
        )
        # Re-check: gitignore might have hidden everything we just tried to add.
        staged = subprocess.run(
            ["git", "-C", str(WORKSPACE), "diff", "--cached", "--name-only"],
            check=True, capture_output=True, text=True,
        )
        if not staged.stdout.strip():
            return {
                "status": "skipped",
                "reason": "no trackable changes (all paths ignored)",
                "run_dir": str(run_dir),
            }
        subprocess.run(
            ["git", "-C", str(WORKSPACE), "commit", "-m", msg],
            check=True, capture_output=True, text=True,
        )
        h = subprocess.run(
            ["git", "-C", str(WORKSPACE), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        push = subprocess.run(
            ["git", "-C", str(WORKSPACE), "push", "origin", GIT_BRANCH],
            capture_output=True, text=True,
        )
        return {
            "status": "ok",
            "hash": h,
            "message": msg,
            "push_rc": push.returncode,
            "push_stderr": push.stderr[-2000:],
        }
    except subprocess.CalledProcessError as e:
        return {
            "status": "failed",
            "message": msg,
            "stderr": (e.stderr or "")[-2000:],
            "stdout": (e.stdout or "")[-2000:],
        }


# ---------------------------------------------------------------------------
# State dump
# ---------------------------------------------------------------------------

def dump_state(state, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(state, indent=2, default=str))
    print(f"[builder] State dumped to {log_path}", flush=True)


def archive_run_dir(run_dir):
    """Copy run_dir to logs/run_archive/<name>/ for forensic preservation.

    Intended for `atexit` so it runs on every termination path — PASS,
    PARTIAL, FAIL, hard sys.exit, even uncaught exceptions. The original
    run_dir is left in place. After copying, prune to the RUN_ARCHIVE_MAX
    most-recent snapshots by mtime so the archive doesn't grow unbounded.

    Failure-tolerant: any exception here is swallowed with a warning. An
    archive failure must never break a real run's exit semantics.
    """
    try:
        if not run_dir.exists():
            return
        RUN_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        dst = RUN_ARCHIVE_DIR / run_dir.name
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(run_dir, dst)
        snapshots = [p for p in RUN_ARCHIVE_DIR.iterdir() if p.is_dir()]
        snapshots.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in snapshots[RUN_ARCHIVE_MAX:]:
            shutil.rmtree(stale, ignore_errors=True)
    except Exception as e:
        print(f"[builder] WARNING: archive_run_dir failed (non-fatal): {e}", flush=True)


# ---------------------------------------------------------------------------
# Legacy single-file fallback (preserves pre-multifile behavior)
# ---------------------------------------------------------------------------

def build_single_legacy(task, info, run_dir, inspections, state, log_path):
    """Run the original single-file pipeline (runtime self-heal, no auto-commit)."""
    print("[builder] === LEGACY SINGLE-FILE MODE ===", flush=True)
    context = info["raw_context"]
    system_parts = [
        f"You are a Python code generator for the {info['project_name']} project.",
        "",
        "PROJECT CONTEXT:",
        context,
    ]
    if inspections:
        system_parts.extend([
            "",
            "INSPECTED WORKSPACE FILES (head and tail samples — use these to ground schemas, columns, and structure; do not guess). "
            "When opening any of these files in your code, use the ABSOLUTE PATH shown in the header, "
            "not the bare filename. The generated script will be run from a temporary directory and "
            "bare filenames will not resolve:",
            inspections,
        ])
    system_parts.extend([
        "",
        "RULES:",
        "- Output ONE complete runnable Python file.",
        "- Include all imports at top.",
        "- Use stdlib only unless required.",
        "- Add print() statements for progress.",
        "- No markdown fences, no explanation — just code.",
    ])
    system = "\n".join(system_parts)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    slug = re.sub(r"[^a-z0-9]+", "_", task.lower())[:40].strip("_") or "script"
    out_file = run_dir / f"{slug}.py"

    results = []
    last_rc = 1
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n[builder] Attempt {attempt}/{MAX_RETRIES}: generating...", flush=True)
        try:
            resp = client.chat.completions.create(model=MODEL, messages=messages, timeout=PER_LLM_TIMEOUT)
        except Exception as e:
            print(f"[builder] LLM call failed: {e}", flush=True)
            results.append({"attempt": attempt, "status": "llm_error", "err": str(e)})
            continue
        code = clean_code(resp.choices[0].message.content or "")
        out_file.write_text(code)
        print(f"[builder] Wrote: {out_file}", flush=True)
        print(f"[builder] Running...\n{'=' * 50}", flush=True)
        try:
            r = subprocess.run(
                [sys.executable, str(out_file)],
                capture_output=True, text=True, timeout=LEGACY_RUN_TIMEOUT,
            )
            rc, stdout, stderr = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            rc, stdout, stderr = -1, "", f"TimeoutExpired after {LEGACY_RUN_TIMEOUT}s: {e}"
        print(stdout, flush=True)
        last_rc = rc
        if rc == 0:
            print(f"{'=' * 50}\n[builder] SUCCESS on attempt {attempt}", flush=True)
            results.append({"attempt": attempt, "status": "ok"})
            state["phases"]["legacy"] = {"status": "ok", "out_file": str(out_file), "results": results}
            dump_state(state, log_path)
            return 0
        print(f"{'=' * 50}\n[builder] FAILED (exit {rc})\nSTDERR:\n{stderr}", flush=True)
        results.append({"attempt": attempt, "status": "run_failed", "rc": rc, "stderr": stderr[-2000:]})
        if attempt < MAX_RETRIES:
            print(f"[builder] Asking model for fix...", flush=True)
            messages.append({"role": "assistant", "content": code})
            messages.append({"role": "user", "content": f"The script failed:\n\n{stderr}\n\nFix the bug and return the complete corrected script. No explanation."})
    state["phases"]["legacy"] = {"status": "failed", "out_file": str(out_file), "results": results}
    dump_state(state, log_path)
    return last_rc or 1


# ---------------------------------------------------------------------------
# Main entry — multi-file pipeline with legacy fallback
# ---------------------------------------------------------------------------

def build(task, project=None):
    info = load_project(resolve_slug(project))
    print(f"[builder] Project: {info['project_name']} ({info['project_slug']})", flush=True)
    print(f"[builder] build() called with task: {task}", flush=True)

    ts = int(time.time())
    run_id = f"run_{ts}"
    run_dir = GENERATED_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"oc_build_{ts}.json"
    # Archive the run dir on any termination path (PASS / PARTIAL / FAIL /
    # sys.exit / uncaught exception). The atexit handler is failure-tolerant
    # — see archive_run_dir docstring.
    atexit.register(archive_run_dir, run_dir)
    start = time.time()

    state = {
        "run_id": run_id,
        "task": task,
        "project": info["project_slug"],
        "started_at": datetime.now().isoformat(),
        "run_dir": str(run_dir),
        "log_path": str(log_path),
        "phases": {},
    }

    context = info["raw_context"]
    inspections = gather_inspections(task, context)
    state["inspections_collected"] = bool(inspections)

    def check_cap(label):
        elapsed = time.time() - start
        if elapsed > RUN_CAP_SECONDS:
            state["aborted"] = {"at": label, "elapsed": round(elapsed, 2), "cap": RUN_CAP_SECONDS}
            dump_state(state, log_path)
            print(f"[builder] ABORT: exceeded {RUN_CAP_SECONDS}s run cap at {label}", flush=True)
            sys.exit(1)

    # ---- PHASE 1: MANIFEST ----
    print("[builder] === PHASE 1: MANIFEST ===", flush=True)
    raw_manifest, parse_err = get_manifest(task, context, inspections)
    if raw_manifest is None:
        print(f"[builder] WARNING: manifest planner unusable ({parse_err}). Degrading to legacy single-file mode.", flush=True)
        state["phases"]["manifest"] = {"status": "degraded", "reason": parse_err or "planner returned no JSON"}
        rc = build_single_legacy(task, info, run_dir, inspections, state, log_path)
        sys.exit(rc)

    errors, topo_order = validate_manifest(raw_manifest)
    if errors:
        print(f"[builder] WARNING: manifest validation failed: {errors}. Degrading to legacy single-file mode.", flush=True)
        state["phases"]["manifest"] = {
            "status": "degraded",
            "reason": "validation failed",
            "errors": errors,
            "raw": raw_manifest,
        }
        rc = build_single_legacy(task, info, run_dir, inspections, state, log_path)
        sys.exit(rc)

    state["phases"]["manifest"] = {
        "status": "ok",
        "files": topo_order,
        "topo_order": [e["path"] for e in topo_order],
    }
    print(f"[builder] Manifest valid. Topo order: {[e['path'] for e in topo_order]}", flush=True)
    check_cap("post-manifest")

    # ---- PHASE 2: TOPOLOGICAL GENERATION ----
    print("[builder] === PHASE 2: TOPOLOGICAL GENERATION ===", flush=True)
    generated_sources = {}
    gen_results = []
    for entry in topo_order:
        check_cap(f"pre-gen[{entry['path']}]")
        code, attempts, err = generate_one_file(
            entry, topo_order, generated_sources, task, info, inspections,
        )
        if code is None:
            gen_results.append({"path": entry["path"], "status": "failed", "attempts": attempts, "err": err})
            state["phases"]["topo_gen"] = {"status": "failed", "results": gen_results}
            dump_state(state, log_path)
            print(f"[builder] FAILED: could not generate {entry['path']} after {attempts} attempts: {err}", flush=True)
            sys.exit(1)
        target = run_dir / entry["path"]
        target.write_text(code)
        generated_sources[entry["path"]] = code
        gen_results.append({
            "path": entry["path"],
            "status": "ok",
            "attempts": attempts,
            "abs_path": str(target),
        })
        print(f"[builder]   wrote {target} (after {attempts} attempt(s))", flush=True)
    state["phases"]["topo_gen"] = {"status": "ok", "results": gen_results}

    # ---- PHASE 3: CROSS-FILE VALIDATION ----
    print("[builder] === PHASE 3: CROSS-FILE VALIDATION ===", flush=True)
    check_cap("pre-cross-file")
    entry_point = find_entry_point(topo_order)
    print(f"[builder] Entry point: {entry_point['path']}", flush=True)

    manifest_paths = [e["path"] for e in topo_order]
    contract_ok, contract_results = verify_export_contracts(run_dir, topo_order)
    cross = {
        "entry_point": entry_point["path"],
        "contracts": {"ok": contract_ok, "results": contract_results},
        "main_guards": None,
        "dep_honesty": None,
        "static_lint": None,
        "dry_import": None,
        "entry_execution": None,
        "smoke_tests": [],
    }
    if not contract_ok:
        state["phases"]["cross_file"] = {"status": "failed", **cross}
        dump_state(state, log_path)
        print("[builder] FAILED: contract verification:", flush=True)
        for r in contract_results:
            if r["status"] == "missing":
                print(f"  - {r['path']}: declared exports not defined: {r['missing']}", flush=True)
            elif r["status"] == "file_missing":
                print(f"  - {r['path']}: file missing", flush=True)
            elif r["status"] == "parse_error":
                print(f"  - {r['path']}: {r['error']}", flush=True)
        sys.exit(1)
    no_contract_files = [r["path"] for r in contract_results if r["status"] == "no_contract"]
    if no_contract_files:
        print(
            f"[builder] WARNING: contracts missing for {no_contract_files} — "
            f"falling back to top-level-names lint for these files.",
            flush=True,
        )
    print(f"[builder] Contract verification OK ({len(manifest_paths)} module(s))", flush=True)

    guard_attempts = {e["path"]: 0 for e in topo_order}
    cross["main_guard_attempts"] = guard_attempts
    while True:
        check_cap("main-guard-gate")
        guard_ok, guard_results = verify_main_guards(run_dir, topo_order, entry_point["path"])
        if guard_ok:
            break
        violating = [r for r in guard_results if r["status"] == "violations"]
        progressed = False
        for r in violating:
            path = r["path"]
            if guard_attempts[path] >= MAIN_GUARD_RETRY_BUDGET:
                cross["main_guards"] = {"ok": False, "results": guard_results}
                state["phases"]["cross_file"] = {"status": "failed", **cross}
                dump_state(state, log_path)
                print(
                    f"[builder] FAILED: {path} still has unguarded top-level calls "
                    f"after {MAIN_GUARD_RETRY_BUDGET} retry attempt(s):",
                    flush=True,
                )
                for v in r["violations"]:
                    print(f"    line {v['lineno']}: {v['call']}", flush=True)
                sys.exit(1)
            guard_attempts[path] += 1
            print(
                f"[builder]   Main-guard violation in {path} (attempt {guard_attempts[path]}/"
                f"{MAIN_GUARD_RETRY_BUDGET}): "
                f"{', '.join(v['call'] for v in r['violations'])}",
                flush=True,
            )
            entry_obj = next(e for e in topo_order if e["path"] == path)
            new_code, err = regenerate_for_main_guards(
                entry_obj, topo_order, generated_sources, task, info, inspections,
                generated_sources[path], r["violations"],
            )
            if new_code is None:
                print(f"[builder]     regeneration failed: {err}", flush=True)
                continue
            (run_dir / path).write_text(new_code)
            generated_sources[path] = new_code
            progressed = True
        if not progressed:
            cross["main_guards"] = {"ok": False, "results": guard_results}
            state["phases"]["cross_file"] = {"status": "failed", **cross}
            dump_state(state, log_path)
            print("[builder] FAILED: main-guard retries did not produce any new code", flush=True)
            sys.exit(1)
        # re-verify contracts for any regenerated file before looping
        contract_ok, contract_results = verify_export_contracts(run_dir, topo_order)
        cross["contracts"] = {"ok": contract_ok, "results": contract_results}
        if not contract_ok:
            state["phases"]["cross_file"] = {"status": "failed", **cross}
            dump_state(state, log_path)
            print("[builder] FAILED: contract verification after main-guard retry:", flush=True)
            for r in contract_results:
                if r["status"] == "missing":
                    print(f"  - {r['path']}: declared exports not defined: {r['missing']}", flush=True)
            sys.exit(1)
    cross["main_guards"] = {"ok": True, "results": guard_results}
    print(f"[builder] Main-guard check OK ({len(topo_order)} module(s))", flush=True)

    check_cap("dep-honesty-gate")
    dep_ok, dep_info = verify_dep_honesty(run_dir, topo_order)
    cross["dep_honesty"] = {
        "ok": dep_ok,
        "cycle": dep_info.get("cycle"),
        "self_imports": dep_info.get("self_imports", []),
        "results": dep_info.get("results", []),
    }
    if not dep_ok:
        state["phases"]["cross_file"] = {"status": "failed", **cross}
        dump_state(state, log_path)
        if dep_info.get("self_imports"):
            print("[builder] FAILED: dep-honesty — self-import in:", flush=True)
            for s in dep_info["self_imports"]:
                print(f"    - {s}", flush=True)
        if dep_info.get("cycle"):
            print(f"[builder] FAILED: dep-honesty — import cycle: {' -> '.join(dep_info['cycle'])}", flush=True)
        if dep_info.get("parse_errors"):
            for pe in dep_info["parse_errors"]:
                print(f"[builder] FAILED: dep-honesty parse error: {pe['module']}: {pe['error']}", flush=True)
        sys.exit(1)
    # warn (not fail) on undeclared manifest-internal imports
    for r in dep_info.get("results", []):
        if r["undeclared"]:
            print(
                f"[builder] WARNING: {r['path']} imports {r['undeclared']} but did not declare them "
                f"in depends_on (declared={r['declared_deps']}). DAG accepted, but the manifest is "
                "under-declared.",
                flush=True,
            )
    print(f"[builder] Dep-honesty check OK ({len(topo_order)} module(s))", flush=True)

    lint_ok, lint_errors = static_cross_module_lint(run_dir, topo_order)
    cross["static_lint"] = {"ok": lint_ok, "errors": lint_errors}
    if not lint_ok:
        state["phases"]["cross_file"] = {"status": "failed", **cross}
        dump_state(state, log_path)
        print("[builder] FAILED: static cross-module lint:", flush=True)
        for e in lint_errors:
            print(f"  - {e['module']}: {e['reason']}", flush=True)
        sys.exit(1)
    print(f"[builder] Static lint OK ({len(manifest_paths)} module(s))", flush=True)

    ok, di_stdout, di_stderr = dry_import(entry_point["path"], run_dir)
    cross["dry_import"] = {"ok": ok, "stdout": di_stdout[-2000:], "stderr": di_stderr[-2000:]}
    if not ok:
        state["phases"]["cross_file"] = {"status": "failed", **cross}
        dump_state(state, log_path)
        print(f"[builder] FAILED: dry-import of {entry_point['path']} failed:\n{di_stderr}", flush=True)
        sys.exit(1)
    print(f"[builder] Dry-import OK", flush=True)

    check_cap(f"execute[{entry_point['path']}]")
    ex_rc, ex_stdout, ex_stderr = execute_entry(entry_point["path"], run_dir)
    cross["entry_execution"] = {
        "rc": ex_rc,
        "stdout": ex_stdout[-2000:],
        "stderr": ex_stderr[-2000:],
    }
    if ex_rc != 0:
        state["phases"]["cross_file"] = {"status": "failed", **cross}
        dump_state(state, log_path)
        print(
            f"[builder] FAILED: entry-point {entry_point['path']} exited {ex_rc}:\n{ex_stderr}",
            flush=True,
        )
        sys.exit(1)
    print(f"[builder] Entry execution OK", flush=True)

    smoke_files = [
        e for e in topo_order
        if "test" in e["purpose"].lower() or "smoke" in e["purpose"].lower()
    ]
    for sf in smoke_files:
        check_cap(f"smoke[{sf['path']}]")
        rc, sout, serr = run_smoke(sf["path"], run_dir)
        cross["smoke_tests"].append({
            "path": sf["path"], "rc": rc,
            "stdout": sout[-2000:], "stderr": serr[-2000:],
        })
        if rc != 0:
            state["phases"]["cross_file"] = {"status": "failed", **cross}
            dump_state(state, log_path)
            print(f"[builder] FAILED: smoke test {sf['path']} exited {rc}:\n{serr}", flush=True)
            sys.exit(1)
        print(f"[builder] Smoke test {sf['path']} OK", flush=True)
    state["phases"]["cross_file"] = {"status": "ok", **cross}

    # ---- PHASE 4: COMMIT ----
    print("[builder] === PHASE 4: COMMIT ===", flush=True)
    commit_info = git_commit_and_push(run_dir, task)
    state["phases"]["commit"] = commit_info
    if commit_info["status"] == "failed":
        dump_state(state, log_path)
        print(f"[builder] FAILED: git commit/push failed: {commit_info.get('stderr', '')}", flush=True)
        sys.exit(1)
    if commit_info["status"] == "skipped":
        print(f"[builder] Commit skipped: {commit_info.get('reason', '')}", flush=True)

    state["finished_at"] = datetime.now().isoformat()
    state["elapsed_seconds"] = round(time.time() - start, 2)
    dump_state(state, log_path)
    if commit_info["status"] == "ok":
        tail = f"Commit: {commit_info.get('hash', '?')[:12]}"
    else:
        tail = f"Commit: skipped ({commit_info.get('reason', '')})"
    print(
        f"[builder] DONE. {len(topo_order)} file(s) generated. {tail}",
        flush=True,
    )
    return run_dir, 0


print(f"[builder] __name__ = {__name__}", flush=True)
if __name__ == "__main__":
    print(f"[builder] argv = {sys.argv}", flush=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None,
                        help="Project slug (default: env OPENCLAW_PROJECT, then 'nexadose')")
    parser.add_argument("task", nargs="+", help="Task description")
    args = parser.parse_args()
    build(" ".join(args.task), project=args.project)
