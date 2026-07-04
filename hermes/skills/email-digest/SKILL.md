---
name: email-digest
description: "Use when the user wants a morning triage digest of exported emails for a facility-build project. Reads exported .eml/.txt files from a directory, classifies each locally (status + category + summary + next step) via Ollama, and writes a mobile-scannable markdown digest grouped by status. READ-ONLY and LOCAL-ONLY: it never sends, deletes, labels, moves, or replies to email, and makes no cloud calls."
version: 1.0.0
author: OpenClaw
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [email, triage, digest, nexadose, ollama, local, read-only, subprocess]
    requires_toolsets: [terminal]
    related_skills: [generate-code]
---

# Email Digest (NexaDose morning triage)

## Overview

This skill turns a folder of exported emails into a single mobile-scannable
**morning digest**. It runs the standalone `digest.py` module as a subprocess;
that module reads exported `.eml`/`.txt` files, classifies each email locally
with an Ollama model, and writes a dated markdown digest grouped by status. **It
never writes the digest itself and never touches a mailbox.**

**Hard guarantees (proven by the module's test suite):**

- **READ-ONLY** — it never sends, deletes, labels, moves, or replies to any
  email. v1 reads exported files only; there is no live mailbox connection.
- **LOCAL-ONLY** — the only network call is to the local Ollama endpoint on
  `localhost:11434`. No cloud APIs.
- **CONFIG-DRIVEN** — the taxonomy (status/category enums, ordering, model,
  paths) is loaded from a YAML config. The machinery is tenant-agnostic; point
  it at a different config to serve a different tenant.

The taxonomy is fixed in config:

- **Status:** `WAITING_ON_YOU`, `REVIEW`, `OPEN`, `WAITING`, `BLOCKED`
- **Category:** `PERMITS_INSPECTIONS`, `ENGINEERING`, `FINANCE`,
  `VENDORS_EQUIPMENT`, `LEGAL`, `CLEANROOM_USP`, `UNCLASSIFIED`

## When to Use

- The user asks for a "morning digest", "email triage", or "what needs my
  attention" over a folder of exported emails for the NexaDose facility build.
- The user has dropped exported `.eml`/`.txt` files into the inbox export folder
  and wants them classified and summarized.

**Don't use for:** anything that acts on a mailbox (sending, replying, deleting,
labeling), live IMAP/SMTP access, or non-email tasks. This skill is read-only.

## How to Run It

The wrapper lives next to this skill. Run it via the **terminal** toolset.
`SKILL_DIR` is the directory containing this `SKILL.md`.

```bash
WRAP="$SKILL_DIR/scripts/run_digest.sh"

# Default run — uses config/nexadose.yaml, inbox_export/, digests/, today:
bash "$WRAP"

# Pin a date (useful for reproducible runs / backfill):
bash "$WRAP" --date 2026-07-04

# Override input/output/config for another tenant or a one-off:
bash "$WRAP" --config /path/to/other.yaml --input /path/to/exported --output /path/to/out
```

The wrapper `cd`s into the workspace and `exec`s `digest.py`, so its stdout and
exit code pass straight through. Exit 0 = success; it prints the path of the
written digest (`digests/<date>_digest.md`).

## Notes & Constraints

- **Cold start.** The first Ollama call loads the model into VRAM (30–90s is
  normal, not a hang). Be patient; do not retry on silence.
- **Deterministic parse, model classify.** File parsing is deterministic; the
  status/category/summary come from the local model. Invalid model output is
  retried once, then falls back to `UNCLASSIFIED`/`OPEN` — a hallucinated label
  can never reach the digest.
- **Workspace path:** defaults to `/root/.openclaw/workspace`; override with
  `OPENCLAW_WS=/path bash "$WRAP" ...`.

## Common Pitfalls

1. **Expecting it to act on email.** It cannot and must not. It reads exported
   files and writes a markdown file — nothing else.
2. **Treating a slow first call as a hang.** See the cold-start note.
3. **Editing the taxonomy in code.** Don't. Edit `config/nexadose.yaml`; the
   enums, ordering, model, and paths all come from there.

## Verification Checklist

- [ ] `bash "$SKILL_DIR/scripts/run_digest.sh" --date <YYYY-MM-DD>` exits 0 and
      prints `wrote .../digests/<date>_digest.md`.
- [ ] The written digest has a `## WAITING_ON_YOU` section first (when present),
      then `BLOCKED`, `REVIEW`, `OPEN`, `WAITING`.
- [ ] Each email appears exactly once as a `- CATEGORY — summary — next:` line.

## One-Shot Recipe — generate today's digest

```bash
bash "$SKILL_DIR/scripts/run_digest.sh"
# Expect: "[digest] wrote /root/.openclaw/workspace/digests/<today>_digest.md"
```
