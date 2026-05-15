#!/usr/bin/env python3
import sys, subprocess, re
from pathlib import Path
from openai import OpenAI

print("[builder] script loaded", flush=True)

WORKSPACE = Path("/root/.openclaw/workspace")
PROJECTS_DIR = WORKSPACE / "projects"
GENERATED_DIR = WORKSPACE / "tools" / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
MODEL = "qwen2.5-coder:32b"
MAX_RETRIES = 3

def load_context(project_name):
    ctx_file = PROJECTS_DIR / f"{project_name}.md"
    return ctx_file.read_text() if ctx_file.exists() else ""

def clean_code(raw):
    return re.sub(r"^```python\n?|^```\n?|```$", "", raw, flags=re.MULTILINE).strip()

def generate(messages):
    resp = client.chat.completions.create(model=MODEL, messages=messages, timeout=300)
    return clean_code(resp.choices[0].message.content)

def run_code(path):
    r = subprocess.run(["python3", str(path)], capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr

def build(task, project="nexadose"):
    print(f"[builder] build() called with task: {task}", flush=True)
    context = load_context(project)
    system = "You are a Python code generator for the Nexadose project.\n\nPROJECT CONTEXT:\n" + context + "\n\nRULES:\n- Output ONE complete runnable Python file.\n- Include all imports at top.\n- Use stdlib only unless required.\n- Add print() statements for progress.\n- No markdown fences, no explanation — just code."
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
