# OpenClaw State

Last updated: 2026-05-20T01:30:00Z
Last session: Shipped 3 Tier-1 v5+ hardening commits (pandas install, dep-honesty gate, smoke-test subprocess prompts). Task-5 mini-validation: 3/3 follow new subprocess pattern; 0/3 PASS end-to-end (failures are orthogonal LLM defects, not the targeted symbol-drift family).

## Push notifications
- Channel: ntfy.sh
- Topic: `openclaw-zunaid786786` (URL: https://ntfy.sh/openclaw-zunaid786786)
- Helper: `ocnotify "title" "body" [priority]` (alias for `tools/notify.sh`; priority = `default` | `high` | `urgent`)
- Triggers: CC session end, sweep completion (high priority), engine push to github-clean
- Script fails silently — notification failure never breaks a workflow.

## Environment
- pandas: installed system-wide as of 2026-05-20, version 3.0.3 (numpy 2.4.6 + python-dateutil 2.9.0 pulled as deps). Installed with `pip3 install --break-system-packages pandas` because the system Python is PEP-668 externally-managed and the engine runs system `python3` directly (no venv).

## Current HEAD
8a568d3 — feat(oc_builder): instruct smoke tests to subprocess the entry script, not import internals

## Last sweep result
v5 determinism sweep (2026-05-19/20) — 5 tasks × 3 runs = 15 runs at HEAD `88ab252`. Modal totals: **3 PASS / 0 PARTIAL / 2 FAIL** (Tasks 2, 3, 4 PASS; Tasks 1, 5 FAIL). Report: `/tmp/determinism_v5.md`.

## Task-5 mini-validation (post v5+ commits)
At HEAD `8a568d3`. 3 runs, same Task 5 prompt as v5 sweep.

- **Smoke-test pattern shift confirmed:** 3/3 runs use `subprocess.run(["python3", "<entry>.py"], ...)` in the smoke file. 0/3 do `from <entry> import internal_func`. The prompt change in Commit 3 took effect on the first try.
- **Verdicts:** 0 PASS / 1 PARTIAL / 2 FAIL. The remaining failures are LLM code-quality defects orthogonal to the smoke-test-internals problem the commits targeted:
  - run a: `date_utils.py` line 1 was the bare identifier `d` (garbled LLM output). NameError at dry-import. Type F.
  - run b: recurring TZ bug in `days_since` (v1/v4/v5 stdlib misuse). Type C.
  - run c: entry executed and produced `/tmp/stale.md` correctly. Smoke test followed subprocess pattern but used `Path(...)` without `from pathlib import Path`. Type C (smoke-test missing import).
- **None of the 3 runs hit the v5 failure shape** (smoke imports unexported internals). The static lint and contract gate stayed quiet throughout. Commit 3 has structurally closed that failure mode.

## Phase 3 pipeline (current)
1. Entry-point selection
2. Contract verification — declared exports must be defined (Fix A)
3. Main-guard check — non-entry modules guard top-level work; up to 2 LLM regenerations (Fix B)
4. Dep-honesty check — actual generated imports must form a DAG; self-imports and cycles hard-fail; undeclared manifest-internal imports log a warning (Tier-1 v5+ Commit 2)
5. Static cross-module lint — `from X import Y` valid only if Y in X's declared exports (Fix A updates lint)
6. Dry-import of entry point
7. Entry execution
8. Smoke tests (if any in manifest)

## Next planned step
Run full v6 determinism sweep (5 tasks × 3 runs) at HEAD `8a568d3`. The 3 v5+ commits address all three remaining v5 failure families (pandas, circular imports, smoke imports). Predicted v6 modal based on:
- Task 1: v5 modal FAIL was 1× circular-import (now caught structurally + the lint message will at least be honest), 1× lint-strict on test_main.py (still LLM-side). Could go either way.
- Task 2: v5 modal PASS already, pandas no longer fails.
- Task 3: v5 modal PASS already, self-import will be caught more cleanly.
- Task 4: v5 modal PASS already, no obvious regression.
- Task 5: mini-validation shows 0/3 PASS at new HEAD; the smoke-test pattern is fixed but TZ bugs and garbled code recur. Likely modal FAIL still.

Realistic v6 prediction: **3–4 PASS / 0–1 PARTIAL / 1–2 FAIL.**

## Open questions / decisions pending
- Task 5's TZ-naive `days_since` bug has now recurred in v1, v4, and the v5+ mini-validation. Worth a targeted prompt-side or AST-level fix? E.g., when the task prompt mentions "days" / "stale" / "older than N", inject explicit tz-aware datetime guidance. Or AST-reject `datetime.now()` / `datetime.utcnow()` in non-test files.
- The mini-validation run-a "garbled LLM output" (`date_utils.py` was literally the single character `d`) is a new defect family — single point so far, but worth watching in v6.
- Should `verify_dep_honesty` upgrade the "undeclared dep" warning to a hard-fail in a future version, or is the warning + lint downstream sufficient?

## Recent commits (last 5)
```
8a568d3 feat(oc_builder): instruct smoke tests to subprocess the entry script, not import internals
cc019c9 feat(oc_builder): verify actual imports form a DAG (catch circular imports the manifest didn't declare)
0da151f chore: install pandas + record in STATE.md environment section
93c67f1 chore: STATE.md post v5 determinism sweep
88ab252 chore: STATE.md post Tier-1 fixes A+B
```

## Workflow note
At the end of any non-trivial task (engine fix, sweep, analysis), Claude Code updates BOTH this file and `/tmp/last_cc_output.md`, commits/pushes STATE.md, and sends a session-end ntfy push, all as the final step before reporting. STATE.md is committed for continuity across sessions; `/tmp/last_cc_output.md` is ephemeral and gets pasted into Claude.ai for next-session planning.
