"""oc2 — Tier 2 orchestrator for OpenClaw.

A CLI that orchestrates multiple Tier 1 (`ocb`) invocations through the
lifecycle: design -> approve -> build -> status -> revise. See
`design/tier2_v1.md` for the full design.

This package is intentionally decoupled from Tier 1: Tier 2 invokes
`oc_builder.py` as a subprocess (later sessions); it does not import it.
"""
from __future__ import annotations

from pathlib import Path

# tools/oc2/__init__.py -> tools/oc2 -> tools -> <workspace root>
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
TIER2_PROJECTS = WORKSPACE_ROOT / "tier2_projects"

__all__ = ["WORKSPACE_ROOT", "TIER2_PROJECTS"]
