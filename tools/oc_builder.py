#!/usr/bin/env python3
import sys, subprocess, re, json
from pathlib import Path
from openai import OpenAI

print("[builder] script loaded", flush=True)

WORKSPACE = Path("/root/.openclaw/workspace")
WORKSPACE_RESOLVED = WORKSPACE.resolve()
PROJECTS_DIR = WORKSPACE / "projects"
GENERATED_DIR = WORKSPACE / "tools" / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
MODEL = "qwen2.5-coder:32b"
MAX_RETRIES = 3

INSPECTION_EXTS = {".csv", ".md", ".json", ".yaml", ".yml", ".txt", ".tsv", ".sh", ".py"}
INSPECTION_SUBDIRS = ("projects", "scripts", "tools", "tasks")
MAX_INSPECT_FILES = 5
HEAD_LINES = 30
TAIL_LINES = 30
MAX_CANDIDATES = 80
MAX_SAMPLE_BYTES = 12000

def load_context(project_name):
    ctx_file = PROJECTS_DIR / f"{project_name}.md"
    return ctx_file.read_text() if ctx_file.exists() else ""

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
            timeout=300,
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
        samples.append(f"=== {rel} ===\n{sample}")
    return "\n\n".join(samples)

def generate(messages):
    resp = client.chat.completions.create(model=MODEL, messages=messages, timeout=300)
    return clean_code(resp.choices[0].message.content)

def run_code(path):
    r = subprocess.run(["python3", str(path)], capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr

def build(task, project="nexadose"):
    print(f"[builder] build() called with task: {task}", flush=True)
    context = load_context(project)
    inspections = gather_inspections(task, context)
    system_parts = [
        "You are a Python code generator for the Nexadose project.",
        "",
        "PROJECT CONTEXT:",
        context,
    ]
    if inspections:
        system_parts.extend([
            "",
            "INSPECTED WORKSPACE FILES (head and tail samples — use these to ground schemas, columns, and structure; do not guess):",
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
    slug = re.sub(r"[^a-z0-9]+", "_", task.lower())[:40].strip("_")
    out_file = GENERATED_DIR / f"{slug}.py"

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n[builder] Attempt {attempt}/{MAX_RETRIES}: generating...", flush=True)
        code = generate(messages)
        out_file.write_text(code)
        print(f"[builder] Wrote: {out_file}", flush=True)
        print(f"[builder] Running...\n{'='*50}", flush=True)
        rc, stdout, stderr = run_code(out_file)
        print(stdout, flush=True)
        if rc == 0:
            print(f"{'='*50}\n[builder] SUCCESS on attempt {attempt}", flush=True)
            return out_file, 0
        print(f"{'='*50}\n[builder] FAILED (exit {rc})\nSTDERR:\n{stderr}", flush=True)
        if attempt < MAX_RETRIES:
            print(f"[builder] Asking model for fix...", flush=True)
            messages.append({"role": "assistant", "content": code})
            messages.append({"role": "user", "content": f"The script failed:\n\n{stderr}\n\nFix the bug and return the complete corrected script. No explanation."})
    return out_file, rc

print(f"[builder] __name__ = {__name__}", flush=True)
if __name__ == "__main__":
    print(f"[builder] argv = {sys.argv}", flush=True)
    if len(sys.argv) < 2:
        print("Usage: oc_builder.py 'task'")
        sys.exit(1)
    build(" ".join(sys.argv[1:]))
