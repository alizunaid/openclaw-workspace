# OpenClaw State

Last updated: 2026-05-19T21:58:33Z
Last session: Tier-1 failure forensics on v4 sweep — root-caused all 5 modal-FAIL runs, produced ranked engine-fix candidate list.

## Current HEAD
bd500b9 — feat(oc_builder): add static cross-module symbol lint before dry-import

## Last sweep result
v4 determinism sweep (2026-05-19) — 5 tasks × 3 runs = 15 runs at HEAD bd500b9. Modal totals: **3 PASS / 0 PARTIAL / 2 FAIL** (Tasks 3, 4, 5 PASS; Tasks 1, 2 FAIL). PARTIAL bucket eliminated vs v3. Report: `/tmp/determinism_v4.md`. Tier-1 forensics on the 5 FAIL runs: `/tmp/tier1_failure_analysis.md`.

## Next planned step
Implement Tier-1 ranked fix #1: cap manifest at 2 files unless the user prompt explicitly names ≥2 modules. Planner-side prompt edit + post-validation. Estimated impact: 4 of 5 v4 failures (all the Type-A symbol-drift cases on over-decomposed manifests). Small implementation effort.

## Open questions / decisions pending
- Whether to land Tier-1 fix #1 alone, or pair it with fix #6 (require `__name__ == "__main__"` guards in non-entry modules) for a combined v5 sweep.
- Whether to bias toward planner-side prevention (#1) or generation-side correction (#2 symbol contract / #3 lint-driven retry). #1 is highest impact-per-effort but removes the surface where multi-file work happens. #2 and #3 keep that surface but enforce correctness.
- Splitter blind spot found in t5a: when `date_utils.py` contained all three files concatenated with `# stale_detector.py` and `# smoke_test.py` headers, `split_manifest_sections` returned the input unchanged because no section had the target header. Behavior should be "return the no-header preamble as the target's content". Low priority — only affected 1/15 v4 runs and the underlying TZ bug would have fired anyway.

## Recent commits (last 5)
```
bd500b9 feat(oc_builder): add static cross-module symbol lint before dry-import
3c0b49a fix(oc_builder): prefer main.py and explicit entry-point purpose strings
7dfddd0 fix(oc_builder): exclude test modules by filename, not just purpose
51161ac fix(oc_builder): expose absolute paths in inspection context
9dff228 fix(oc_builder): exclude test files from entry-point and execute entry before smoke tests
```

## Workflow note
At the end of any non-trivial task (engine fix, sweep, analysis), Claude Code updates BOTH this file and `/tmp/last_cc_output.md` as the final step before reporting. STATE.md is committed for continuity across sessions; `/tmp/last_cc_output.md` is ephemeral and gets pasted into Claude.ai for next-session planning.
