# Tier 1 — Regen-with-error-feedback loop (v1 scoping)

**Status: SCOPING.** This is a design/recommendation document, not an
implementation. It ends in explicit open questions (Section 7) that need human
judgment before any `oc_builder.py` code is written. No Tier-1 code was touched
to produce it (engine stays at `05891c0`).

This is the first proposed change to `oc_builder.py` since it was declared
**STRUCTURALLY COMPLETE** (v7 sweep). Section 5 answers whether that verdict
survives. Read it before deciding to proceed.

---

## 1. Motivation (the evidence)

The engine's gates catch failures with precise diagnostics, then **halt**
(`sys.exit(1)`). A class of real failures is a *single localized inconsistency*
in otherwise-working generated code — the kind a human fixes in one line, and the
kind no additional structural gate can catch (you would chase infinitely many
bug-classes). Concrete cases on record:

- **histogram-builder, 3 rolls, 3 different gates (S9/S11):**
  - roll 1+2 (`run_1780267356`, `run_1780267494`): `dep_honesty` — the generated
    `utils.py` contains `from utils import <its-own-fns>`, a self-import.
  - roll 3 (`run_1780273462`): `smoke_tests` — the model's own `smoke_test.py`
    asserts `"No arguments provided" in stdout`, but the entry prints
    `"Histogram builder self-check passed."` (and exits 0). Every other gate
    passed; it died on a one-line assertion mismatch.
  Each failure is a single fixable inconsistency. A per-bug structural gate would
  not generalize across the three; a corrective re-prompt plausibly fixes each.

- **The Step-1 I/O one-liner (the pinned archetype):** an 81-line single-file
  script, all gates green through dry-import, then `entry_execution` exited 1 with
  `ValueError: I/O operation on closed file` (a `csv.reader` reused outside its
  `with` block). "One line away from working." This is the canonical
  regen-with-feedback test case.

The thesis: **feed the failing gate's error back to the model for one corrective
regeneration, before halting.** The engine already does exactly this in two
places (Section 2) — this generalizes the pattern to the gates that lack it.

---

## 2. What the engine already does (the proven precedents)

The feedback loop is **not net-new machinery**. Two existing mechanisms are the
template; the proposal extends them, it does not invent a pattern.

**(a) AST self-heal — `generate_one_file` (oc_builder.py:691).** Phase 2, per
file. On an AST-parse failure it appends the broken code + the parse error to the
conversation and asks for a corrected file, up to `MAX_RETRIES = 3` attempts:

```
messages.append({"role": "assistant", "content": code})
messages.append({"role": "user", "content":
    f"That file failed to parse:\n{err}\n\nReturn the complete corrected file..."})
```

**(b) Main-guard regeneration — `regenerate_for_main_guards` (oc_builder.py:867).**
Phase 3, the `main_guards` gate. A bounded loop (`MAIN_GUARD_RETRY_BUDGET = 2` per
file) that builds a 4-message conversation — system prompt + original task +
prior code + a *specific corrective message* naming the exact violation — then
regenerates and re-verifies (and re-checks `contracts` after, oc_builder.py:1662):

```
messages = [
    {"role": "system",    "content": system},
    {"role": "user",      "content": task},
    {"role": "assistant", "content": prior_code},
    {"role": "user",      "content": corrective},   # names the exact violation + fix
]
```

This is precisely the shape the new loop needs. The proposal = **the
`regenerate_for_main_guards` template, generalized to the later Phase-3 gates that
currently halt without it.**

---

## 3. The current Phase 3 pipeline (gate-by-gate)

In `build()` (oc_builder.py:1575-1761). Each gate records into a `cross` dict and,
on failure, dumps state and `sys.exit(1)`. The failure *shape* is heterogeneous —
this matters for both the corrective-prompt builder and the `_extract_failed_gate`
fix (Section 4.3).

| # | Gate (loc) | state-log shape | on fail | feedback today |
|---|---|---|---|---|
| — | AST self-heal (Phase 2, :704) | per-file attempts | retry ≤3 w/ error | **yes** |
| 1 | contracts (:1582) | dict `{ok, results}` | halt | no |
| 2 | main_guards (:1616) | dict `{ok, results}` + `main_guard_attempts` | **loop regen ≤2/file** then halt | **yes** |
| 3 | dep_honesty (:1676) | dict `{ok, cycle, self_imports, results}` | halt | no |
| 4 | static_lint (:1707) | dict `{ok, errors}` | halt | no |
| 5 | dry_import (:1718) | dict `{ok, stdout, stderr}` | halt | no |
| 6 | entry_execution (:1728) | dict `{rc, stdout, stderr}` — **no `ok`** | halt | no |
| 7 | smoke_tests (:1748) | **list** `[{path, rc, stdout, stderr}]` — **no `ok`** | halt | no |

Global backstop: `check_cap()` aborts the whole run at `RUN_CAP_SECONDS = 1200`.
Each LLM round-trip is up to `PER_LLM_TIMEOUT = 300`s. So feedback regens are
*expensive against the cap* — a hard constraint on the budget (Section 4.2).

Observation: the gates that the motivating failures hit — **dep_honesty,
entry_execution, smoke_tests** (and dry_import, by family) — are exactly the ones
in rows 3-7 that **halt without any feedback**. contracts and main_guards already
have recovery; static_lint is structural.

---

## 4. The design questions, answered

### 4.A — WHERE does the loop hook in?

**Candidates evaluated against the code:**

- **(i) Whole-Phase-3 wrapper.** Any gate fails → re-prompt → regenerate →
  re-run Phase 3 from the top. *Pro:* one mental model. *Con:* a single gate
  failure rarely implicates all files; ambiguous *which* file to regenerate;
  re-runs everything. The blast radius is the entire phase per attempt.
- **(ii) Per-gate feedback.** Each target gate, at its failure branch, attempts a
  bounded corrective regen before halting — generalizing `main_guards` exactly.
  *Pro:* the failing gate already knows the file + error; surgical; matches
  precedent. *Con:* touches each target gate's halt path (mechanical).
- **(iii) Targeted subset.** Only the gates where localized-inconsistency
  failures cluster: **dep_honesty, dry_import, entry_execution, smoke_tests** —
  the runtime/consistency back-half. Skip contracts (already re-checked in the
  main-guard loop) and static_lint (structural; failures usually need
  re-architecture, low regen yield).

**Recommendation: (iii) scope ⊕ (ii) mechanism, with a full Phase-3 re-verify
after each accepted regen.** Concretely:
- **Trigger per-gate** on the four runtime/consistency gates (dep_honesty,
  dry_import, entry_execution, smoke_tests), reusing the
  `regenerate_for_main_guards` template.
- After an accepted regeneration, **re-run Phase 3 from the top** (contracts
  onward), exactly as the main-guard loop re-verifies contracts after a regen —
  so a fix that reintroduces an earlier-gate violation is caught, not smuggled
  through. Bounded by the global budget (4.C) so this cannot spin.
- This combines (ii)'s surgical trigger (the error + file are in hand at the halt
  site) with (i)'s safe re-verify, while excluding the low-yield structural gates.

*Tradeoff stated:* (i) is conceptually simpler but re-runs everything and is
ambiguous about the regen target; (ii)/(iii) is more code but surgical, precedent-
aligned, and the trigger sites already hold the exact diagnostic.

### 4.B — WHAT goes in the corrective re-prompt?

Mirror `regenerate_for_main_guards`: `[system (per-file prompt), user (task),
assistant (prior_code), user (corrective)]`. The corrective message =
**the gate's specific failure text + the source of the file to fix + a "fix ONLY
this, change nothing else" instruction.** Per-gate extraction:

- **dep_honesty:** "`<file>` imports from itself: `from <mod> import X` where X is
  defined in this same module. Remove the self-import and reference the local
  definitions directly." (+ cycle path if `cycle` is set.) Target = the
  self-importing file (`self_imports[0]`).
- **dry_import:** the captured `stderr` traceback. Target = the file named in the
  traceback's last in-`run_dir` frame (fallback: the entry point).
- **entry_execution:** the `stderr` traceback + `rc` (e.g. the I/O one-liner's
  `ValueError`). Target = entry point (or the traceback's last in-run_dir frame).
- **smoke_tests:** the failing test's `stderr` (e.g. the `AssertionError`) + the
  test path. This one is *two-sided* — the entry's behavior and the test's
  expectation disagree. The corrective must include BOTH the entry source and the
  test source and say: "the test and the code under test disagree; make them
  consistent — usually align the test's expectation to the code's actual output,
  unless the code is wrong." Target = the test file by default (lower blast
  radius than regenerating the entry), but the prompt shows both.

A "change nothing else; return the complete corrected file; no fences, no
commentary" instruction is carried verbatim from the existing templates.

**4.B addendum — the `_extract_failed_gate` normalization (the S11 finding).**
The corrective-prompt builder needs a single helper that, given the `cross` dict,
returns `(gate_name, failure_text, target_file)` handling all three shapes:
dict-with-`ok=False`, dict-with-`rc!=0` (entry_execution), and
list-of-dicts-with-`rc!=0` (smoke_tests). This is the *same* normalization the
Tier-2 `build._extract_failed_gate` needs (S11 logged it surfacing only
`phase=cross_file status=failed` with no sub-gate detail for list-shaped gates).
**Spec it here; implement it in the build session** — one shared shape-aware
extractor serves both the Tier-1 corrective prompt and the Tier-2 diagnostic.

### 4.C — RETRY BUDGET + termination

Precedent: AST self-heal = 3, main-guards = 2. The `RUN_CAP_SECONDS = 1200` cap
with ~150-300s per LLM round-trip means only ~2-4 extra regens fit before the cap
aborts — so the budget must be **small**, and the loop must stop on no-progress
rather than burning the budget.

**Recommendation:**
- **Per-gate budget: 1** corrective regen (the evidence is "one localized fix";
  one pass tests the hypothesis without thrashing).
- **Global feedback budget: 3** total corrective regens across all of Phase 3 —
  bounds the cross-gate "fix-one-break-another" walk (histogram-builder's exact
  risk: fix the self-import → fail the smoke test → fix that → fail something).
- **No-progress guard:** track `(gate, target_file, normalized_error_signature)`.
  If a regen yields the *same* signature again, **stop immediately** (don't spend
  the rest of the budget — the regen isn't helping).
- **Cycle guard:** if a `(gate, file, signature)` tuple *repeats* anywhere in the
  run (A→B→A oscillation), halt.
- **Backstop:** `check_cap()` already aborts on total wall-clock — the final
  safety net.
- **Termination = current behavior** (`sys.exit(1)` + `dump_state`) when budget
  exhausted / no-progress / cycle, with all feedback attempts recorded in the
  state log (mirroring `main_guard_attempts`) for forensics.

### 4.D — Does this break "STRUCTURALLY COMPLETE"?

The v7 verdict: *"every failure is either caught by a gate with a precise
diagnostic, OR is LLM-quality and not amenable to additional structural gates."*

Regen-with-feedback **adds no gate.** It adds a *response* to the second category
(LLM-quality failures) — the first mechanism to attack that category without a new
structural check. So it **extends** the verdict rather than invalidating it: the
set of gates remains complete and unchanged; a bounded **self-repair layer** is
inserted between "gate fails" and "halt."

This is continuous with the existing design philosophy, not a departure — the
engine *already* self-repairs in two places (AST self-heal, main-guards). The
honest nuance for the human: it shifts the engine's posture from
**diagnose-and-halt** to **diagnose → attempt-repair → halt**, which is a
behavioral change (Section 4.E) even though it is not a structural one.

**Recommended framing: a new cross-cutting *recovery layer*, not a new pipeline
phase and not a verdict revision.** "Structurally complete" stays true.

### 4.E — Blast radius / regression risk

**The critical invariant: the loop must not fire on a passing build.** The hook
lives strictly inside each gate's *failure* branch (`if not ok` / `if rc != 0`).
A passing gate never enters the regen path → currently-green builds (the `today10`
daily driver, csvmd 4/4, the green determinism-sweep tasks) are **byte-for-byte
unaffected**. This invariant must be the first thing the test surface proves.

Residual risks:
- A regen could fix gate X but mutate the file such that an *earlier* gate now
  fails. Mitigated by the **full Phase-3 re-verify after each regen** (4.A) +
  the global budget + cycle guard.
- **Determinism semantics shift.** A previously-deterministic FAIL can become a
  sometimes-PASS (the intended effect) — but this means feedback-enabled runs are
  **not comparable to the v7 sweep baselines**. The determinism-sweep methodology
  needs a note: grade feedback-on vs feedback-off separately.
- Longer wall-clock on failing builds (extra round-trips), bounded by the cap.

**Regression test surface (before ship):**
1. **No-fire-on-pass** (the key guard): a build where all gates pass → assert
   **0** corrective regens fired and identical output.
2. End-to-end re-run of a known-green build (`today10` or csvmd `file-reader`) →
   identical pass, zero feedback.
3. The cap-abort path still triggers with the loop present.

### 4.F — Test plan (for when it is built; do NOT build it now)

**Unit/integration with a FAKE model** (mirror the Tier-2 fake-`ocb_runner`
pattern): stub `client.chat.completions.create` to return a *scripted sequence* —
broken code on call 1, fixed code on call 2 — so the loop logic is fully testable
**without ollama**. Cases:
- *happy:* gate fails once → regen → passes. Assert exactly 1 regen, build green.
- *budget exhaustion:* regen still broken → hard-fail after the budget. Assert N
  regens then `exit 1`, state log records all attempts.
- *no-progress guard:* identical error signature twice → stop *before* budget.
- *cross-gate oscillation:* fix A breaks B, fix B breaks A → cycle guard halts.
- *no-fire-on-pass:* all gates green → 0 regens (the 4.E invariant).
- *extractor shapes:* feed the three real `cross` shapes (dict-`ok`, dict-`rc`,
  list) → correct `(gate, text, file)` each.

**Live validation (after unit-green; run sparingly — ollama cost):**
- The histogram-builder three failure classes: `oc2 build numstat --only
  histogram-builder` a handful of times; each roll's distinct failure exercises a
  different corrective path. Success criterion: the loop carries at least one roll
  to green that would otherwise have halted.
- The Step-1 I/O one-liner: reconstruct the pinned `entry_execution`
  `ValueError` case and confirm a single corrective regen repairs it.
- Regression: re-run a green build, assert no behavior change.

---

## 5. Recommendation summary (one line each)

- **A (hook):** per-gate trigger on the four runtime/consistency gates
  (dep_honesty, dry_import, entry_execution, smoke_tests), reusing the
  `regenerate_for_main_guards` template, with a full Phase-3 re-verify after each
  accepted regen.
- **B (prompt):** system + task + prior_code + a gate-specific corrective ("fix
  ONLY this"); add a shared shape-aware `(gate, text, file)` extractor that also
  fixes the S11 `_extract_failed_gate` gap.
- **C (budget):** 1 regen per gate, 3 global, + no-progress + cycle guards, +
  the existing `RUN_CAP_SECONDS` backstop.
- **D (verdict):** EXTENDS "structurally complete" — a recovery layer, not a new
  gate; the verdict stands.
- **E (blast radius):** zero effect on passing builds (hook is failure-branch
  only); the no-fire-on-pass test is the gate to ship.
- **F (tests):** fake-model unit suite (broken→fixed scripted sequence) for loop
  logic; histogram-builder + the I/O one-liner as live cases.

---

## 6. Smallest viable v1 (if the human says "proceed, but minimal")

If full per-gate coverage is too much for a first cut, the **minimum** that
captures most of the evidence value:
- Cover **two gates only: `entry_execution` and `smoke_tests`** (the I/O one-liner
  + histogram roll 3). Both already produce a clean traceback/assertion.
- **1 regen each, 2 global**, no-progress guard, no cross-gate re-verify beyond
  re-running those two gates.
- Ship the shared extractor (also fixes the Tier-2 S11 gap) regardless.
This is the lowest-blast-radius slice and still exercises the whole design.

---

## 7. Open questions for the human (decide before building)

1. **Scope:** the recommended four gates (A), the minimal two-gate slice (Section
   6), or all of Phase 3 including static_lint/contracts?
2. **Budget:** 1-per-gate / 3-global as proposed, or tighter (1 global) / looser?
   Is the `RUN_CAP_SECONDS` interaction acceptable, or should the cap be raised to
   give feedback room?
3. **Re-verify policy:** full Phase-3 re-verify after each regen (safe, costs
   re-runs), or forward-only from the failing gate (cheaper, risks reintroducing
   earlier violations)?
4. **Verdict framing (D):** accept "recovery layer extends structurally-complete,"
   or does the human want this gated behind a flag (feedback OFF by default,
   opt-in) so the v7 baseline stays the default engine?
5. **Determinism sweep:** is splitting the sweep into feedback-on / feedback-off
   acceptable, or must a new baseline sweep be run first?
6. **The `_extract_failed_gate` fix:** ship it standalone *now* (tiny, also helps
   Tier-2) regardless of whether the full loop proceeds — yes/no?
7. **smoke_tests two-sidedness (B):** default to regenerating the *test* (lower
   blast radius) or the *entry* when they disagree? Or always show both and let
   the model choose, accepting the larger diff?

When these are settled, the next session is the implementation brief (build the
loop + the fake-model test suite, checkpoint before the live runs).
