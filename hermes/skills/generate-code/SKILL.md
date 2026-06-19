---
name: generate-code
description: "Use when the user wants to generate working code from a plain-English spec via the local OpenClaw engine (Tier 1 single-file, or Tier 2 multi-subsystem projects). Runs the engine as a subprocess; does not write code itself."
version: 1.0.0
author: OpenClaw
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [codegen, openclaw, ollama, local, subprocess]
    requires_toolsets: [terminal]
    related_skills: [plan, requesting-code-review]
---

# Generate Code (OpenClaw engine)

## Overview

This skill turns a plain-English specification into working, gate-checked code by
invoking the local **OpenClaw** code-generation engine as a subprocess. All
generation happens locally via qwen2.5-coder:32b on Ollama. The skill is a thin
wrapper: it shells out to the engine and returns the result. **It never writes the
code itself and never edits or patches the engine.**

There are two tiers:

- **Tier 1** (`tools/oc_builder.py`, alias `ocb`) — one self-contained spec → a single
  file (plus its helper/test files), run through the engine's validation gates.
- **Tier 2** (`tools/oc2/__main__.py`, alias `oc2`) — a larger spec → an architecture of
  multiple subsystems built in dependency order, with an integration smoke runner.

## When to Use

- The user gives a spec like "build a CSV-to-markdown converter" or "a function that
  parses ISO durations" and wants real, runnable code generated locally.
- The user wants to (re)build, approve, or smoke-test an existing OpenClaw Tier 2
  project (e.g. `csvmd`, `numstat`).

**Don't use for:** editing the OpenClaw engine itself, hand-writing code inline, or any
task where the user just wants you to answer directly without generating a project.

## How to Run It

The wrapper lives next to this skill. Run it via the **terminal** toolset.
`SKILL_DIR` is the directory containing this `SKILL.md`.

```bash
WRAP="$SKILL_DIR/scripts/generate_code.sh"

# Tier 1 — single-file build from a spec (optional project slug for context):
bash "$WRAP" tier1 "a function that returns the nth Fibonacci number"
bash "$WRAP" tier1 "<spec>" <project_slug>

# Tier 2 — full project flow:
bash "$WRAP" design  "build a csv-to-markdown converter" csvmd   # spec -> architecture.md
bash "$WRAP" approve csvmd                                       # validate + approve
bash "$WRAP" build   csvmd                                       # build all subsystems (topo order)
bash "$WRAP" build   csvmd --only file-reader                   # rebuild one subsystem
bash "$WRAP" smoke   csvmd                                       # integration smoke (no LLM)
bash "$WRAP" status  csvmd                                       # project status
```

The wrapper `cd`s into the workspace and `exec`s the engine, so the engine's stdout and
exit code pass straight through. Exit 0 = success. Report the engine's output verbatim.

## Notes & Constraints

- **Generation is slow.** First Ollama call loads ~19GB into VRAM (30–90s is normal, not a
  hang). Each Tier-1/subsystem build can take several minutes. Be patient; do not retry on
  silence.
- **Determinism:** `smoke` runs the already-built project's code with no LLM call — it is
  fast and deterministic. Use it to verify a project is green.
- **Workspace path:** defaults to `/root/.openclaw/workspace`; override with
  `OPENCLAW_WS=/path bash "$WRAP" ...` if the repo is elsewhere.

## Common Pitfalls

1. **Editing the engine to make a build pass.** Never. The engine (`tools/oc_builder.py`,
   `tools/oc2/*`, `tools/oc_project.py`) is protected. If a build fails, report the engine's
   diagnostic — do not patch generated code or the engine to force a gate green.
2. **Retry loops.** One diagnostic retry max on a failed build, then stop and report.
3. **Treating a slow first call as a hang.** See the cold-start note above.
4. **Wrong arg order for `design`.** It is `design "<spec>" <name>` (spec first, then the
   project name) — the wrapper maps this to `oc2 design "<spec>" --name <name>`.

## Verification Checklist

- [ ] `bash "$SKILL_DIR/scripts/generate_code.sh" smoke csvmd` exits 0 and prints
      `PASS: DAG composes end-to-end`.
- [ ] For a new build, the engine reports the subsystem(s) `done` and exit code is 0.
- [ ] No engine files were modified (`git status` under the workspace is clean of `tools/` edits).

## One-Shot Recipe — smoke-test the reference project

```bash
bash "$SKILL_DIR/scripts/generate_code.sh" smoke csvmd
# Expect: "[oc2 smoke] PASS: DAG composes end-to-end on disk (...); all 7 expected item(s) present"
```
