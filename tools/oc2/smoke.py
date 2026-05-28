"""Integration smoke runner.

STUB (Session 1). The minimal v1 integration test (doc Section 3, Q4 option b):
import each built subsystem's entry point in topological order from one Python
process and capture any ImportError / runtime error. Lands in Session 5.
"""
from __future__ import annotations

from pathlib import Path


def run_smoke(project_dir: Path, topo_order: list[str]) -> tuple[bool, str]:
    raise NotImplementedError("integration smoke runner lands in Session 5 (doc Section 3)")
