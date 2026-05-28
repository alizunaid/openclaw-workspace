"""Subsystem-to-Tier-1 prompt translator.

STUB (Session 1). Given one subsystem entry from the architecture plus the
already-built dependency source paths and the architecture's integration-points
section, this builds the task prompt handed to `ocb` as a subprocess. The prompt
is the contract between Tier 2 and Tier 1; it lands in Session 4 (doc Section 3).
"""
from __future__ import annotations

from oc2.architecture import Subsystem


def build_task_prompt(
    subsystem: Subsystem,
    dep_source_paths: dict[str, str],
    integration_points: str,
) -> str:
    raise NotImplementedError(
        "subsystem-to-ocb prompt translation lands in Session 4 (doc Section 3)"
    )
