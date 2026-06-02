"""`oc2 build` — topological execution of an approved architecture.

Session 4 replaces the stub with a real orchestrator that, per doc Section 3:
  - iterates subsystems in topological order (alphabetical tiebreak)
  - sequentially invokes `ocb` as a subprocess for each `pending` subsystem
  - copies the generated source files from ocb's run dir into
    `tier2_projects/<project>/subsystems/<name>/`
  - HALTS on first failure: failed subsystem is marked `failed` with the gate
    + error from ocb's state log; transitive dependents stay `pending`
  - the `--only <name>` escape hatch rebuilds just one subsystem (for
    stochastic-failure retries)
  - ntfy events fire on subsystem-built / build-paused / build-complete

The core is `build_project(project_dir, name, only=None, ocb_runner=None)` so
integration tests drive it against a tmpdir with a fake ocb_runner (no
subprocess, no model). `cmd_build` is a thin CLI wrapper.

The Tier 1 boundary stays subprocess-only — `oc_builder.py` is invoked via
`python3 -u tools/oc_builder.py "<prompt>"` and never imported.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from oc2 import TIER2_PROJECTS, WORKSPACE_ROOT
from oc2.approve import resolve_project
from oc2.architecture import ParseError, parse, validate
from oc2.diff import propagate_pending
from oc2.prompt import build_task_prompt
from oc2.smoke import run_smoke, smoke_spec_for
from oc2.state import append_history, now_iso, read_state, write_state

# Paths the real ocb_runner needs to discover its outputs.
_OCB_PATH = WORKSPACE_ROOT / "tools" / "oc_builder.py"
_OCB_GENERATED = WORKSPACE_ROOT / "tools" / "generated"
_OCB_LOGS = WORKSPACE_ROOT / "logs"
_NTFY_SCRIPT = WORKSPACE_ROOT / "tools" / "notify.sh"


# --- ocb-runner abstraction -------------------------------------------------

@dataclass
class OcbResult:
    """What one ocb invocation produced. Tests inject fake results via the
    ocb_runner parameter; production uses `_real_ocb_runner`."""
    success: bool
    run_id: str | None = None        # e.g. "run_1748431234"
    run_dir: Path | None = None      # tools/generated/run_<ts>/
    log_path: Path | None = None     # logs/oc_build_<ts>.json
    error: str = ""                  # "" on success; one-line gate+msg on failure
    generated_files: list[str] = field(default_factory=list)  # filled by orchestrator


OcbRunner = Callable[[str, Path, str], OcbResult]


def _ocb_outputs_before() -> tuple[set[str], set[str]]:
    """Snapshot existing run dirs + state logs so we can identify the NEW one
    that ocb produced for our invocation. Falls back to newest-by-mtime if the
    snapshot diff is empty (e.g., ocb died very early)."""
    runs = {p.name for p in _OCB_GENERATED.iterdir()} if _OCB_GENERATED.exists() else set()
    logs = {p.name for p in _OCB_LOGS.glob("oc_build_*.json")} if _OCB_LOGS.exists() else set()
    return runs, logs


def _ocb_outputs_after(before: tuple[set[str], set[str]]) -> tuple[Path | None, Path | None]:
    """Identify the new run_dir + log_path produced by ocb. Returns (None, None)
    if nothing new is visible (which means ocb didn't even reach run-dir
    creation; the caller should treat it as a hard failure)."""
    before_runs, before_logs = before
    new_runs = []
    if _OCB_GENERATED.exists():
        for p in _OCB_GENERATED.iterdir():
            if p.name not in before_runs and p.name.startswith("run_"):
                new_runs.append(p)
    new_logs = []
    if _OCB_LOGS.exists():
        for p in _OCB_LOGS.glob("oc_build_*.json"):
            if p.name not in before_logs:
                new_logs.append(p)
    # The run dir we want is the freshest one. If multiple appeared (only
    # possible if multiple builds raced), take the newest by mtime.
    run_dir = max(new_runs, key=lambda p: p.stat().st_mtime, default=None)
    log_path = max(new_logs, key=lambda p: p.stat().st_mtime, default=None)
    return run_dir, log_path


def _extract_failed_gate(log_path: Path | None) -> str:
    """Read ocb's state log and report which phase failed. Tolerant of a
    missing or unparseable log — returns a generic message in that case."""
    if not log_path or not log_path.exists():
        return "ocb exited non-zero; no state log on disk"
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"ocb state log unreadable ({e})"
    phases = data.get("phases", {})
    if not isinstance(phases, dict):
        return f"ocb state log has no phases dict; see {log_path}"
    # Look for the first phase reporting non-ok status. ocb writes phases in
    # order, so the first non-ok we encounter is the gate that fired.
    for phase_name, phase_body in phases.items():
        if not isinstance(phase_body, dict):
            continue
        status = phase_body.get("status")
        if status and status != "ok":
            detail = _failed_subgate_detail(phase_body)
            return f"phase=`{phase_name}` status={status}{detail}"
    return f"ocb exited non-zero; no failed phase in log (see {log_path})"


def _manifest_degrade_detail(log_path: Path | None) -> str | None:
    """If ocb's manifest phase DEGRADED (the planner's file plan was rejected,
    so ocb fell back to its single-file legacy path), return a one-line reason;
    else None.

    A degrade still produces a GREEN build, but the entry module is named by the
    legacy path's prompt-slug heuristic rather than the canonical name (the S14
    file-writer finding: a hyphenated manifest entry path `file-writer.py` is
    rejected by ocb's FILENAME_RE → degrade → `build_subsystem_file_writer_…py`).
    ocb logs this and prints a WARNING to its own stdout, but oc2 reported the
    subsystem green with no trace — a silent landmine. Surfacing it here makes
    the degrade LOUD in oc2's build output, state, and BUILD_REPORT."""
    if not log_path or not log_path.exists():
        return None
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    manifest = data.get("phases", {}).get("manifest", {})
    if not isinstance(manifest, dict) or manifest.get("status") != "degraded":
        return None
    reason = manifest.get("reason") or "manifest planner output rejected"
    errors = manifest.get("errors")
    if isinstance(errors, list) and errors:
        return f"{reason}: {errors[0]}"
    return str(reason)


def _first_error_line(*texts: str) -> str:
    """First non-blank line across the given text fields (a traceback's last
    line is the most informative, but the first non-blank is cheap + stable);
    returns '' if none. Used to enrich a one-line gate diagnostic."""
    for t in texts:
        if not t:
            continue
        for line in str(t).splitlines():
            line = line.strip()
            if line:
                return line
    return ""


def _failed_subgate_detail(phase_body: dict) -> str:
    """Find the failing sub-gate inside a phase body and return a one-line
    `— sub-gate ...` suffix. Handles the three real sub-gate log shapes (S11):
      - dict with `ok: False`           (contracts, dep_honesty, dry_import, ...)
      - dict with `rc != 0` and no `ok` (entry_execution)
      - list of dicts each with `rc`    (smoke_tests)
    The pre-S11 extractor only knew the first shape, so a smoke_tests failure
    surfaced as a bare `status=failed` with no sub-gate. Returns '' if no
    failing sub-gate is identifiable."""
    for k, v in phase_body.items():
        if isinstance(v, dict):
            if v.get("ok") is False:
                return f" — sub-gate `{k}` failed"
            # rc-shaped sub-gate (e.g. entry_execution): rc present, no `ok`.
            if "ok" not in v and isinstance(v.get("rc"), int) and v["rc"] != 0:
                err = _first_error_line(v.get("stderr", ""), v.get("stdout", ""))
                tail = f": {err}" if err else ""
                return f" — sub-gate `{k}` rc={v['rc']}{tail}"
        elif isinstance(v, list):
            # list-shaped sub-gate (e.g. smoke_tests): each item has rc/path.
            for item in v:
                if isinstance(item, dict) and isinstance(item.get("rc"), int) and item["rc"] != 0:
                    err = _first_error_line(item.get("stderr", ""), item.get("stdout", ""))
                    where = item.get("path", "")
                    loc = f" ({where})" if where else ""
                    tail = f": {err}" if err else ""
                    return f" — sub-gate `{k}`{loc} rc={item['rc']}{tail}"
    return ""


def _ocb_subprocess_env(project_dir: Path) -> dict:
    """Build the env for the ocb subprocess, SCOPED to this Tier 2 project.

    Tier 2 subsystems intentionally receive NO operator-project context. A
    subsystem builds from its ARCHITECTURE SPEC (purpose, I/O, integration
    points — already injected by build_task_prompt); per-project info a
    subsystem needs belongs in architecture.md, not in the operator's default
    project. So we set OPENCLAW_PROJECT to the Tier 2 project's OWN name: ocb's
    resolve_slug() then looks for projects/<name>.md, which does not exist, so
    load_project() returns an EMPTY context (project_name = the Tier 2 name, no
    raw_context). Without this, resolve_slug() falls through to
    DEFAULT_PROJECT='nexadose' and every Tier 2 build inherits Nexadose context
    (the S6/S7/S9 leak). See design/tier2_v1.md ("Project context: suppress").

    SYNTHESIZE SEAM: projects/<name>.md is also the exact hook for a future
    synthesize upgrade — generate a per-project context file there (from the
    architecture Overview + integration points) and this same lever loads it,
    no code change. Caveat: naming a Tier 2 project after a real projects/*.md
    slug (e.g. "nexadose") would re-load that context by design.

    We copy os.environ and override the one key — we never mutate the parent
    process env, and the override applies only to this subprocess.
    """
    env = dict(os.environ)
    env["OPENCLAW_PROJECT"] = Path(project_dir).name
    return env


def _real_ocb_runner(prompt: str, project_dir: Path, subsystem_name: str) -> OcbResult:
    """Invoke `python3 -u tools/oc_builder.py "<prompt>"` as a subprocess and
    discover its outputs by snapshot-diff against `tools/generated/` and `logs/`.

    The prompt is passed as a single positional argument; ocb's CLI joins
    `nargs="+"` with spaces internally (re-joining on whitespace is harmless
    since the prompt is already whitespace-formatted).

    The subprocess env is scoped via `_ocb_subprocess_env` so ocb does NOT
    inherit the operator's default (nexadose) project context.
    """
    before = _ocb_outputs_before()
    cmd = ["python3", "-u", str(_OCB_PATH), prompt]
    try:
        proc = subprocess.run(
            cmd, cwd=str(WORKSPACE_ROOT),
            capture_output=True, text=True,
            env=_ocb_subprocess_env(project_dir),
        )
    except Exception as e:
        return OcbResult(success=False, error=f"ocb subprocess failed to launch: {e}")

    run_dir, log_path = _ocb_outputs_after(before)
    run_id = run_dir.name if run_dir is not None else None

    if proc.returncode == 0:
        return OcbResult(success=True, run_id=run_id, run_dir=run_dir, log_path=log_path)

    # Non-zero exit — surface the failed gate from the state log.
    gate = _extract_failed_gate(log_path)
    return OcbResult(success=False, run_id=run_id, run_dir=run_dir,
                     log_path=log_path, error=gate)


# --- file copy + ntfy -------------------------------------------------------

def _copy_generated(src_run_dir: Path | None, dst_subsystem_dir: Path) -> list[str]:
    """Copy ocb's generated `.py` files into `subsystems/<name>/`. Returns a
    sorted list of the copied filenames (relative to dst). If src is None or
    empty, returns an empty list — the orchestrator will note that."""
    if src_run_dir is None or not src_run_dir.exists():
        return []
    dst_subsystem_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for src in sorted(src_run_dir.rglob("*.py")):
        rel = src.relative_to(src_run_dir)
        dst = dst_subsystem_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(str(rel))
    return copied


def _ntfy(title: str, body: str, priority: str = "default") -> None:
    """Best-effort phone push. Never raises — notification failures must never
    interrupt a build."""
    if not _NTFY_SCRIPT.exists():
        return
    try:
        subprocess.run(
            ["bash", str(_NTFY_SCRIPT), title, body, priority],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


# --- main orchestration -----------------------------------------------------

_BROKEN_BUILD_STATUSES = {"failed", "failed_or_killed"}


def _plan_build(state: dict, topo_order: list[str], only: str | None
                ) -> tuple[list[str], str | None]:
    """Determine which subsystems to attempt this run, walking topo order.

    Returns (targets, blocker):
      - targets: subsystems to invoke ocb for, in order
      - blocker: name of a non-done non-pending subsystem encountered in topo
                 order (failed / failed_or_killed); the caller should halt and
                 tell the user to retry --only it or revise + reapprove.

    `--only` short-circuits this: just the named subsystem (callers validate
    its deps separately).
    """
    if only:
        return [only], None
    subs = state.get("subsystems", {})
    targets: list[str] = []
    for n in topo_order:
        status = subs.get(n, {}).get("status", "pending")
        if status == "done":
            continue
        if status == "pending":
            targets.append(n)
            continue
        # failed / failed_or_killed / in_progress (in_progress is normally
        # converted to failed_or_killed by recovery, but handle defensively).
        return targets, n
    return targets, None


def _recover_in_progress(state: dict) -> list[str]:
    """If a prior build crashed mid-subsystem, the subsystem was left
    `in_progress`. Mark those `failed_or_killed` per the doc and return their
    names so the orchestrator can report the recovery."""
    recovered: list[str] = []
    for name, entry in state.get("subsystems", {}).items():
        if entry.get("status") == "in_progress":
            entry["status"] = "failed_or_killed"
            entry["error"] = (entry.get("error")
                              or "subsystem was in_progress at startup — "
                              "treat as killed mid-build")
            recovered.append(name)
    return recovered


def build_report_markdown(state: dict, topo_order: list[str], project_name: str,
                          smoke_summary: str, smoke_ok: bool,
                          smoke_entries: dict | None = None,
                          smoke_findings: list | None = None,
                          elapsed_seconds: float | None = None) -> str:
    """Assemble BUILD_REPORT.md (doc §3:301) — the per-subsystem final state +
    the integration smoke verdict. Pure: takes the already-computed smoke
    result, returns markdown. Kept testable so the report layout is asserted
    against a built project without re-running ocb."""
    subs = state.get("subsystems", {})
    lines = [f"# Build report: {project_name}", ""]
    if elapsed_seconds is not None:
        lines.append(f"Last build run wall-clock: {elapsed_seconds:.1f}s")
        lines.append("")

    lines.append("## Subsystems")
    lines.append("")
    lines.append("| # | subsystem | status | ocb run_id | built_at | files |")
    lines.append("|---|-----------|--------|------------|----------|-------|")
    for i, n in enumerate(topo_order, 1):
        e = subs.get(n, {})
        files = ", ".join(e.get("generated_files", []) or []) or "—"
        lines.append(
            f"| {i} | `{n}` | {e.get('status', '?')} | "
            f"{e.get('ocb_run_id') or '—'} | {e.get('built_at') or '—'} | {files} |"
        )
    lines.append("")

    # Surface any manifest degrades — green builds, but the file plan was
    # rejected and ocb fell back to its single-file legacy path (slugged name).
    degraded = [(n, subs.get(n, {}).get("manifest_degraded"))
                for n in topo_order if subs.get(n, {}).get("manifest_degraded")]
    if degraded:
        lines.append("## Manifest degrades (built green via legacy fallback)")
        lines.append("")
        for n, reason in degraded:
            lines.append(f"- `{n}`: {reason}")
        lines.append("")

    verdict = "PASS" if smoke_ok else "FAIL"
    lines.append("## Integration smoke")
    lines.append("")
    lines.append(f"**Verdict: {verdict}** — {smoke_summary}")
    lines.append("")
    if smoke_entries:
        lines.append("Resolved entry points (topological order):")
        lines.append("")
        for n in topo_order:
            if n in smoke_entries:
                lines.append(f"- `{n}` -> `{smoke_entries[n]}`")
        lines.append("")
    if smoke_findings:
        lines.append("Findings:")
        lines.append("")
        for f in smoke_findings:
            lines.append(f"- {f}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _all_done(state: dict, topo_order: list[str]) -> bool:
    subs = state.get("subsystems", {})
    return all(subs.get(n, {}).get("status") == "done" for n in topo_order)


def _finalize_full_build(project_dir: Path, state: dict, arch, topo_order: list[str],
                         project_name: str, total_elapsed: float) -> bool:
    """Called when the whole project just reached all-done. Runs the integration
    smoke and writes BUILD_REPORT.md with the verdict. Returns the smoke ok
    flag. Never raises — a smoke crash is itself a finding written to the
    report (we must not let it mask a successful build's other state)."""
    try:
        result = run_smoke(project_dir, arch, topo_order, smoke_spec_for(project_name))
        smoke_ok, summary = result.ok, result.summary
        entries, findings = result.entries, result.findings
    except Exception as e:  # noqa: BLE001 — defensive; run_smoke shouldn't raise
        smoke_ok, summary = False, f"smoke runner crashed: {type(e).__name__}: {e}"
        entries, findings = {}, [summary]

    report = build_report_markdown(
        state, topo_order, project_name, summary, smoke_ok,
        smoke_entries=entries, smoke_findings=findings,
        elapsed_seconds=total_elapsed,
    )
    (project_dir / "BUILD_REPORT.md").write_text(report, encoding="utf-8")

    append_history(state, "smoke", ok=smoke_ok, summary=summary,
                   entries=entries)
    write_state(project_dir, state)

    print(f"[oc2 build] integration smoke: "
          f"{'PASS' if smoke_ok else 'FAIL'} — {summary}", flush=True)
    if not smoke_ok:
        for f in findings:
            print(f"    - {f}")
    print(f"[oc2 build] wrote {project_dir / 'BUILD_REPORT.md'}", flush=True)
    return smoke_ok


def build_project(project_dir: Path, name: str, only: str | None = None,
                  ocb_runner: OcbRunner | None = None) -> int:
    """Run the build phase for `name`. Returns 0 on success, 1 on failure.

    project_dir: tier2_projects/<name>/ (the test suite passes a tmpdir).
    only:        if set, rebuild ONLY this subsystem (escape hatch for
                 stochastic ocb failures, doc §3).
    ocb_runner:  injection point for tests; defaults to `_real_ocb_runner`.
    """
    runner = ocb_runner or _real_ocb_runner
    project_dir = Path(project_dir)

    arch_path = project_dir / "architecture.md"
    if not project_dir.exists():
        print(f"error: no project {name!r} in tier2_projects/.")
        return 1
    if not arch_path.exists():
        print(f"error: {name!r} has no architecture.md. "
              f"Run `oc2 design ... --name {name}` first.")
        return 1

    state = read_state(project_dir)
    if state is None:
        print(f"error: {name!r} has no state.json yet — run `oc2 approve {name}` first.")
        return 1
    if not state.get("approval", {}).get("approved"):
        print(f"error: {name!r} is not approved. Run `oc2 approve {name}` first.")
        return 1

    # The architecture must still validate at build time: the user may have
    # edited architecture.md after `approve`. Refuse so we don't drive ocb
    # from a stale or broken spec.
    md = arch_path.read_text(encoding="utf-8")
    try:
        arch = parse(md)
    except ParseError as e:
        print(f"error: architecture.md no longer parses ({e}). "
              f"Re-run `oc2 approve {name}` after fixing it.")
        return 1
    res = validate(arch)
    if not res.ok:
        print(f"error: architecture.md no longer validates. "
              f"Re-run `oc2 approve {name}` after fixing these:")
        for e in res.errors:
            print(f"  - {e}")
        return 1

    # Recover any stale in_progress from a crashed prior session.
    recovered = _recover_in_progress(state)
    if recovered:
        print(f"recovered from interrupted prior build — marked "
              f"{', '.join(recovered)} as failed_or_killed")
        write_state(project_dir, state)

    topo = res.topo_order
    sub_by_name = arch.by_name()

    # Resolve target set + sanity check `--only` early.
    if only:
        if only not in sub_by_name:
            print(f"error: --only {only!r} is not a subsystem in this architecture.")
            return 1
        deps = sub_by_name[only].depends_on
        not_done = [d for d in deps
                    if state["subsystems"].get(d, {}).get("status") != "done"]
        if not_done:
            print(f"error: --only {only!r} depends on {', '.join(not_done)} "
                  f"which are not 'done'. Build them first, or revise the "
                  f"architecture and `oc2 approve`.")
            return 1
        # If `only` is currently in a broken status, that's exactly the
        # stochastic-retry case --only exists for: reset to pending so the
        # state machine moves it to in_progress cleanly.
        entry = state["subsystems"].get(only, {})
        if entry.get("status") in _BROKEN_BUILD_STATUSES:
            entry["status"] = "pending"
            entry["error"] = None

    targets, blocker = _plan_build(state, topo, only)
    if blocker is not None:
        bstatus = state["subsystems"].get(blocker, {}).get("status", "?")
        berror = state["subsystems"].get(blocker, {}).get("error") or "(none recorded)"
        print(f"error: subsystem {blocker!r} is in state {bstatus!r} from a "
              f"prior session.")
        print(f"       reason: {berror}")
        print(f"       Retry it: `oc2 build {name} --only {blocker}`,")
        print(f"       or revise architecture.md and re-approve.")
        return 1
    if not targets:
        print(f"{name}: nothing to build — all subsystems are already done.")
        return 0

    project_name = state.get("project_name", name)
    integ = arch.integration_points
    build_started = time.time()
    print(f"[oc2 build] {project_name}: building {len(targets)} subsystem(s) "
          f"in order: {', '.join(targets)}", flush=True)
    append_history(state, "build_start", targets=list(targets), only=only)
    write_state(project_dir, state)

    succeeded: list[str] = []
    for sub_name in targets:
        sub = sub_by_name[sub_name]
        entry = state["subsystems"].setdefault(sub_name, {})

        # Mark in_progress and persist BEFORE invoking ocb so a crash leaves
        # the truthful state on disk (recovery picks it up next session).
        entry["status"] = "in_progress"
        entry["started_at"] = now_iso()
        entry["error"] = None
        write_state(project_dir, state)

        # Build the dep-source-paths map from already-done deps.
        dep_paths = {
            d: f"subsystems/{d}/"
            for d in sub.depends_on
            if state["subsystems"].get(d, {}).get("status") == "done"
        }
        subsystem_dir = project_dir / "subsystems" / sub_name
        prompt = build_task_prompt(
            subsystem=sub,
            dep_source_paths=dep_paths,
            integration_points=integ,
            subsystem_dir=f"subsystems/{sub_name}/",
        )

        t0 = time.time()
        print(f"[oc2 build]   -> {sub_name} (ocb invocation; "
              f"first call may take 30-90s for VRAM load)...", flush=True)
        result = runner(prompt, project_dir, sub_name)
        elapsed = time.time() - t0

        if result.success:
            copied = _copy_generated(result.run_dir, subsystem_dir)
            entry.update({
                "status": "done",
                "ocb_run_id": result.run_id,
                "built_at": now_iso(),
                "error": None,
                "generated_files": copied,
            })
            entry.pop("started_at", None)
            # A degraded manifest still builds green, but the file plan was
            # rejected and ocb slugged the filename — surface it LOUDLY instead
            # of leaving a silent landmine (S15). Recorded on the entry so it
            # also shows up in BUILD_REPORT.
            degrade = _manifest_degrade_detail(result.log_path)
            if degrade:
                entry["manifest_degraded"] = degrade
            else:
                entry.pop("manifest_degraded", None)
            write_state(project_dir, state)
            print(f"[oc2 build]   <- {sub_name} done in {elapsed:.1f}s "
                  f"(run {result.run_id}, {len(copied)} files)", flush=True)
            if degrade:
                print(f"[oc2 build]   !! {sub_name}: manifest DEGRADED to legacy "
                      f"single-file mode — {degrade}", flush=True)
                _ntfy("oc2 build: manifest degraded",
                      f"{project_name}/{sub_name}: {degrade}", "high")
            _ntfy("oc2 build: subsystem built",
                  f"{project_name}/{sub_name} done in {elapsed:.0f}s")
            succeeded.append(sub_name)
            continue

        # Failure path — halt per doc §3.
        entry.update({
            "status": "failed",
            "ocb_run_id": result.run_id,
            "error": result.error or "ocb failed (no error message)",
        })
        entry.pop("started_at", None)
        # Identify transitive dependents that won't run this session.
        blocked = sorted(propagate_pending({sub_name}, arch) - {sub_name})
        append_history(
            state, "build_pause",
            failed=sub_name, error=entry["error"],
            blocked=blocked, succeeded=succeeded,
        )
        state["last_session_at"] = now_iso()
        write_state(project_dir, state)

        log_ref = (str(result.log_path) if result.log_path
                   else "(no ocb state log on disk)")
        print(f"[oc2 build] PAUSED: subsystem {sub_name!r} failed.", flush=True)
        print(f"  ocb run_id: {result.run_id or '(none)'}")
        print(f"  gate:       {entry['error']}")
        print(f"  log:        {log_ref}")
        if blocked:
            print(f"  dependents not built: {', '.join(blocked)}")
        print()
        print(f"Decide: revise architecture.md + `oc2 approve {project_name}` "
              f"+ `oc2 build {project_name}`,")
        print(f"        OR retry just this subsystem: "
              f"`oc2 build {project_name} --only {sub_name}`.")
        _ntfy("oc2 build: PAUSED on failure",
              f"{project_name}/{sub_name}: {entry['error']}",
              priority="high")
        return 1

    # Success — every target built.
    total_elapsed = time.time() - build_started
    append_history(
        state, "build_complete",
        built=succeeded, elapsed_seconds=round(total_elapsed, 1),
    )
    state["last_session_at"] = now_iso()
    write_state(project_dir, state)
    print(f"[oc2 build] {project_name}: built {len(succeeded)} subsystem(s) "
          f"in {total_elapsed:.1f}s.", flush=True)

    # If the whole project just reached all-done, run the integration smoke and
    # write BUILD_REPORT.md (doc §3 / Q4). The build itself SUCCEEDED — a smoke
    # failure is a finding recorded in the report + ntfy, not a build-failure
    # exit (we must not retro-fail subsystems ocb gated green).
    if _all_done(state, topo):
        smoke_ok = _finalize_full_build(project_dir, state, arch, topo,
                                        project_name, total_elapsed)
        _ntfy("oc2 build: complete",
              f"{project_name}: {len(succeeded)} built in {total_elapsed:.0f}s; "
              f"smoke {'PASS' if smoke_ok else 'FAIL'}",
              priority="default" if smoke_ok else "high")
    else:
        remaining = [n for n in topo
                     if state["subsystems"].get(n, {}).get("status") != "done"]
        print(f"[oc2 build] {len(remaining)} subsystem(s) still pending: "
              f"{', '.join(remaining)} — smoke + BUILD_REPORT.md run on full completion.")
        _ntfy("oc2 build: progress",
              f"{project_name}: {len(succeeded)} built in {total_elapsed:.0f}s; "
              f"{len(remaining)} still pending")
    return 0


def cmd_build(args) -> int:
    name = resolve_project(args.project)
    if name is None:
        print("error: specify which project to build, e.g. `oc2 build <name>`.")
        return 1
    only = getattr(args, "only", None)
    # `--resume` is the documented default (doc §1); the flag exists only so
    # the help text surfaces resumability. No behavioural difference.
    return build_project(TIER2_PROJECTS / name, name, only=only)
