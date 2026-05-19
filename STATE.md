# OpenClaw State

Last updated: 2026-05-19T22:38:00Z
Last session: Shipped Tier-1 fixes — symbol contracts (Fix A) and main-guard gate with retry (Fix B). Engine smoke test green; both new gates appear in pipeline output.

## Push notifications
- Channel: ntfy.sh
- Topic: `openclaw-zunaid786786` (URL: https://ntfy.sh/openclaw-zunaid786786)
- Helper: `ocnotify "title" "body" [priority]` (alias for `tools/notify.sh`; priority = `default` | `high` | `urgent`)
- Triggers: CC session end, sweep completion (high priority), engine push to github-clean
- Script fails silently — notification failure never breaks a workflow.

## Current HEAD
19b6a7a — feat(oc_builder): require __main__ guards in non-entry modules with retry

## Last sweep result
v4 determinism sweep (2026-05-19) — 5 tasks × 3 runs = 15 runs at HEAD `bd500b9`. Modal totals: **3 PASS / 0 PARTIAL / 2 FAIL** (Tasks 3, 4, 5 PASS; Tasks 1, 2 FAIL). PARTIAL bucket eliminated vs v3. Report: `/tmp/determinism_v4.md`. Tier-1 forensics on the 5 FAIL runs: `/tmp/tier1_failure_analysis.md`. Tier-1 fixes A+B now shipped on top — v5 sweep is the next measurement.

## Phase 3 pipeline (current)
After Tier-1 ship, Phase 3 runs in this order:
1. Entry-point selection (Fix 1 + Fix 2 from earlier session)
2. Contract verification — declared exports must be defined (Fix A)
3. Main-guard check — non-entry modules must guard top-level work; up to 2 LLM regenerations per offending file (Fix B)
4. Static cross-module lint — `from X import Y` valid only if Y in X's declared exports (Fix A updates lint)
5. Dry-import of entry point
6. Entry execution
7. Smoke tests (if any in manifest)

## Next planned step
Run v5 determinism sweep (5 tasks × 3 runs = 15 runs) at HEAD `19b6a7a` with the same prompts and median-of-3 grading. Predicted v5 modal based on Tier-1 forensics: **4–5 PASS / 0 PARTIAL / 0–1 FAIL** if Fix A neutralises the dominant Type-A symbol drift on Tasks 1 and 2, and Fix B closes the unguarded-top-level-execution defects on Task 5a.

## Open questions / decisions pending
- Does the LLM reliably emit the `exports` field on every manifest, or does it need prompt iteration? The smoke test showed it emitting the field correctly first try. The v5 sweep will produce 15 data points to confirm.
- Should we still cap manifest size (Tier-1 rank #1 fix) on top of contracts, or do contracts alone neutralise over-decomposition's downside? Saving for post-v5 decision based on the failure-mode distribution there.
- t5a's TZ-naive bug (Type C) is not addressed by either Fix A or Fix B. If it recurs in v5, prompt-side date-task guidance is the next move.

## Recent commits (last 5)
```
19b6a7a feat(oc_builder): require __main__ guards in non-entry modules with retry
00b6b59 feat(oc_builder): add symbol contracts to planner manifest and verify generated exports
56c25d7 feat: ntfy push notifications for sweep/commit/session events
85f6672 chore: add STATE.md for session continuity
bd500b9 feat(oc_builder): add static cross-module symbol lint before dry-import
```

## Workflow note
At the end of any non-trivial task (engine fix, sweep, analysis), Claude Code updates BOTH this file and `/tmp/last_cc_output.md`, commits/pushes STATE.md, and sends a session-end ntfy push, all as the final step before reporting. STATE.md is committed for continuity across sessions; `/tmp/last_cc_output.md` is ephemeral and gets pasted into Claude.ai for next-session planning.
