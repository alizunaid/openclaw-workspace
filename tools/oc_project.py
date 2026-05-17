"""Project identity loader for OpenClaw engines.

Reads projects/<slug>.md and returns a dict suitable for prompt templating.
Resolution order for the active project slug is: explicit CLI value > the
OPENCLAW_PROJECT env var > the hardcoded DEFAULT_PROJECT.

Two markdown conventions are supported, tried in order:
  1. YAML-style frontmatter at the very top of the file:
        ---
        project_name: Something
        short_description: One-line summary.
        ---
  2. Fallback parsing of the existing nexadose.md layout:
        H1 of the form "# Project: <name>"
        first non-blank line under "## What this is"
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_PROJECT = "nexadose"
PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"


def resolve_slug(cli_value: str | None = None) -> str:
    if cli_value:
        return cli_value.strip()
    env_value = os.environ.get("OPENCLAW_PROJECT", "").strip()
    if env_value:
        return env_value
    return DEFAULT_PROJECT


def load_project(slug: str) -> dict:
    path = PROJECTS_DIR / f"{slug}.md"
    raw = path.read_text(encoding="utf-8") if path.exists() else ""

    info = {
        "project_slug": slug,
        "project_name": slug,
        "short_description": "",
        "raw_context": raw,
    }

    if not raw:
        return info

    fm = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if fm:
        for line in fm.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                info[k] = v
        return info

    h1 = re.search(r"^#\s+Project:\s*(.+?)\s*$", raw, re.MULTILINE)
    if h1:
        info["project_name"] = h1.group(1).strip()

    desc = re.search(
        r"^##\s+What this is\s*\n+(.+?)(?:\n\s*\n|\Z)",
        raw,
        re.MULTILINE | re.DOTALL,
    )
    if desc:
        first_para = desc.group(1).strip().splitlines()[0]
        info["short_description"] = first_para

    return info
