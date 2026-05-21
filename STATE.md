# OpenClaw State

Last updated: 2026-05-21T15:50:00Z
Last session: Step 1 first-real-work attempt — TODAY top-10 generator via `ocb`. **Validation failed**: engine exceeded 1200s cap (1241s), planner over-decomposed to 6 files, main.py has tuple-vs-dict signature drift between sibling modules. No commits made; user decision pending on four paths documented in `/tmp/step1_validation.md`. Working artifact exists at `tools/generated/run_1779377170/reporter.py` (one of 4 standalone implementations the planner produced) but was not the planner's chosen entry.

Prior session: Final Tier-1 hardening — splitter preamble fallback (Item 1) + Phase 4 scoping (Item 2) + v7 determinism sweep. Tier-1 declared structurally complete.

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
- Populated via atexit hook in `oc_builder.py` — fail-tolerant
- Currently 31 archives (15 v6 + 1 misc + 15 v7)

## Current HEAD
05891c0 — fix(oc_builder): scope Phase 4 auto-commit to tools/generated/run_<id>/ only, skip if out-of-scope changes exist
(plus follow-up STATE.md commit at session end)

## Tier-1 verdict: STRUCTURALLY COMPLETE
- Every observed failure mode is either caught by an engine gate with a precise diagnostic, OR is LLM-quality (Type A consumer/producer drift, Type C missing imports, smoke-test correctness) and not amenable to additional structural engine gates.
- v7 sweep showed real saves from every Phase-3 gate: contracts (1, first-ever), main-guards (6 retries), dep-honesty (1), static lint (3), splitter (6). No gate is idle.
- The full Tier-1 surface is shipped: symbol contracts → entry-point routing → AST self-heal → splitter w/ preamble fallback → contracts gate → main-guards → dep-honesty → static lint → dry-import → entry execution → smoke tests → scoped Phase 4 → forensic archives.

## Last sweep result
v7 determinism sweep (2026-05-20) — 5 tasks × 3 runs at HEAD `05891c0`. Report: `/tmp/determinism_v7.md`.

| Task | a | b | c | Modal | Stability |
|------|---|---|---|-------|-----------|
| 1 | FAIL | FAIL | PASS | FAIL | 2/3 |
| 2 | FAIL | PARTIAL | PARTIAL | PARTIAL | 2/3 |
| 3 | PARTIAL | PASS | FAIL | (3-way tie) | 1/3 |
| 4 | FAIL | PARTIAL | PASS | (3-way tie) | 1/3 |
| 5 | PARTIAL | PASS | FAIL | (3-way tie) | 1/3 |

**Modal:** 0 PASS / 3 PARTIAL / 2 FAIL. **Run-level:** 4 PASS / 5 PARTIAL / 6 FAIL.

| | v1 | v2 | v3 | v4 | v5 | v6 | v7 |
|---|---:|---:|---:|---:|---:|---:|---:|
| PASS modal    | 1 | 1 | 1 | 3 | 3 | 3 | 0 |
| PARTIAL modal | 1 | 2 | 2 | 0 | 0 | 0 | 3 |
| FAIL modal    | 3 | 2 | 2 | 2 | 2 | 2 | 2 |

The v7 modal regression is largely **tie-break noise** on 1/3-stability tasks (3 tasks each had one of each verdict). Run-level outcomes are comparable to v4–v6. The engine is producing correct, diagnosable signal — the LLM is producing diverse bugs that the gates accurately surface.

## Phase 3 pipeline (final)
1. Entry-point selection (Fix 1 filename-based exclusion + Fix 2 main-preference)
2. Contract verification — declared exports must be defined (Fix A)
3. Main-guard check — non-entry modules guard top-level work; up to 2 LLM regenerations (Fix B)
4. Dep-honesty check — actual generated imports must form a DAG; self-imports and cycles hard-fail
5. Static cross-module lint — `from X import Y` valid only if Y in X's declared exports
6. Dry-import of entry point
7. Entry execution
8. Smoke tests (if any in manifest)

Plus preceding: planner manifest with symbol contracts, per-file AST self-heal (3 retries) + splitter (header detection + preamble fallback). Plus succeeding: Phase 4 scoped commit (refuses out-of-scope changes) + atexit run-dir archive.

## Step 1 status
First real-work attempt — TODAY top-10 generator — failed validation. Engine timed out at 1241s (1200s cap), planner over-decomposed into 6 files with broken cross-file glue. No commits made. Full validation report at `/tmp/step1_validation.md` with four decision-point options:
- A. Manual rescue — copy working `reporter.py` from the run dir to `tools/daily/today_top10.py`, ship today's value (RECOMMENDED).
- B. Re-prompt with tighter scoping (one or two files, no test file).
- C. Tier-2 regenerate-with-error-feedback loop first.
- D. Bump RUN_CAP_SECONDS to 1800.

User decision pending. Run dir preserved at `tools/generated/run_1779377170/` and archived at `logs/run_archive/run_1779377170/`.

## Next planned step
Awaiting user direction on Step 1 (A/B/C/D above). Default after sufficient time: A — ship the working reporter.py as the daily driver, pin Tier-2 + over-decomposition as the next engine targets.

## Open questions / decisions pending
- The 1/3-stability tasks (3, 4, 5) make single-run grading nearly random. Future engine work needs ≥5-run grading to be statistically meaningful. Or accept that 5-task sweeps will continue to land headline modals in a 0-4 PASS range.
- One v7 contracts firing (t1b, producer-side declared-vs-defined mismatch) — Fix A's first save in 7 sweeps. Worth watching whether this becomes a pattern with larger manifests.
- **Step-1 surfaced Type-B value-shape drift.** Sibling module signatures don't match across files the planner glued together. Lint can't catch this. Either a Tier-2 regen-with-feedback (catches at dry-import) or a static typecheck (heavier).
- **Engine cap is at the edge for 6-file manifests.** 1241s for Step 1. Larger or more complex manifests will increasingly hit it during generation, not just validation.

## Recent commits (last 8)
```
05891c0 fix(oc_builder): scope Phase 4 auto-commit to tools/generated/run_<id>/ only, skip if out-of-scope changes exist
3e56f7a fix(oc_builder): split_manifest_sections falls back to pre-header preamble when target file lacks an explicit header
b4572e5 chore: STATE.md post v6 determinism sweep
db71e3c feat(oc_builder): archive generated run dirs to logs/run_archive for forensics
0f6146b feat(oc_builder): add TZ-aware datetime guidance to planner and generator prompts
ecb8737 chore: STATE.md post v5+ hardening + Task-5 mini-validation
8a568d3 feat(oc_builder): instruct smoke tests to subprocess the entry script, not import internals
cc019c9 feat(oc_builder): verify actual imports form a DAG (catch circular imports the manifest didn't declare)
```

## Workflow note
At the end of any non-trivial task (engine fix, sweep, analysis), Claude Code updates BOTH this file and `/tmp/last_cc_output.md`, commits/pushes STATE.md, and sends a session-end ntfy push, all as the final step before reporting. STATE.md is committed for continuity across sessions; `/tmp/last_cc_output.md` is ephemeral and gets pasted into Claude.ai for next-session planning.
