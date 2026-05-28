# OpenClaw State

Last updated: 2026-05-28T01:00:00Z
Last session: **Tier 2 v1 — Session 1: `oc2` CLI scaffolding + architecture schema/validator (LLM mocked).** Built the `tools/oc2/` package per `design/tier2_v1.md` Section 8: `architecture.py` (markdown parser + structural validator — required fields, kebab names, reserved `main`, count bounds [2,10], DAG closure + Kahn topo sort with alphabetical tiebreak, soft warnings), `state.py` (atomic state.json via temp+fsync+os.replace, `state_schema_version: 1` from day one per Q6), `cli.py` argparse dispatcher, real `design`/`approve`/`list`/`status`, and `build`/`archive`/`prompt`/`smoke` stubs. LLM is MOCKED (`mock_llm_design` returns a fixed valid 4-subsystem daily-snapshot architecture, diamond DAG). End-to-end mock loop verified: `oc2 design "<task>" --name test` → valid `architecture.md` → `oc2 approve test` passes validation → `state.json` written. Q8 name-conflict refusal + explicit-`--name` regeneration/rotation (`architecture.v<N>.md`) both work. 31 unit tests green (parser + validator: positive case + every failure mode + soft warnings). `oc2` alias added to `~/.bashrc`. Commits: `d13a5bd` (parser+validator), `5306bc5` (state), `dab0301` (CLI+commands), `d786fe4` (gitignore). **Deferred:** Session 2 = real single-call LLM design against `qwen2.5-coder:32b` (Q5) + parsing real LLM markdown + `oc_project` context loading (no Tier 1 imports this session); Session 3 = spec-diff cascading rebuild; Sessions 4/5 = build phase + subsystem→ocb prompt translator + integration smoke. `tier2_projects/test/` is an untracked Session-1 verification artifact (safe to delete). Started from HEAD `6e40f21`, a clean descendant of design-time `15aa453` (intervening commits were chore/design only — no engine code).

Prior session: Built `review_bundle/` for human review of the agnostic Tier 1 system. 26 files, 324KB. 12 numbered directories at `/root/.openclaw/workspace/review_bundle/` covering engine source, extracted prompts (5 verbatim from `oc_builder.py`), extracted validation gates (5 with source), retry/repair loops (3 with source), CLI entry points, sample state logs (PASS + FAIL real runs), sample run output trees, project-context example, and current docs. Two narrative READMEs (`00_README.md` reading order + `12_README_engine_overview.md` end-to-end engine walkthrough). Gitignored — local review only, not for tracking. Only commit was the .gitignore line itself.

Prior session: Tier 2 v1 design scoping. No code written. Produced `design/tier2_v1.md` (commit `5450140`) — 550-line scoping document. Ends with 9 explicit open questions awaiting user review.

Prior session: Step 1 v2 — tighter ocb re-prompt + rescue fallback. Path B (rescue) shipped. `tools/daily/today_top10.py` is the first real-work daily-driver. Alias `today10` works.

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
d786fe4 — chore: gitignore tier2_projects (Tier 2 v1 Session 1 code HEAD)
(plus follow-up STATE.md commit at session end)
Tier 1 engine HEAD remains 05891c0 (fix(oc_builder): scope Phase 4 auto-commit); untouched this session.

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

## Step 1 status: SHIPPED via Path B (rescue)
- Tool: `tools/daily/today_top10.py` (108 lines, single Python file)
- Alias: `today10='python3 /root/.openclaw/workspace/tools/daily/today_top10.py'` in `~/.bashrc`
- Shipping commit: `ede47cb feat(daily): today's top-10 generator (ocb-assisted, manually rescued from run_1779377170)`
- Provenance: started from `tools/generated/run_1779377170/reporter.py` (the working artifact from last session's over-decomposed 6-file engine output); applied 3 minimal edits — added `REQUIRED_COLUMNS` validation, fixed `split(';')` → strip-tokens for multi-category items, fixed `'\n'.join` → `''.join` for clean markdown rendering.
- Live output: 20 WAITING_ON_YOU items found; top-2 are PERMITS_INSPECTIONS at score 5 (city/permit blockers stuck 219 + 128 days); rows 3-7 are ENGINEERING/CLEANROOM at score 4. Domain-sense check passes for a real pharmacy permit build.

Phase A — the tighter ocb re-prompt — got SO close: 250s elapsed, exactly 1 file in the manifest, all 4 Phase 3 gates green up through dry-import, then entry execution exited 1 with `ValueError: I/O operation on closed file` (LLM reused a `csv.reader` outside its `with` block for the summary count). One line away from working. Pin this as the v8 sweep's reference test case for Tier-2 regen-with-feedback.

## First real-work tools
| Path | What it does | How produced | Alias |
|------|--------------|--------------|-------|
| `tools/daily/today_top10.py` | Reads `WORK_ITEMS_REGISTER.upgraded.v4_1.csv`, filters `status_current == 'WAITING_ON_YOU'`, ranks by composite priority (PERMITS_INSPECTIONS, ENGINEERING/CLEANROOM, stuck>7d, VENDORS/FINANCE/LEGAL), writes `/tmp/today_top10.md`. | ocb-assisted, manually rescued from a failed engine run (3 minimal edits to make agnostic) | `today10` |

## Tier 2 (architecture-first planner — design phase)
A new layer above Tier 1. Tier 1 (`ocb`) generates one cohesive script per invocation. Tier 2 (`oc2`, proposed) orchestrates multiple Tier 1 invocations into systems-of-systems — driving use case is the NexaDose audit/reconciliation engine (8-12 subsystems, designed together, built incrementally).

- **Design doc:** `design/tier2_v1.md` (commit `5450140`, 550 lines).
- **Status:** SCOPING — design doc written, awaiting human review.
- **Lifecycle:** `oc2 design "<task>"` → human reviews + edits architecture.md → `oc2 approve` → `oc2 build` (topological subsystem-by-subsystem ocb invocation) → `oc2 status` (per-subsystem state).
- **Storage:** `tier2_projects/<name>/` — `architecture.md` (human-editable design), `state.json` (build state), `subsystems/<name>/` (generated code per subsystem), all git-tracked.
- **9 open questions in the doc** need human judgment before implementation: command name (`oc2` vs `ocplan`), test project choice (3 candidates ranked), architecture markdown verbosity, integration-test scope, model choice for the design phase, state-schema versioning, cascading-rebuild semantics, project-name conflict policy, ntfy hooks.
- **Next:** human reads design doc, we resolve open questions together, THEN implementation brief.

## Next planned step
**Human review of `design/tier2_v1.md`.** After we resolve the 9 open questions, the next implementation session writes the brief that becomes Tier 2 v1 Session 1 (CLI scaffolding + architecture schema + LLM prompt). Estimated 3-5 sessions to ready-for-test-project state.

Independent of Tier 2: a Tier-1 v2 enhancement is still on the queue — **regen-with-error-feedback loop in `oc_builder.py`'s Phase 3** (NOT the same as Tier 2 the architecture layer; this is a refinement to the builder engine itself). When entry-execution or dry-import fails, feed the traceback back to the LLM for a corrective regeneration. Step 1 v2's Phase A failure is the archetypal test case (a one-line bug — `ValueError: I/O operation on closed file` — in an otherwise-correct 81-line single-file script). This Tier-1 enhancement could come before or after Tier 2 v1 implementation depending on priority; design doc doesn't depend on it.

## Open questions / decisions pending
- The 1/3-stability tasks (3, 4, 5) make single-run grading nearly random. Future engine work needs ≥5-run grading to be statistically meaningful. Or accept that 5-task sweeps will continue to land headline modals in a 0-4 PASS range.
- One v7 contracts firing (t1b, producer-side declared-vs-defined mismatch) — Fix A's first save in 7 sweeps. Worth watching whether this becomes a pattern with larger manifests.
- **Step-1 surfaced Type-B value-shape drift.** Sibling module signatures don't match across files the planner glued together. Lint can't catch this. Either a Tier-1 regen-with-feedback (catches at dry-import) or a static typecheck (heavier).
- **Engine cap is at the edge for 6-file manifests.** 1241s for Step 1 v1. Mitigated in Step 1 v2 by explicit "single file" prompt constraint (250s for 1-file manifest). Tier 2 builds many 1-3-file subsystems sequentially rather than one big multi-file manifest, which sidesteps this cap entirely.
- **The 9 Tier 2 design questions in `design/tier2_v1.md`.** These need user judgment, not Claude judgment. Awaiting review.

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
