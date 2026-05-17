#!/usr/bin/env python3
"""
OpenClaw Autopilot: goal-driven edit -> verify -> commit loop.

Design:
- NO unified diffs / git apply
- Model returns deterministic edit ops in JSON
- Ops are applied only to allowlisted, in-repo files
- Verify runs after changes
- Non-artifact changes are committed on success
- Every iteration is logged to JSONL
- Smart error classification for adaptive retry strategies

Hard guardrails:
- Never modify canonical CSVs
- Never modify canonical builder scripts
- Never modify generated artifacts
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------- Guardrails ----------
IMMUTABLE_PATHS = {
    "WORK_ITEMS_REGISTER.canonical.v4.csv",
    "scripts/build_work_items_canonical_v4.py",
}

ARTIFACT_PATTERNS = [
    r"^WORK_ITEMS_REGISTER\.upgraded\.v4_1\.csv$",
    r"^logs/next_step_upgrades\.v4_1\.jsonl$",
    r"\.pyc$",
    r"(^|/)__pycache__(/|$)",
    r"\.bak(\.|$)",
    r"^tools/_oc_last\.patch$",
    r"^tools/_oc_last_model_output\.txt$",
]

DEFAULT_ALLOWED_EDIT_PATHS = {
    "tools/oc_autopilot.py",
    "scripts/upgrade_next_steps_from_bodies_v4_1.py",
    "scripts/verify_v4_1.sh",
    "scripts/run_v4_1_full.sh",
}

DEFAULT_SYSTEM_RULES = [
    "You are editing a codebase for deterministic email->work-item processing.",
    "Never hallucinate file contents. Only propose edits using the provided file snippets.",
    "Do not suggest changes to immutable files.",
    "Prefer minimal deterministic logic only. No LLM extraction.",
    "Return ONLY valid JSON for the edit plan.",
    "To create a new file, use op='create' with a 'content' field and any path under the workspace.",
]

# ---------- Retry strategies (from error classification) ----------
RETRY_STRATEGY_MAP = {
    "syntax_error": "Focus ONLY on fixing Python syntax. Do not refactor.",
    "patch_conflict": "Conflict detected. Request fresh snippet before modifying.",
    "allowlist_violation": "Adjust command to comply strictly with allowlist.",
    "guardrail_violation": "Do NOT attempt modification. Escalate to NEED_USER.",
    "runtime_traceback": "Analyze traceback_tail only. Minimal fix at failing line.",
    "verification_failure": "Verification failed. Apply smallest possible corrective change.",
    "unknown": "Apply minimal deterministic fix.",
}

# ---------- Data ----------
@dataclass
class EditOp:
    path: str
    op: str  # replace | append
    find: Optional[str] = None
    replace: Optional[str] = None
    append: Optional[str] = None
    count: Optional[int] = None
    content: Optional[str] = None

@dataclass
class CmdResult:
    cmd: str
    returncode: int
    stdout: str
    stderr: str


# ---------- Error classification ----------
def _tail_lines(text: str, max_lines: int = 60, max_chars: int = 12000) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
    return tail


def _extract_traceback_tail(text: str, max_lines: int = 60, max_chars: int = 12000) -> Optional[str]:
    marker = "Traceback (most recent call last):"
    if not text:
        return None
    idx = text.rfind(marker)
    if idx == -1:
        return None
    tb = text[idx:]
    return _tail_lines(tb, max_lines=max_lines, max_chars=max_chars) or None


def extract_error_summary(results: List[CmdResult]) -> Optional[dict]:
    for r in results:
        if r.returncode != 0:
            preferred = r.stderr or r.stdout or ""
            tail = _tail_lines(preferred)
            tb_tail = _extract_traceback_tail(preferred)
            return {
                "failing_cmd": r.cmd,
                "returncode": r.returncode,
                "tail": tail,
                "traceback_tail": tb_tail,
            }
    return None


def classify_error(error_summary: Optional[dict]) -> Optional[str]:
    if not error_summary:
        return None
    tail = (error_summary.get("tail") or "") + "\n" + (error_summary.get("traceback_tail") or "")
    tail_lower = tail.lower()
    if "syntaxerror" in tail_lower:
        return "syntax_error"
    if "patch failed" in tail_lower or "corrupt patch" in tail_lower:
        return "patch_conflict"
    if "command not allowlisted" in tail_lower:
        return "allowlist_violation"
    if "forbidden" in tail_lower:
        return "guardrail_violation"
    if "traceback (most recent call last)" in tail_lower:
        return "runtime_traceback"
    if error_summary.get("returncode", 0) != 0:
        return "verification_failure"
    return "unknown"


# ---------- Helpers ----------
def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sh(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, text=True, capture_output=True, check=check)


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def get_openai_client():
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        raise RuntimeError("openai python package not available in this environment") from e
    return OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")


def get_repo_root() -> Path:
    p = Path.cwd().resolve()
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    raise RuntimeError("Could not find .git repo root; run inside the workspace repo.")


def is_immutable(path: str) -> bool:
    return path.lstrip("./") in IMMUTABLE_PATHS


def looks_like_artifact(path: str) -> bool:
    norm = path.lstrip("./")
    return any(re.search(pat, norm) for pat in ARTIFACT_PATTERNS)


def is_allowed_edit_path(path: str, allowed_edit_paths: set) -> bool:
    return path.lstrip("./") in allowed_edit_paths


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_clean_worktree(strict: bool = False) -> None:
    r = sh("git status --porcelain", check=True)
    dirty = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if strict and dirty:
        raise RuntimeError(
            "Working tree is not clean. Refusing to run in strict mode.\n"
            + "\n".join(dirty)
        )


def get_context_snippets(root: Path, paths: List[str], max_chars: int = 18000) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rel in paths:
        rel = rel.lstrip("./")
        if is_immutable(rel) or looks_like_artifact(rel):
            continue
        p = (root / rel).resolve()
        if not p.exists() or p.is_dir():
            continue
        try:
            txt = read_text(p)
        except Exception:
            continue
        out[rel] = txt[:max_chars]
    return out


def validate_edit_op(root: Path, op: EditOp, allowed_edit_paths: set) -> str:
    p = Path(op.path)
    if not p.is_absolute():
        p = (root / p).resolve()
    try:
        rel = str(p.relative_to(root))
    except Exception:
        raise RuntimeError(f"Refusing to edit path outside repo root: {p}")
    if is_immutable(rel):
        raise RuntimeError(f"Refusing to modify immutable file: {rel}")
    if looks_like_artifact(rel):
        raise RuntimeError(f"Refusing to modify artifact-like path: {rel}")
    if not is_allowed_edit_path(rel, allowed_edit_paths):
        raise RuntimeError(f"Refusing to modify non-allowlisted path: {rel}")
    if op.op not in {"replace", "append", "create"}:
        raise RuntimeError(f"Unknown op '{op.op}' for {rel}")
    if op.op == "replace" and (op.find is None or op.replace is None):
        raise RuntimeError(f"replace op missing find/replace for {rel}")
    if op.op == "append" and op.append is None:
        raise RuntimeError(f"append op missing append text for {rel}")
    return rel


def apply_edit_ops(root: Path, ops: List[EditOp], allowed_edit_paths: set) -> List[str]:
    notes: List[str] = []
    for op in ops:
        rel = validate_edit_op(root, op, allowed_edit_paths)
        p = (root / rel).resolve()
        before = read_text(p) if p.exists() else ""
        if op.op == "replace":
            expected = 1 if op.count is None else int(op.count)
            new, n = re.subn(re.escape(op.find or ""), op.replace or "", before)
            if n != expected:
                raise RuntimeError(
                    f"Replace count mismatch for {rel}: expected {expected}, got {n}."
                )
            write_text(p, new)
            notes.append(f"{rel}: replaced {n} occurrence(s)")
        elif op.op == "create":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(op.content or "", encoding="utf-8")
            notes.append(f"{rel}: created ({len(op.content or '')} chars)")
            continue
        elif op.op == "append":
            new = before + ("" if before.endswith("\n") or before == "" else "\n") + (op.append or "")
            if not new.endswith("\n"):
                new += "\n"
            write_text(p, new)
            notes.append(f"{rel}: appended {len(op.append or '')} chars")
    return notes


def propose_edits_with_model(
    goal: str,
    ctx: Dict[str, str],
    model: str,
    error_summary: Optional[dict] = None,
    failure_type: Optional[str] = None,
) -> Tuple[List[EditOp], str]:
    client = get_openai_client()

    system_msg = "Return ONLY valid JSON. No markdown. No commentary."
    if failure_type:
        strategy = RETRY_STRATEGY_MAP.get(failure_type, "Apply minimal deterministic fix.")
        system_msg = f"Detected failure_type: {failure_type}. {strategy} " + system_msg

    prompt = {
        "goal": goal,
        "rules": DEFAULT_SYSTEM_RULES,
        "files": ctx,
        "error_summary": error_summary,
        "failure_type": failure_type,
        "output_schema": {
            "ops": [
                {
                    "path": "relative/path.py",
                    "op": "replace|append",
                    "find": "exact anchor text to find (required for replace)",
                    "replace": "replacement text (required for replace)",
                    "count": 1,
                    "append": "text to append (required for append)",
                }
            ],
            "commit_message": "short git commit message",
            "notes": "brief explanation",
        },
    }

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(prompt)},
        ],
    )

    text = resp.choices[0].message.content.strip()
    try:
        plan = json.loads(text)
    except Exception as e:
        raise RuntimeError(f"Model did not return valid JSON. First 300 chars:\n{text[:300]}") from e

    ops = [EditOp(**o) for o in (plan.get("ops") or [])]
    commit_message = (plan.get("commit_message") or "").strip()
    if not commit_message:
        commit_message = f"autopilot: {goal[:60].rstrip()}".replace('"', "'")
    return ops, commit_message


def run_verify(verify_cmd: str) -> subprocess.CompletedProcess:
    r = sh(verify_cmd, check=False)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"Verify command failed: {verify_cmd} (exit {r.returncode})")
    return r


def git_commit(commit_msg: str) -> None:
    sh("git add -A", check=True)
    r = sh("git status --porcelain", check=True)
    staged = r.stdout.splitlines()
    bad = []
    for line in staged:
        path = line[3:].strip()
        if looks_like_artifact(path) or path.startswith("WORK_ITEMS_REGISTER.upgraded.") or path.startswith("logs/"):
            bad.append(path)
    for p in bad:
        sh(f"git restore --staged -- '{p}'", check=True)
    r2 = sh("git diff --cached --name-only", check=True)
    files = [ln.strip() for ln in r2.stdout.splitlines() if ln.strip()]
    if not files:
        print("Nothing non-artifact to commit.")
        return
    sh(f'git commit -m "{commit_msg}"', check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True, help="What you want the autopilot to accomplish.")
    ap.add_argument("--verify", required=True, help="Shell command to run verification (must exit 0 on success).")
    ap.add_argument("--model", default="qwen2.5-coder:32b", help="Model name.")
    ap.add_argument("--max-iterations", type=int, default=8)
    ap.add_argument("--strict-worktree", action="store_true", help="Refuse to run if worktree is dirty.")
    ap.add_argument("--context", nargs="*", default=[
        "tools/oc_autopilot.py",
        "scripts/upgrade_next_steps_from_bodies_v4_1.py",
        "scripts/verify_v4_1.sh",
        "scripts/run_v4_1_full.sh",
    ])
    args = ap.parse_args()

    root = get_repo_root()
    log_path = root / "tools" / "oc_autopilot_runs.jsonl"
    allowed_edit_paths = set(DEFAULT_ALLOWED_EDIT_PATHS)

    ensure_clean_worktree(strict=args.strict_worktree)

    last_error_summary: Optional[dict] = None
    last_failure_type: Optional[str] = None

    for i in range(1, args.max_iterations + 1):
        print(f"\n=== Autopilot iteration {i}/{args.max_iterations} ===")
        if last_failure_type:
            print(f"  Retrying with strategy: {RETRY_STRATEGY_MAP.get(last_failure_type, 'unknown')}")

        ctx = get_context_snippets(root, args.context)

        append_jsonl(log_path, {
            "ts": utc_now(),
            "iteration": i,
            "phase": "pre_model",
            "goal": args.goal,
            "context_paths": sorted(ctx.keys()),
            "verify": args.verify,
            "allowed_edit_paths": sorted(allowed_edit_paths),
            "error_summary": last_error_summary,
            "failure_type": last_failure_type,
        })

        try:
            ops, commit_msg = propose_edits_with_model(
                args.goal, ctx, args.model,
                error_summary=last_error_summary,
                failure_type=last_failure_type,
            )
        except RuntimeError as e:
            print(f"Model error: {e}")
            last_error_summary = {"failing_cmd": "model_call", "returncode": 1, "tail": str(e), "traceback_tail": None}
            last_failure_type = "unknown"
            continue

        append_jsonl(log_path, {
            "ts": utc_now(),
            "iteration": i,
            "phase": "model_ops",
            "ops": [asdict(op) for op in ops],
            "commit_message": commit_msg,
        })

        if not ops:
            print("No ops returned. Treating as successful no-op run.")
            append_jsonl(log_path, {
                "ts": utc_now(),
                "iteration": i,
                "phase": "no_op",
                "message": "Model returned no edit operations.",
            })
            return

        try:
            notes = apply_edit_ops(root, ops, allowed_edit_paths)
        except RuntimeError as e:
            print(f"Apply error: {e}")
            last_error_summary = {"failing_cmd": "apply_edit_ops", "returncode": 1, "tail": str(e), "traceback_tail": None}
            last_failure_type = classify_error(last_error_summary)
            continue

        for n in notes:
            print("APPLIED:", n)

        append_jsonl(log_path, {
            "ts": utc_now(),
            "iteration": i,
            "phase": "post_apply",
            "notes": notes,
        })

        if (root / "scripts/upgrade_next_steps_from_bodies_v4_1.py").exists():
            r = sh("python3 -m py_compile scripts/upgrade_next_steps_from_bodies_v4_1.py", check=False)
            if r.returncode != 0:
                print("Syntax check failed after apply.")
                last_error_summary = {"failing_cmd": "py_compile", "returncode": r.returncode, "tail": r.stderr, "traceback_tail": None}
                last_failure_type = "syntax_error"
                continue

        try:
            verify_result = run_verify(args.verify)
        except RuntimeError as e:
            print(f"Verify failed: {e}")
            # Build CmdResult-like dict for classify_error
            last_error_summary = {
                "failing_cmd": args.verify,
                "returncode": 1,
                "tail": str(e),
                "traceback_tail": None,
            }
            last_failure_type = classify_error(last_error_summary)
            continue

        append_jsonl(log_path, {
            "ts": utc_now(),
            "iteration": i,
            "phase": "verify_ok",
            "verify_stdout_tail": verify_result.stdout[-4000:],
            "verify_stderr_tail": verify_result.stderr[-4000:],
        })

        git_commit(commit_msg)
        print("\n✅ Success. Committed.")
        return

    raise RuntimeError("Max iterations reached without success.")


if __name__ == "__main__":
    main()
