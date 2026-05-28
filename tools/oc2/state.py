"""state.json read/write for oc2 projects (Tier 2 v1).

`state.json` is the source of truth for a project's build state. Writes are
atomic (temp file in the same directory + os.replace) so a kill mid-write never
leaves a half-written file — important because subsystem completions write here
one at a time and the build is resumable across sessions.

Schema is design/tier2_v1.md Section 4. `state_schema_version` is stamped from
day one (locked decision Q6) to keep future migrations cheap.

This module is filesystem-aware but Tier-1-free: it takes a project directory
and knows nothing about the architecture parser or the CLI.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE_SCHEMA_VERSION = 1
STATE_FILENAME = "state.json"


def now_iso() -> str:
    """UTC timestamp, second precision, e.g. '2026-05-27T18:00:00Z'."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def state_path(project_dir: Path) -> Path:
    return Path(project_dir) / STATE_FILENAME


def read_state(project_dir: Path) -> dict | None:
    """Return the parsed state dict, or None if no state file exists yet."""
    p = state_path(project_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write_state(project_dir: Path, state: dict) -> None:
    """Atomically write state to <project_dir>/state.json.

    Writes to a temp file in the same directory, flushes+fsyncs, then os.replace
    (atomic on the same filesystem) so readers never see a partial file.
    """
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    target = state_path(project_dir)
    fd, tmp_name = tempfile.mkstemp(prefix=".state.", suffix=".json.tmp", dir=str(project_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        # Best-effort cleanup of the temp file on any failure path.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def new_state(project_name: str) -> dict:
    """A fresh state skeleton for a newly-designed project (doc Section 4)."""
    return {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "project_name": project_name,
        "architecture": {
            "current_path": "architecture.md",
            "current_sha256": None,
            "version_count": 0,
        },
        "approval": {
            "approved": False,
            "approved_at": None,
            "approved_architecture_sha256": None,
        },
        "subsystems": {},
        "history": [],
        "last_session_at": None,
    }


def append_history(state: dict, event: str, **fields) -> None:
    """Append a timestamped event to state['history'] in place."""
    entry = {"event": event, "at": now_iso()}
    entry.update(fields)
    state.setdefault("history", []).append(entry)
