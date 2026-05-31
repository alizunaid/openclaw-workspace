# Tier 2 v1 — design document

**Status:** Draft for human review. No code written. Read time: ~20 minutes.
**Author:** Claude Code, 2026-05-21.
**Prior art:** OpenClaw Tier 1 (`tools/oc_builder.py`, `ocb`) shipped and stable; first real-work tool `tools/daily/today_top10.py` in production.
**Branch this doc targets:** github-clean. **Engine HEAD at design time:** `15aa453`.

This document is the scoping artifact, not the implementation plan. The implementation brief is a separate session, written only after both of us agree this design is right.

---

## TL;DR

Tier 2 is `oc2`, a CLI that orchestrates multiple Tier 1 invocations. The lifecycle is **design → approve → build → status → revise**.

- **`oc2 design "<task>"`** asks the LLM for an architecture (subsystems, interfaces, dependencies, failure modes), writes it as `tier2_projects/<name>/architecture.md`, runs a structural validator (schema + DAG), tells the user where to read it.
- **`oc2 approve`** re-validates and locks the architecture for build. Human is expected to read and possibly edit `architecture.md` between `design` and `approve`.
- **`oc2 build`** topologically executes the architecture: per subsystem, translates one entry into a Tier 1 (`ocb`) invocation, captures the result, updates `state.json`. Pauses on failure; resumable across sessions.
- **`oc2 status`** prints which subsystems are pending / in-progress / done / failed.

State lives in `tier2_projects/<name>/` — git-tracked markdown + JSON. Build is sequential, one subsystem at a time. Failures are surfaced to the human, not auto-retried beyond Tier 1's existing budgets. Architecture changes cascade rebuilds.

Tier 2 v1 explicitly does NOT do: auto-rearchitecting, parallel builds, multi-LLM routing, integration testing beyond a minimal "all subsystems import together" smoke. v1 is the minimum coherent layer; v2+ adds intelligence.

---

## Section 1 — User-facing interface

### Command name

**Proposed: `oc2`.** Parallels `ocb` (builder). Short. Suggests it's part of the same family rather than a separate tool. Muscle memory transfers.

Alternative: `ocplan`. More self-documenting, but misleading — `oc2` owns more than just planning (it owns the whole multi-session lifecycle, including the build orchestration). I'd flip to `ocplan` only if you find `oc2` too opaque on the command line.

### Subcommands

```
oc2 design "<task>" [--name <project>] [--from <existing-arch.md>]
oc2 list
oc2 status [<project>]
oc2 approve [<project>]
oc2 build [<project>] [--resume]
oc2 archive [<project>]
```

- `design` produces the architecture. `--name` overrides the auto-derived project name (default: kebab-cased first 4 words of the task). `--from` lets the user point at an existing architecture.md they want regenerated/iterated.
- `list` shows all tier-2 projects with their current state (pending architecture / awaiting approval / building / done / failed).
- `status` shows a single project's per-subsystem build state.
- `approve` validates the current `architecture.md` against the schema + DAG, and if clean, marks it as locked-for-build in `state.json`. Re-running `approve` after edits is how revisions work.
- `build` executes the approved architecture. `--resume` is the default; build always picks up from the first non-done subsystem. The flag exists explicitly so the help text surfaces resumability.
- `archive` is for project cleanup — moves the project to `tier2_projects/.archive/<name>/` without deleting.

### Where docs and state live

```
tier2_projects/
├── <project_name>/
│   ├── architecture.md          # current design (human-editable)
│   ├── architecture.v<N>.md     # version history (kept on each new `design` run)
│   ├── state.json               # project state (see Section 4)
│   ├── subsystems/
│   │   └── <subsystem_name>/    # generated code per subsystem (mirrors ocb output)
│   │       └── *.py
│   ├── runs/
│   │   └── <iso_timestamp>.log  # per-session orchestration log
│   └── BUILD_REPORT.md          # written on completion
└── .archive/                    # archived projects
```

The `tier2_projects/` directory is at the workspace root. Why not under `tools/`: these are workspace artifacts (project outputs), not engine code. They share lineage with `logs/` and the generated `tools/generated/`.

### Approve / edit / revise flow

The architecture is markdown because **the user reads and edits it as part of the loop**, not as a transient LLM artifact. After `oc2 design` writes the file, the workflow is:

1. User reads `architecture.md`.
2. Optionally edits it directly (add a subsystem, remove a dependency, tighten a purpose, etc.).
3. Runs `oc2 approve <name>`. Tier 2 re-validates the schema + DAG. If clean, locks for build. If dirty, prints errors and waits.
4. `oc2 build` runs only against an approved architecture.

For revisions mid-build: user edits `architecture.md`, runs `oc2 approve` again. The state machine (Section 4) detects which subsystems' specs changed and marks them + their transitive dependents as `pending`. Next `oc2 build` resumes from there.

### Pause / resume across sessions

Resume is the default behavior. `state.json` is the source of truth; every subsystem completion writes atomically. If the user kills `oc2 build` mid-subsystem, the in-progress subsystem is marked `failed_or_killed` next time `status` is run — the user decides whether to retry or revise.

---

## Section 2 — The architecture phase

### What happens when `oc2 design "build me an audit engine"` runs?

1. Resolve project name (auto from task or `--name`). Create `tier2_projects/<name>/` if absent.
2. Load project context via `oc_project.py` — but **scoped to this project's own name**, which suppresses operator-project context (see "Project context: suppress" below).
3. Single LLM call: system prompt + user task → markdown architecture. (I'm proposing single-call for v1. Two-call — design then critique — is reasonable but doubles cost; defer to v2 unless v1 quality is bad.)
4. Parse the returned markdown into a structured model.
5. Run validation (schema + DAG + sanity bounds).
6. If valid: write `architecture.md`. If a prior version exists, rotate it to `architecture.v<N>.md` first.
7. Print: "Architecture written to `tier2_projects/<name>/architecture.md`. Read it, edit if needed, then `oc2 approve <name>`."

### Project context: suppress (decided Session 10)

**Decision: Tier 2 builds and designs receive NO operator-project context.** A
Tier 2 subsystem builds from its ARCHITECTURE SPEC — purpose, I/O, integration
points — which `build_task_prompt` already injects. Per-project information a
subsystem needs belongs in `architecture.md` (the integration-points section),
not in the operator's default project. **The architecture IS the per-project
context, done right.** This is not a compromise versus synthesizing context —
it is the correct boundary.

*Why this was needed.* `oc_project.resolve_slug()` resolves CLI value > the
`OPENCLAW_PROJECT` env var > `DEFAULT_PROJECT='nexadose'`. Because `oc2` shelled
out to `ocb` (and `design` loaded context) without scoping a project, both fell
through to `nexadose`, so every Tier 2 project inherited Nexadose context — the
leak observed across csvmd (S6/S7: Nexadose columns in generated test data) and
numstat (S9: build log `project: nexadose`, architecture titled `nexadose-rx`).

*The lever (Tier-2-side only; Tier 1 untouched).* Both call sites scope the slug
to the **Tier 2 project's own name**, so `oc_project` looks for
`projects/<name>.md`, which does not exist, and returns an EMPTY context
(`project_name` = the Tier 2 name, no `raw_context`). ocb renders that as a
benign empty `PROJECT CONTEXT:` block — no guard or Tier-1 change needed.
- **build:** `OPENCLAW_PROJECT=<name>` set in the ocb subprocess env
  (`build._ocb_subprocess_env`; the subprocess env dict only, never the parent
  process env).
- **design:** `design._load_project_context(name)` passes the name as the
  resolve_slug CLI value.

*The synthesize seam (documented, not built).* If a future subsystem provably
needs project-level context not expressible in the architecture, **synthesize a
per-project `projects/<slug>.md`** from the architecture Overview + integration
points; the SAME lever (`OPENCLAW_PROJECT=<name>` / `resolve_slug(name)`) then
loads it automatically, no code change. Hook points: `build._ocb_subprocess_env`
and `design._load_project_context`. Caveat: naming a Tier 2 project after an
existing `projects/*.md` slug (e.g. `nexadose`) would re-load that context by
design — that is the seam working, not a regression.

### Architecture markdown schema (the minimum content for v1)

```markdown
# Architecture: <project name>

## Overview
One paragraph: what the system does, the problem it solves, the rough shape.

## Subsystems

### <subsystem-name>
**Purpose:** one paragraph.
**Inputs:** bulleted list (data, files, state, called functions).
**Outputs:** bulleted list (data, files, state, return values).
**Depends on:** comma-separated list of other subsystem names, or `none`.
**Owns state:** what persistent state this subsystem reads/writes; or `stateless`.
**Failure modes:** bulleted list of specific things that can go wrong here.

### <next-subsystem-name>
...

## Data flow
One paragraph narrative tracing a typical input through the system.

## Cross-cutting failure modes
Failures that span subsystems (e.g. "downstream service unavailable", "register file missing"). Each: what the system does in response.

## Integration points
Where subsystems meet — shared data schemas, message formats, file conventions, common types. The contracts between subsystems.
```

Constraints v1 enforces by validator:
- Between 2 and 10 subsystems.
- Every subsystem has all required fields (purpose, inputs, outputs, depends_on, owns_state, failure_modes).
- `depends_on` forms a DAG (no cycles, no self-deps, all referenced names exist in the doc).
- No subsystem named `main` (reserved).
- Subsystem names are kebab-case identifiers (alphanumeric + hyphens).

Soft warnings (don't block approval, just print):
- A subsystem with more than 4 deps (probably under-decomposed).
- A subsystem with no inputs AND no outputs (probably dead code).
- A single subsystem named after the whole project (probably under-decomposed).

### Why this shape

The schema is the contract between three audiences: the LLM that produces it, the human that reads/approves it, the Tier 2 build phase that consumes it to drive Tier 1. Each subsystem entry maps roughly 1:1 to one `ocb` invocation in the build phase — purpose becomes the task prompt, inputs/outputs/depends_on/owns_state become constraints on the generated code.

### Pre-review validation

Before the human reads the doc, Tier 2 runs:
- **Schema check** (every required field per subsystem)
- **DAG check** (Kahn's topological sort succeeds)
- **Closure check** (every `depends_on` references a subsystem in the doc)
- **Sanity bounds** (count in [2, 10], naming convention, no `main`)

Failures are printed inline. The doc is still written to disk so the human can read it and see what the LLM tried to produce. State is `architecture_invalid` until next `design` run.

### Human review

Markdown was the right format choice because the user reads it like prose. The doc is structured enough that the validator can parse it, loose enough that a domain expert can edit specific lines without breaking the parse.

Approval is explicit: `oc2 approve` re-parses + re-validates the (potentially edited) file. If it parses clean, state goes `approved`. If not, errors are printed.

There is no "approve with changes" or "approve subsystem X but not Y" in v1. The whole architecture is one unit.

---

## Section 3 — The build phase

### Order of operations

Topological by `depends_on`. Standard Kahn — start with subsystems that have no deps; advance frontier as deps complete.

Stable tiebreak when multiple subsystems are ready: alphabetical by name. (Avoids non-determinism in resume order across sessions.)

v1 is sequential — one subsystem at a time. Parallelism would be a nice v2 optimization but v1 prioritizes "easy to reason about" over throughput.

### Translating one subsystem entry to a Tier 1 invocation

Given a subsystem entry from `architecture.md`, Tier 2 constructs a task prompt for `ocb` and invokes it as a subprocess. The constructed prompt includes:

1. The subsystem's purpose paragraph (verbatim).
2. The inputs and outputs as a structured block.
3. The list of already-built dependency subsystems with their generated source available at `tier2_projects/<project>/subsystems/<dep>/`.
4. The "Integration points" section from the architecture doc, since contracts between subsystems live there.
5. The "Owns state" line — Tier 1 uses this to know whether the subsystem should read/write persistent state.

Concrete sketch:

```python
def build_task_prompt(subsystem_entry, dep_source_paths, integration_points):
    return f"""Build subsystem `{subsystem_entry.name}`. Purpose:
{subsystem_entry.purpose}

Inputs:
{format_bullets(subsystem_entry.inputs)}

Outputs:
{format_bullets(subsystem_entry.outputs)}

State this subsystem owns: {subsystem_entry.owns_state}

It depends on these already-built subsystems whose source is at the following
paths (read them if you need to understand the contracts they expose):
{format_dep_paths(dep_source_paths)}

Integration points (contracts between subsystems):
{integration_points}

Build as a single coherent script or as a small set of files. Do not split
unless the work is genuinely multi-file. Output to {subsystem_dir}/.
"""
```

Then: `subprocess.run(["python3", "tools/oc_builder.py", task_prompt])` and watch for exit code + state log.

On success: copy generated files from the ocb run dir into `tier2_projects/<project>/subsystems/<name>/`. Update `state.json` with the subsystem's status, the ocb `run_id`, the source file list.

### What Tier 2 does when Tier 1 fails

Tier 1 (`ocb`) already has its own retry budgets — AST self-heal, main-guard regen, contract verification — and surfaces a clean diagnostic when it hard-fails. **Tier 2 v1 does not auto-retry beyond what Tier 1 already does.**

If `ocb` exits non-zero:
- Capture the run_id and which Phase 3 gate failed (read it out of `logs/oc_build_<ts>.json`).
- Update `state.json`: this subsystem is `failed` with the gate + traceback recorded.
- Stop the build. Do not advance to dependent subsystems.
- Print to stdout: "Subsystem X failed at gate Y. See `tier2_projects/<project>/runs/<ts>.log` and the ocb state log at `logs/oc_build_<ts>.json`. Decide: revise architecture (edit `architecture.md` + `oc2 approve` + `oc2 build`), or re-run this subsystem only (`oc2 build --only X`)."

Note: `oc2 build --only X` is a v1 escape hatch for re-running a single subsystem without revising the architecture. Useful when the failure is stochastic (LLM was unlucky) vs. structural.

### Subsystem status tracking

`state.json` holds a dict keyed by subsystem name:
```json
{
  "subsystems": {
    "thread-reader": {"status": "done", "ocb_run_id": "run_1779...", "built_at": "...", "source_sha": "..."},
    "key-grouper":   {"status": "in_progress", "started_at": "..."},
    "summary-writer": {"status": "pending"}
  }
}
```

Status enum: `pending` / `in_progress` / `done` / `failed` / `failed_or_killed` (the last is for "user killed mid-build, we don't know what state ocb left things in").

### What happens after all subsystems are built

In v1: minimum integration test = "try to import each subsystem's main entry from a single Python process in topological order, capture any ImportError or runtime errors". If it succeeds, write `BUILD_REPORT.md` listing what got built. If it fails, surface the error and treat it like a Tier 1 failure on the last subsystem.

No automated end-to-end test in v1. The user runs the system manually for that.

---

## Section 4 — State and persistence

### What state Tier 2 maintains

**`architecture.md`** — current design, human-readable + editable. Version-rotated to `architecture.v<N>.md` on each `design` run.

**`state.json`** — the source of truth for build state.

```json
{
  "state_schema_version": 1,
  "project_name": "audit-engine",
  "architecture": {
    "current_path": "architecture.md",
    "current_sha256": "abc123...",
    "version_count": 3
  },
  "approval": {
    "approved": true,
    "approved_at": "2026-05-21T18:00:00Z",
    "approved_architecture_sha256": "abc123..."
  },
  "subsystems": {
    "<name>": {
      "status": "done|pending|in_progress|failed|failed_or_killed",
      "spec_sha256": "...",          // SHA of just this subsystem's section in the architecture
      "ocb_run_id": "run_177...",    // null if not yet built
      "source_dir": "subsystems/<name>/",
      "built_at": "...",
      "error": null                  // populated when status == failed
    }
  },
  "history": [
    {"event": "design", "at": "...", "architecture_sha256": "..."},
    {"event": "approve", "at": "...", "architecture_sha256": "..."},
    {"event": "build_start", "at": "..."},
    {"event": "subsystem_done", "name": "...", "at": "..."},
    {"event": "build_pause", "at": "...", "reason": "..."}
  ],
  "last_session_at": "..."
}
```

**`runs/<iso_timestamp>.log`** — orchestration log for each `oc2 build` invocation. Plain text. Includes the ocb run_ids referenced.

**`subsystems/<name>/*.py`** — the actual generated code, copied from the ocb run dirs.

**`BUILD_REPORT.md`** — written on full completion: subsystem list with status and ocb run_id, total wall-clock time, any warnings.

### Storage location and git policy

All under `tier2_projects/<name>/`. `architecture.md`, `state.json`, `subsystems/`, `BUILD_REPORT.md` are git-tracked — the user wants version history of the design AND the generated code.

`runs/*.log` are not git-tracked (gitignored, like `logs/`).

`.archive/` is git-tracked because archiving is the user's intent to preserve.

### Detecting architecture changes

Per-subsystem `spec_sha256` is the SHA of just that subsystem's markdown section (extracted by the parser). On `oc2 approve` re-run:

1. Re-parse `architecture.md`.
2. For each subsystem now in the doc, compute the new `spec_sha256`.
3. Compare to the stored `spec_sha256` in `state.json`.
4. If different: mark this subsystem as `pending`. Also mark every subsystem that depends on it (transitively) as `pending`.
5. If a subsystem was removed: mark it for deletion (move to `.archive/<name>/`).
6. If a subsystem is new: add as `pending`.
7. Re-validate the whole DAG.
8. Write updated `state.json`.

This is essentially "make" semantics — only rebuild what's changed downstream of changes.

There is **no architecture history beyond `architecture.v<N>.md` files**. If the user wants to revert to an earlier architecture, they manually copy the old file over `architecture.md` and re-approve.

---

## Section 5 — Failure modes and responses

| # | Failure | v1 response |
|---|---------|-------------|
| 1 | Architecture LLM produces invalid output (broken schema, cycle in DAG, missing fields) | Validator surfaces errors; doc is written so user can see what the LLM tried; state is `architecture_invalid`. User options: re-run `oc2 design` (regenerates from the LLM), or hand-edit the doc and run `oc2 approve`. |
| 2 | User approves architecture with a subtle flaw not caught at validation | Will manifest as a Tier 1 failure on some downstream subsystem or as an integration smoke failure. v1 surfaces the symptom to the user; user decides whether to revise the architecture. **No auto-rearchitecting in v1.** |
| 3 | Tier 1 fails to build a subsystem after its own retries | `oc2 build` pauses. State updated to `failed` with gate name + traceback path. User options: revise architecture and re-approve (triggers cascading rebuild), or `oc2 build --only <subsystem>` to retry just that subsystem (in case it was stochastic). |
| 4 | Mid-build, user wants to revise the architecture | User edits `architecture.md`, runs `oc2 approve`. State machine recomputes which subsystems are affected; marks them + transitive dependents `pending`. Already-built non-affected subsystems are preserved. Next `oc2 build` continues from the first pending in topological order. |
| 5 | Subsystem builds successfully but interface doesn't mesh with downstream | Caught when the downstream subsystem builds — Tier 1's contract verification, lint, dep-honesty, or entry-execution will catch the mismatch. Same path as #3: pause, surface, ask user to revise. v1 doesn't try to back out the upstream subsystem automatically. |
| 6 | Model produces a subsystem that's correct in isolation but doesn't fit the architecture spec | Tier 1's contract verification already checks declared exports against generated code. Signature/type mismatches between declared and actual function shapes — Tier 1 doesn't catch, Tier 2 v1 doesn't either. **This is the known v1 gap.** Defer to Tier 2 v2 or a later Tier 1 typecheck. |

The pattern: **v1 is human-in-the-loop on every non-trivial failure.** Tier 2's job is to surface the failure with enough context for a human to make a good decision. Automating decisions is v2's job; v1's job is to make the manual loop fast and unambiguous.

---

## Section 6 — What Tier 2 v1 explicitly does NOT do

Confirming the suggested non-goals and adding a few:

| Non-goal | v1 stance | Reasoning |
|----------|-----------|-----------|
| Auto-rearchitecting on build failures | Confirmed | Human decides. Auto-rearchitecting is a v2 problem (and a hard one). |
| Subsystem-level live-reload or hot-swap | Confirmed | Architecture change → cascading rebuild from `state.json`. No incremental patching. |
| Cross-project knowledge transfer | Confirmed | Each project is independent. Project context is SUPPRESSED in Tier 2 (S10): a project builds from its own `architecture.md`, not the operator's default `oc_project.py` context. See "Project context: suppress". |
| GUI | Confirmed | CLI only. Markdown for human review. |
| Sophisticated integration testing | Mostly confirmed | v1 ships a minimal "import everything in topological order" smoke. Anything beyond that is the user's problem until v2. |
| Multi-LLM orchestration or model selection per subsystem | Confirmed | One model. Same as Tier 1 (`qwen2.5-coder:32b`). Architecture phase MIGHT benefit from a reasoning model — see Open Questions. |
| Parallel subsystem building | **Added non-goal** | v1 is strictly sequential. Parallelism is a v2 throughput optimization. |
| Subsystem versioning beyond architecture re-runs | **Added non-goal** | When you re-run `oc2 design`, the old architecture archives to `architecture.v<N>.md`. No per-subsystem branching or A/B comparison. |
| Cross-project subsystem reuse | **Added non-goal** | If two projects need similar subsystems, the user copies code manually. Library-style sharing is v3 territory. |
| Smart project name inference | **Added non-goal** | Auto-name is "first 4 words of task, kebab-cased". User can `--name` it. No LLM-assisted naming. |
| Project deletion or rename CLI | **Added non-goal** | Manual filesystem operations. `oc2 archive` is in-scope; rename/delete is not. |

The cumulative effect: **v1 is a CLI for one human, one project at a time, with the human as the supervisor of every non-trivial decision.** That's intentional. It's the minimum coherent shape that lets us validate the architecture-first approach without building infrastructure that's only justified once we know the basic loop works.

---

## Section 7 — Validation plan: how do we know v1 works?

Tier 2 v1 needs a test project. Three candidates:

### Candidate A — "Daily snapshot reporter"

**What it is:** A tool that runs each morning (cron or manual), reads `WORK_ITEMS_REGISTER`, runs the existing `today10`, generates a per-category breakdown, optionally emails or files the result.

**Subsystems (~4):**
- `register-reader` — load the CSV, expose typed rows
- `top10-runner` — invoke `today_top10.py` (already exists), capture output
- `breakdown-generator` — per-category counts and trends
- `output-formatter` — combine top10 + breakdown into one daily-brief markdown

**Why it's a good test:** Closest to what we just shipped. Failures would be Tier 2 orchestration issues rather than domain modeling issues. The first subsystem already has a working reference (`today_top10.py`); the others are simple aggregations. Failure surface is bounded.

**Why it might be too easy:** It's almost a single-file refactor. Doesn't exercise Tier 2's harder cases (cascading rebuild on architecture change, signature mismatch between subsystems).

### Candidate B — "Email-thread reconciler (small slice)"

**What it is:** Read a small set of email threads, group by `canonical_key`, output a markdown summary. A toy version of the eventual audit engine.

**Subsystems (~3-4):**
- `thread-reader` — load emails from `email_raw/` directory
- `key-grouper` — group by canonical_key, surface conflicts
- `summary-writer` — markdown report

**Why it's a good test:** Genuine path toward the audit engine. Whatever architectural learnings we get from this transfer directly to the bigger project.

**Why it might be too risky:** Small scope but the email_raw/ structure I haven't audited. The domain modeling is genuinely harder than Candidate A — easy for me to under-spec.

### Candidate C — "CSV-to-markdown converter framework"

**What it is:** A generic CSV-to-markdown converter with: csv-loader, schema-detector, table-formatter, output-writer.

**Subsystems (~4):**
- `csv-loader` — robust CSV reading with type detection
- `schema-detector` — infer column meaning from heuristics
- `table-formatter` — render as markdown table with configurable columns
- `output-writer` — write to file or stdout

**Why it's a good test:** Pedagogical. Close to existing Tier 1 patterns. Each subsystem is small.

**Why it might be too generic:** Not a real Nexadose need. We'd be validating Tier 2 against synthetic work, which has lower stakes but also lower information value.

### Selection criteria

- **Real enough that "it works" means something to you** — not a toy.
- **Small enough to build in 2-3 sessions after Tier 2 v1 ships.**
- **Diverse enough subsystems** (3-5) to exercise the orchestration logic but not so many that failures are death-by-a-thousand-cuts.

**My recommendation: Candidate A (Daily snapshot reporter).** It's the safer choice and uses code we just shipped (`today_top10.py`) as one of the subsystems, which means a working reference is in scope from day one. If A goes smoothly, Candidate B is the natural follow-up. Candidate C is the fallback if both A and B turn out to have hidden domain issues.

The bar for "Tier 2 v1 works": the test project completes end-to-end across at least one full session pause/resume cycle, and the final output (the daily brief markdown) is something you'd want to read.

---

## Section 8 — Implementation scoping

### Sessions estimate

**3-5 Claude Code sessions to implement Tier 2 v1 to "ready for the test project" state**, based on the Tier 1 implementation pace (8 engineering commits across 7 sweeps over a similar number of sessions).

Rough breakdown:
- **Session 1:** CLI scaffolding (`oc2 design/list/status/approve/build` with placeholders), `tier2_projects/` layout, architecture schema + validator. Mocked LLM call for early testing.
- **Session 2:** Live `oc2 design` against ollama, parsing the LLM markdown response into structured form, edge cases in the validator.
- **Session 3:** `oc2 approve` + `state.json` persistence + spec_sha256 + cascading rebuild logic.
- **Session 4:** `oc2 build` topological execution, subsystem-to-ocb prompt translator, ocb subprocess wrapper with state-log harvesting.
- **Session 5:** Failure handling + `--only` escape hatch + integration smoke + dogfood against Candidate A.

This estimate is for "ready for the test project," not "production-quality." Some rough edges will surface during the test project and need a follow-up session.

### Highest implementation risk

Ranked by my uncertainty about how hard these will actually be:

1. **The architecture-phase LLM prompt.** Has to elicit structured markdown that's also human-readable. The schema needs to be tight enough that the validator can parse reliably but loose enough that the LLM doesn't tie itself in knots producing it. This is the #1 risk — if the LLM produces inconsistent shapes we'll spend a session iterating on the prompt.
2. **Cascading rebuild logic.** State machine for "user edited 2 subsystems, transitive dependents need invalidating, but already-built non-affected subsystems stay." Correct under interruption — the user kills mid-rebuild and restarts. Atomic state.json updates. Easy to get wrong on edge cases.
3. **Subsystem-to-Tier-1-prompt translator.** Text manipulation, but quality-sensitive. The prompt is the contract between Tier 2 and Tier 1; if we pass a bad prompt, Tier 1 produces bad subsystems and Tier 2 looks broken when it's actually a prompt-quality issue.
4. **Integration smoke runner.** Sounds simple ("import all subsystems") but in practice has to handle: subsystems with side effects, subsystems with absent runtime deps (file paths, env vars), subsystems whose entry isn't named `main`. Bounded but fiddly.

Lower-risk pieces: CLI dispatcher (argparse), state.json persistence (json + Path), git-aware project archive (subprocess + shutil), ntfy hooks (already shipped), atexit save patterns (already shipped).

### What Tier 1 code can be reused

Direct reuse (import / call):
- `oc_project.py` (`resolve_slug`, `load_project`) — same project context loader.
- `tools/notify.sh` — ntfy already wired.
- The atexit run-dir archive pattern from `oc_builder.py:archive_run_dir` — Tier 2 can archive per-session state similarly.

Shape reuse (copy and adapt):
- The manifest validation pattern from `validate_manifest` — DAG check, schema fields, sanity bounds.
- The `_collect_top_level_names` AST helper — useful for the integration smoke runner.
- The state-dump pattern (`dump_state`) for atomic JSON writes.

Net-new code (no Tier 1 equivalent):
- Architecture-markdown parser.
- The CLI command dispatcher (Tier 1 is single-mode).
- The cascading rebuild state machine.
- The subsystem-to-ocb-prompt translator.
- The integration smoke runner.

### Location in the codebase

**Proposed: `tools/oc2/` as its own package.**

```
tools/oc2/
├── __init__.py
├── __main__.py          # `python -m oc2`-style entry
├── cli.py               # argparse dispatcher
├── design.py            # `oc2 design` flow
├── approve.py           # `oc2 approve` flow
├── build.py             # `oc2 build` flow
├── status.py            # `oc2 status`, `oc2 list`
├── architecture.py      # markdown schema parser + validator
├── state.py             # state.json read/write
├── prompt.py            # subsystem-to-ocb-prompt translator
├── smoke.py             # integration smoke runner
└── tests/               # unit tests as we go
```

Alias: `oc2='python3 /root/.openclaw/workspace/tools/oc2/cli.py'` in `~/.bashrc`, parallel to `ocb`.

Reasoning for own directory: the codebase is large enough that mixing Tier 2 logic with `oc_builder.py` would create import cycles (Tier 2 imports `oc_builder` to subprocess it; if `oc_builder` ever needed to import Tier 2 utilities, we'd be stuck). Clean separation now.

---

## Open questions for the user

These are the design decisions where I want your judgment before implementation starts.

### 1. Command name: `oc2` or `ocplan`?
Both are defensible. I'm proposing `oc2` for parallel with `ocb`, accepting it's opaque. Flip to `ocplan` if you find the opacity worse than the muscle-memory benefit.

### 2. Test project: Candidate A, B, or C?
I'm leaning A (Daily snapshot reporter). B (Email reconciler) is more aspirational and transfers architecturally to the audit engine. C (CSV converter) is the safest if A or B turn out to have hidden issues. Pick based on appetite for risk vs forward-progress.

### 3. How verbose should the architecture markdown be?
v1 currently leans toward "full prose": purpose paragraph + bulleted inputs/outputs + failure modes per subsystem. The doc is what humans read to approve, so brevity at the cost of clarity defeats the point. But "full prose" means a 4-subsystem project's architecture.md is ~3-4 KB of markdown. Is that the right scale, or should subsystem entries be tighter (e.g. 1-2 sentence purpose, terse bullets only)?

### 4. Where to draw the line on "minimum integration test"?
Three options:
- (a) No integration test. Ship if all subsystems built.
- (b) Minimal smoke: import each subsystem's main entry in topological order, capture errors.
- (c) Lightweight assertion against the architecture's data-flow narrative (e.g. "feed a single canonical input through subsystems in order, assert no exception").

I'm proposing (b). (c) is conceptually nicer but harder to operationalize generically. (a) feels like under-shipping.

### 5. Should `oc2 design` use a different (larger / reasoning-focused) model than Tier 1?
Architecture is more about decomposition and naming than about Python idioms. A reasoning-focused model could be better. But introducing a new model is a real dependency. v1 default: same model as Tier 1 (`qwen2.5-coder:32b`). Flip only if v1's architectures are visibly weak.

### 6. State.json schema versioning from day one?
I've included `state_schema_version: 1` in the schema. Cheap to add, useful for future migrations. Just confirming this isn't over-engineering for v1 — I don't think it is, but the user might.

### 7. How does Tier 2 handle architecture revisions to a subsystem that has already built downstream dependents?
v1's cascading rebuild marks the changed subsystem + transitive dependents as `pending`. The build phase then rebuilds them. **This means the user implicitly loses downstream work when they revise an upstream subsystem.** That's the correct behavior, but worth confirming you're okay with it — alternative is "warn loudly + require `--force-cascade`".

### 8. Project name conflicts?
What happens if `oc2 design "..."` produces a project name that already exists in `tier2_projects/`? v1 options:
- (a) Append `-N` suffix automatically.
- (b) Refuse and require `--name`.
- (c) Overwrite (after a confirmation prompt).

I'm leaning (b). Conservative; predictable; cheap.

### 9. Should Tier 2 expose its own ntfy events?
Tier 1 sends ntfy on session end, sweep done, and commit-pushed events. Tier 2 build runs could send ntfy on: subsystem-built, build-paused-on-failure, build-complete. The user already has the helper. Just confirming v1 should wire this in (yes, probably — but not free).

---

## Appendix — Things I considered and rejected for v1

- **A real DSL for architectures (YAML/JSON schema with formal validation).** Considered. Rejected because markdown is what the human reads. JSON-with-comments-and-required-fields is harder to edit by hand and worse for review.
- **LLM-driven architecture critique pass (two LLM calls in design phase).** Considered. Deferred to v2 unless v1 architecture quality is visibly bad. Cost-doubling on every design.
- **Per-subsystem unit-test generation.** Considered. Rejected — Tier 1's smoke-test handling is already complicated; piling on more here is scope creep. v2 can revisit.
- **Web UI for design review.** Hard pass for v1. Maybe ever.
- **`oc2 fork <project>` to branch architectures.** Considered. Rejected for v1. The user can `cp -r` if they need branching.

---

**End of design document. Awaiting human review.**

The numbered open questions above are where I need your judgment before implementation. After we work through those, the next session is the implementation brief (Session 1 from the breakdown in Section 8).
