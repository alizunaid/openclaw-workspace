"""Subsystem -> Tier-1 prompt translator.

Given one subsystem entry from the parsed architecture plus the (already-built)
dependencies' source paths and the architecture's Integration-points section,
this builds the task prompt that Tier 2 hands to `ocb` as a subprocess. The
prompt is the contract between Tier 2 and Tier 1: it shapes what Tier 1
generates per subsystem, so it must include exactly the five elements
specified in design/tier2_v1.md §3.

Pure / deterministic: no LLM, no filesystem, no Tier 1 imports.
"""
from __future__ import annotations

from pathlib import Path

from oc2.architecture import Subsystem


def _bullets(items: list[str], indent: str = "  ") -> str:
    """Render a list as a markdown-style bulleted block, or 'none' if empty."""
    if not items:
        return f"{indent}(none)"
    return "\n".join(f"{indent}- {item}" for item in items)


def _dep_paths_block(dep_source_paths: dict) -> str:
    """Render the dep-paths block. Empty -> a clear 'no upstream' line so the
    model doesn't try to read non-existent paths."""
    if not dep_source_paths:
        return "  (no upstream dependencies — this subsystem can be built standalone)"
    lines = []
    for name in sorted(dep_source_paths):
        path = dep_source_paths[name]
        lines.append(f"  - `{name}` — source at `{path}`")
    return "\n".join(lines)


def build_task_prompt(
    subsystem: Subsystem,
    dep_source_paths: dict[str, str | Path],
    integration_points: str,
    subsystem_dir: str | Path,
) -> str:
    """Construct the task prompt for one subsystem.

    The prompt mirrors the doc §3 sketch, with the five elements:
      1. Purpose paragraph (verbatim).
      2. Inputs / Outputs as bulleted blocks.
      3. Owns-state line.
      4. Already-built dependency sources (paths the model may read to
         understand what each dep exposes).
      5. The architecture's Integration-points section (the inter-subsystem
         contracts; verbatim).
    Plus a trailing instruction telling ocb where to write its output.

    Args:
        subsystem: parsed Subsystem (purpose/inputs/outputs/depends_on/
            owns_state/failure_modes/name).
        dep_source_paths: mapping from already-built dep subsystem name to its
            generated-source directory (e.g. `subsystems/file-reader/`).
        integration_points: the verbatim text of the architecture's
            `## Integration points` section.
        subsystem_dir: where ocb should land its output for THIS subsystem
            (e.g. `subsystems/csv-parser/`).

    Returns:
        A plain-English task prompt suitable to pass as ocb's positional task.
    """
    # Normalise paths to strings so the prompt stays string-typed. Strip a
    # trailing slash from subsystem_dir; the output-instruction template adds
    # exactly one trailing slash so the result reads cleanly whether the
    # caller passed `subsystems/x` or `subsystems/x/`.
    deps_str = {name: str(p) for name, p in dep_source_paths.items()}
    sub_dir_str = str(subsystem_dir).rstrip("/")

    purpose = subsystem.purpose.strip() or "(no purpose recorded)"
    owns_state = subsystem.owns_state.strip() or "stateless"
    integ = integration_points.strip() or "(no integration-points section recorded)"

    return (
        f"Build subsystem `{subsystem.name}`.\n"
        "\n"
        "Purpose:\n"
        f"{purpose}\n"
        "\n"
        "Inputs:\n"
        f"{_bullets(subsystem.inputs)}\n"
        "\n"
        "Outputs:\n"
        f"{_bullets(subsystem.outputs)}\n"
        "\n"
        f"State this subsystem owns: {owns_state}\n"
        "\n"
        "It depends on these already-built subsystems whose source is at the\n"
        "following paths (read them if you need to understand the contracts\n"
        "they expose):\n"
        f"{_dep_paths_block(deps_str)}\n"
        "\n"
        "Integration points (contracts between subsystems):\n"
        f"{integ}\n"
        "\n"
        "Failure modes this subsystem must handle gracefully:\n"
        f"{_bullets(subsystem.failure_modes)}\n"
        "\n"
        "The entry point must exit 0 when invoked with no arguments — Tier 1's\n"
        "validation runs `python3 <entry>.py` with no args and fails the build\n"
        "on any non-zero exit. Keep real CLI argument handling when given args,\n"
        "but the no-args path must be a no-op self-check: print a one-line\n"
        "confirmation and exit 0, NOT a usage error.\n"
        "\n"
        "Build as a single coherent script or as a small set of files. Do not\n"
        f"split unless the work is genuinely multi-file. Output to {sub_dir_str}/.\n"
    )
