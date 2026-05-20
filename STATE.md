# OpenClaw State

Last updated: 2026-05-20T03:35:00Z
Last session: v6 determinism sweep at HEAD `db71e3c` (post v5+ pandas/dep-honesty/smoke-subprocess + Item 1 TZ guidance + Item 2 archives). Modal: 3 PASS / 0 PARTIAL / 2 FAIL — flat headline vs v4/v5 but new dominant failure mode (multi-file concatenation splitter blind spot) is visible and actionable.

## Push notifications
- Channel: ntfy.sh
- Topic: `openclaw-zunaid786786` (URL: https://ntfy.sh/openclaw-zunaid786786)
- Helper: `ocnotify "title" "body" [priority]` (alias for `tools/notify.sh`; priority = `default` | `high` | `urgent`)
- Triggers: CC session end, sweep completion (high priority), engine push to github-clean
- Script fails silently — notification failure never breaks a workflow.

## Environment
- pandas: installed system-wide as of 2026-05-20, version 3.0.3 (numpy 2.4.6 + python-dateutil 2.9.0 pulled as deps). Installed with `pip3 install --break-system-packages pandas` because the system Python is PEP-668 externally-managed and the engine runs system `python3` directly (no venv).

## Run archives
- Path: `/root/.openclaw/workspace/logs/run_archive/run_<ts>/` (gitignored)
- Cap: `RUN_ARCHIVE_MAX = 50` most-recent by mtime; auto-pruned on each run
- Populated atexit, fail-tolerant — archive failure never breaks the run's exit semantics
- Use for post-sweep forensics so the original `tools/generated/run_<ts>/` stays available too

## Current HEAD
db71e3c — feat(oc_builder): archive generated run dirs to logs/run_archive for forensics

## Last sweep result
v6 determinism sweep (2026-05-20) — 5 tasks × 3 runs at HEAD `db71e3c`. Modal totals: **3 PASS / 0 PARTIAL / 2 FAIL** (Tasks 1, 2, 5 PASS modal; Tasks 3, 4 FAIL modal). Report: `/tmp/determinism_v6.md`. 15/15 sweep runs archived to `logs/run_archive/`.

| | v1 | v2 | v3 | v4 | v5 | v6 |
|---|---:|---:|---:|---:|---:|---:|
| PASS    | 1 | 1 | 1 | 3 | 3 | 3 |
| PARTIAL | 1 | 2 | 2 | 0 | 0 | 0 |
| FAIL    | 3 | 2 | 2 | 2 | 2 | 2 |

**Headline pass count flat at 3 since v4.** Task identity rotates each sweep — Task 5 newly fixed (Item 1 TZ guidance worked, 0/3 naive `datetime.now()`), Task 3 newly broken (splitter blind spot exposed by Task 5 no longer masking it).

## Phase 3 pipeline (current)
1. Entry-point selection
2. Contract verification — declared exports must be defined (Fix A)
3. Main-guard check — non-entry modules guard top-level work; up to 2 LLM regenerations (Fix B)
4. Dep-honesty check — actual generated imports must form a DAG; self-imports and cycles hard-fail (Tier-1 v5+ Commit 2)
5. Static cross-module lint — `from X import Y` valid only if Y in X's declared exports
6. Dry-import of entry point
7. Entry execution
8. Smoke tests (if any in manifest)

## Next planned step
**One more obvious engine fix: splitter blind spot.** `split_manifest_sections` returns the LLM input unchanged when the target file's section has no explicit `# <filename>` header. Change it to fall back to the first (header-less) section when:
1. The LLM output contains header markers for some manifest files but not for the target, AND
2. The first (header-less) section has substantive content.

This single change is predicted to address 4 of 8 v6 hard-fails (t3a/b/c, t5c — all multi-file-concatenation cases). After it ships, Tier-1 hardening is complete; remaining failures are LLM-quality issues (consumer-side contract violations + missing stdlib imports) that don't dissolve with more engine gates.

Predicted post-splitter-fix v7 modal: **4 PASS / 0 PARTIAL / 1 FAIL.**

## Open questions / decisions pending
- Phase 4 auto-commit shouldn't pick up dirty files outside `tools/generated/run_<ts>/`. During this session it grabbed Item-2 in-progress changes with a wrong commit message; recovered via `git commit --amend` + force-push, but a scoping guard would prevent recurrence. Out of scope for v6 but worth a quick follow-up.
- v6 stability worsened vs v5 (3 tasks now at 1/3 instead of 0). New gates surface more issues that were previously silent; LLM bug distribution spreading. Median-of-3 grading stays mandatory.
- v6's lint catches now include production-code consumer-side violations (t1c main.py, t4b prioritizer.py + main.py). Tier-2 territory — would need a retry-with-error-feedback loop similar to main-guard retry.

## Recent commits (last 8)
```
db71e3c feat(oc_builder): archive generated run dirs to logs/run_archive for forensics
0f6146b feat(oc_builder): add TZ-aware datetime guidance to planner and generator prompts
ecb8737 chore: STATE.md post v5+ hardening + Task-5 mini-validation
8a568d3 feat(oc_builder): instruct smoke tests to subprocess the entry script, not import internals
cc019c9 feat(oc_builder): verify actual imports form a DAG (catch circular imports the manifest didn't declare)
0da151f chore: install pandas + record in STATE.md environment section
93c67f1 chore: STATE.md post v5 determinism sweep
88ab252 chore: STATE.md post Tier-1 fixes A+B
```

## Workflow note
At the end of any non-trivial task (engine fix, sweep, analysis), Claude Code updates BOTH this file and `/tmp/last_cc_output.md`, commits/pushes STATE.md, and sends a session-end ntfy push, all as the final step before reporting. STATE.md is committed for continuity across sessions; `/tmp/last_cc_output.md` is ephemeral and gets pasted into Claude.ai for next-session planning.
