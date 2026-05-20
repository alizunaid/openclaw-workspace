# OpenClaw State

Last updated: 2026-05-20T00:25:00Z
Last session: v5 determinism sweep at HEAD `88ab252` (Fix A contracts + Fix B main-guards). Modal: 3 PASS / 0 PARTIAL / 2 FAIL — same headline as v4 but with new failure-mode shape (test files importing internals, circular imports).

## Push notifications
- Channel: ntfy.sh
- Topic: `openclaw-zunaid786786` (URL: https://ntfy.sh/openclaw-zunaid786786)
- Helper: `ocnotify "title" "body" [priority]` (alias for `tools/notify.sh`; priority = `default` | `high` | `urgent`)
- Triggers: CC session end, sweep completion (high priority), engine push to github-clean
- Script fails silently — notification failure never breaks a workflow.

## Environment
- pandas: installed system-wide as of 2026-05-20, version 3.0.3 (numpy 2.4.6 + python-dateutil 2.9.0 pulled as deps). Installed with `pip3 install --break-system-packages pandas` because the system Python is PEP-668 externally-managed and the engine runs system `python3` directly (no venv).

## Current HEAD
88ab252 — chore: STATE.md post Tier-1 fixes A+B

## Last sweep result
v5 determinism sweep (2026-05-19/20) — 5 tasks × 3 runs = 15 runs at HEAD `88ab252`. Modal totals: **3 PASS / 0 PARTIAL / 2 FAIL** (Tasks 2, 3, 4 PASS; Tasks 1, 5 FAIL). PARTIAL bucket still empty. Report: `/tmp/determinism_v5.md`.

Key Fix A signal: the model emitted the `exports` field on 14/15 runs (the 15th degraded to legacy single-file mode for unrelated reasons). Contract verification never fired — every file with a declared contract defined exactly those names. **The Fix-A discipline is being followed by the model with no iteration needed.**

Key Fix B signal: 3/15 runs needed main-guard regeneration (sort_data.py, test_main.py, utils.py, smoke_test_stale_detector.py). All fixed on first retry within budget. Zero hard-fails from budget exhaustion.

## Phase 3 pipeline (current, unchanged since 88ab252)
1. Entry-point selection
2. Contract verification — declared exports must be defined (Fix A)
3. Main-guard check — non-entry modules guard top-level work; up to 2 LLM regenerations (Fix B)
4. Static cross-module lint — `from X import Y` valid only if Y in X's declared exports (Fix A updates lint)
5. Dry-import of entry point
6. Entry execution
7. Smoke tests (if any in manifest)

## Next planned step
**Diagnose two distinct new failure shapes the v5 sweep revealed.** Tier-1-style forensic before any new engine work:
1. **Smoke-test-imports-internals (5 of 7 v5 FAILs).** LLM consistently writes smoke tests that `from entry_script import internal_func` rather than subprocess-invoking the script. Entry's declared exports = `[]` (correctly), lint blocks. Need a planning-side fix: either (a) declare the entry's callable as an export when a smoke test is going to use it, or (b) instruct smoke tests to subprocess the script.
2. **Circular imports (2 of 7 v5 FAILs).** LLM-generated source introduces import cycles between sibling modules that the `depends_on` DAG didn't declare. t1a: category_filter ↔ output_formatter. t3c: utils.py imports from itself. Need a cross-check between actual imports and declared `depends_on`.

## Open questions / decisions pending
- Are these two new shapes worth distinct fixes, or does one prompt-side intervention (smoke-test discipline + dep-honesty in manifest) cover both?
- t2c still hits `ModuleNotFoundError: pandas` despite "stdlib only" instruction. Stand-alone fix (install pandas, AST-reject non-stdlib imports, or stronger prompt) — independent of the two new shapes.
- The contract narrowing exposed that the LLM declares `exports=[]` on entry scripts (correct) AND writes test files that import their internals (incorrect under the new contract). The cleanest fix is planning-side, not engine-side, and worth a targeted prompt experiment.

## Recent commits (last 5)
```
88ab252 chore: STATE.md post Tier-1 fixes A+B
19b6a7a feat(oc_builder): require __main__ guards in non-entry modules with retry
00b6b59 feat(oc_builder): add symbol contracts to planner manifest and verify generated exports
56c25d7 feat: ntfy push notifications for sweep/commit/session events
85f6672 chore: add STATE.md for session continuity
```

## Workflow note
At the end of any non-trivial task (engine fix, sweep, analysis), Claude Code updates BOTH this file and `/tmp/last_cc_output.md`, commits/pushes STATE.md, and sends a session-end ntfy push, all as the final step before reporting. STATE.md is committed for continuity across sessions; `/tmp/last_cc_output.md` is ephemeral and gets pasted into Claude.ai for next-session planning.
