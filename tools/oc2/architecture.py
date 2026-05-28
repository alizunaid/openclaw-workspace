"""Architecture markdown: parser + structural validator for oc2 (Tier 2 v1).

The architecture is markdown because the human reads and edits it as part of the
design loop. This module is the contract between three audiences: the LLM that
produces the markdown, the human who reviews it, and the build phase that will
later consume it to drive Tier 1 (`ocb`).

Schema (design/tier2_v1.md Section 2):

    # Architecture: <project name>

    ## Overview
    <one paragraph>

    ## Subsystems

    ### <subsystem-name>
    **Purpose:** <one paragraph>
    **Inputs:** <bulleted list>
    **Outputs:** <bulleted list>
    **Depends on:** <comma-separated names, or `none`>
    **Owns state:** <prose, or `stateless`>
    **Failure modes:** <bulleted list>

    ## Data flow
    <one paragraph>

    ## Cross-cutting failure modes
    <prose>

    ## Integration points
    <prose>

This module is pure (no Tier 1 imports, no filesystem) so it is trivially
testable and reusable by design/approve.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# --- limits / naming, per doc Section 2 ------------------------------------

MIN_SUBSYSTEMS = 2
MAX_SUBSYSTEMS = 10
MAX_DEPS_SOFT = 4          # >4 deps -> soft warning (probably under-decomposed)
RESERVED_NAMES = {"main"}  # reserved; build phase owns `main`
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Canonical subsystem field labels -> attribute keys.
_FIELD_LABELS = {
    "purpose": "purpose",
    "inputs": "inputs",
    "outputs": "outputs",
    "depends on": "depends_on",
    "owns state": "owns_state",
    "failure modes": "failure_modes",
}
REQUIRED_FIELDS = ("purpose", "inputs", "outputs", "depends_on", "owns_state", "failure_modes")
LIST_FIELDS = ("inputs", "outputs", "failure_modes")

# A bold field label at the start of a line. Tolerates both `**Purpose:**` (colon
# inside the bold) and `**Purpose**:` (colon after), plus surrounding whitespace.
_FIELD_RE = re.compile(
    r"^\s*\*\*\s*(?P<label>[A-Za-z][A-Za-z ]*?)\s*:?\s*\*\*\s*:?\s*(?P<rest>.*)$"
)
# A markdown bullet line.
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<item>.+?)\s*$")
# The H1 title: `# Architecture: <name>`.
_TITLE_RE = re.compile(r"^#\s+Architecture:\s*(?P<name>.+?)\s*$", re.MULTILINE)


@dataclass
class Subsystem:
    name: str
    purpose: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    owns_state: str = ""
    failure_modes: list[str] = field(default_factory=list)
    raw_section: str = ""          # raw markdown of just this subsystem (for spec_sha256)
    present_fields: set = field(default_factory=set)  # which labels actually appeared

    def spec_sha256(self) -> str:
        """SHA-256 of just this subsystem's markdown section (doc Section 4)."""
        return hashlib.sha256(self.raw_section.encode("utf-8")).hexdigest()


@dataclass
class Architecture:
    project_name: str
    overview: str = ""
    subsystems: list[Subsystem] = field(default_factory=list)
    data_flow: str = ""
    cross_cutting_failure_modes: str = ""
    integration_points: str = ""
    raw: str = ""

    def by_name(self) -> dict[str, Subsystem]:
        return {s.name: s for s in self.subsystems}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    topo_order: list[str] | None = None  # subsystem names, deps before dependents

    @property
    def ok(self) -> bool:
        return not self.errors


class ParseError(ValueError):
    """Raised only for structural failures that prevent any parse at all."""


# --- parsing ----------------------------------------------------------------

def _split_sections(body: str) -> dict[str, str]:
    """Split a markdown body by `## ` level-2 headers into {lower_heading: text}.

    Heading text is normalized to lowercase. Subsections (`### `) stay inside the
    parent section's text.
    """
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(?P<h>.+?)\s*$", line)
        if m and not line.startswith("###"):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group("h").strip().lower()
            buf = []
        else:
            if current is not None:
                buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _parse_subsystem_block(name: str, block: str) -> Subsystem:
    """Parse the lines under a `### <name>` header into a Subsystem."""
    sub = Subsystem(name=name, raw_section=f"### {name}\n{block}".strip())

    # Walk lines, assigning each to the most recently-seen field label.
    field_lines: dict[str, list[str]] = {}
    current_key: str | None = None
    for line in block.splitlines():
        m = _FIELD_RE.match(line)
        if m:
            label = re.sub(r"\s+", " ", m.group("label").strip().lower())
            key = _FIELD_LABELS.get(label)
            if key:
                current_key = key
                sub.present_fields.add(key)
                field_lines.setdefault(key, [])
                rest = m.group("rest").strip()
                if rest:
                    field_lines[key].append(rest)
                continue
        if current_key is not None:
            field_lines[current_key].append(line)

    for key, lines in field_lines.items():
        if key in LIST_FIELDS:
            setattr(sub, key, _parse_list(lines))
        elif key == "depends_on":
            sub.depends_on = _parse_depends_on(lines)
        else:  # purpose, owns_state -> prose
            setattr(sub, key, _join_prose(lines))
    return sub


def _parse_list(lines: list[str]) -> list[str]:
    """Collect markdown bullets; if no bullets but inline prose exists, treat the
    prose as a single item (loose enough for hand-edits, never silently empty)."""
    items: list[str] = []
    inline: list[str] = []
    for line in lines:
        bm = _BULLET_RE.match(line)
        if bm:
            items.append(bm.group("item").strip())
        elif line.strip():
            inline.append(line.strip())
    if items:
        return items
    joined = " ".join(inline).strip()
    if joined and joined.lower() != "none":
        return [joined]
    return []


def _parse_depends_on(lines: list[str]) -> list[str]:
    """Comma-separated names on one line, or `none`. Also tolerates bullets."""
    bullets = [m.group("item").strip() for line in lines if (m := _BULLET_RE.match(line))]
    if bullets:
        raw = bullets
    else:
        text = " ".join(l.strip() for l in lines if l.strip())
        raw = [p.strip() for p in text.split(",")]
    out = []
    for token in raw:
        token = token.strip().strip("`").strip()
        if not token or token.lower() == "none":
            continue
        out.append(token)
    return out


def _join_prose(lines: list[str]) -> str:
    return "\n".join(l.rstrip() for l in lines).strip()


def parse(md: str) -> Architecture:
    """Parse architecture markdown into an Architecture model.

    Lenient on content (missing fields/sections become empty — the validator
    reports those). Raises ParseError only when there is no parseable title,
    which means the input is not an architecture document at all.
    """
    title_m = _TITLE_RE.search(md)
    if not title_m:
        raise ParseError("missing `# Architecture: <name>` title")
    project_name = title_m.group("name").strip()

    # Everything after the title line is the body to split into ## sections.
    body = md[title_m.end():]
    sections = _split_sections(body)

    arch = Architecture(
        project_name=project_name,
        overview=sections.get("overview", ""),
        data_flow=sections.get("data flow", ""),
        cross_cutting_failure_modes=sections.get("cross-cutting failure modes", ""),
        integration_points=sections.get("integration points", ""),
        raw=md,
    )

    subs_text = sections.get("subsystems", "")
    if subs_text:
        # Split into `### <name>` blocks.
        parts = re.split(r"(?m)^###\s+(?P<name>.+?)\s*$", subs_text)
        # re.split with one group yields: [pre, name1, block1, name2, block2, ...]
        it = iter(parts[1:])
        for name in it:
            block = next(it, "")
            arch.subsystems.append(_parse_subsystem_block(name.strip(), block))
    return arch


# --- validation -------------------------------------------------------------

def validate(arch: Architecture) -> ValidationResult:
    """Structural validation: schema fields, DAG, closure, sanity bounds.

    Hard errors block approval; soft warnings are advisory (doc Section 2).
    """
    res = ValidationResult()
    errors, warnings = res.errors, res.warnings

    if not arch.project_name:
        errors.append("missing project name in title")

    subs = arch.subsystems
    names = [s.name for s in subs]

    # --- count bounds
    if len(subs) < MIN_SUBSYSTEMS:
        errors.append(f"too few subsystems: {len(subs)} (minimum {MIN_SUBSYSTEMS})")
    if len(subs) > MAX_SUBSYSTEMS:
        errors.append(f"too many subsystems: {len(subs)} (maximum {MAX_SUBSYSTEMS})")

    # --- per-subsystem: naming + required fields
    seen: set[str] = set()
    for s in subs:
        if s.name in seen:
            errors.append(f"duplicate subsystem name: {s.name!r}")
        seen.add(s.name)
        if s.name in RESERVED_NAMES:
            errors.append(f"subsystem {s.name!r} uses a reserved name")
        if not KEBAB_RE.match(s.name):
            errors.append(f"subsystem {s.name!r} is not a kebab-case identifier")

        # required field labels must all be present
        for f in REQUIRED_FIELDS:
            if f not in s.present_fields:
                errors.append(f"subsystem {s.name!r}: missing required field '{f}'")
        # content requirements
        if "purpose" in s.present_fields and not s.purpose.strip():
            errors.append(f"subsystem {s.name!r}: 'purpose' is empty")
        if "owns_state" in s.present_fields and not s.owns_state.strip():
            errors.append(f"subsystem {s.name!r}: 'owns_state' is empty (use `stateless`)")
        if "failure_modes" in s.present_fields and not s.failure_modes:
            errors.append(f"subsystem {s.name!r}: 'failure_modes' has no entries")

        # soft warnings
        if len(s.depends_on) > MAX_DEPS_SOFT:
            warnings.append(
                f"subsystem {s.name!r} has {len(s.depends_on)} deps "
                f"(>{MAX_DEPS_SOFT}; probably under-decomposed)"
            )
        if not s.inputs and not s.outputs:
            warnings.append(
                f"subsystem {s.name!r} has no inputs and no outputs (probably dead code)"
            )

    # subsystem named after the whole project -> soft warning
    project_slug = _kebab(arch.project_name)
    if project_slug in names:
        warnings.append(
            f"subsystem {project_slug!r} is named after the whole project "
            f"(probably under-decomposed)"
        )

    # missing optional top-level sections -> soft warning (Subsystems is required
    # structurally; the rest are part of the documented schema but advisory)
    for label, text in (
        ("Overview", arch.overview),
        ("Data flow", arch.data_flow),
        ("Cross-cutting failure modes", arch.cross_cutting_failure_modes),
        ("Integration points", arch.integration_points),
    ):
        if not text.strip():
            warnings.append(f"missing or empty '## {label}' section")

    # --- closure + self-dependency
    name_set = set(names)
    for s in subs:
        for d in s.depends_on:
            if d == s.name:
                errors.append(f"subsystem {s.name!r}: self-dependency")
            elif d not in name_set:
                errors.append(f"subsystem {s.name!r}: depends_on {d!r} which does not exist")

    # If structure is already broken, don't attempt the topo sort.
    if errors:
        return res

    res.topo_order = _topo_sort(subs)
    if res.topo_order is None:
        errors.append("dependency graph has a cycle")
    return res


def _topo_sort(subs: list[Subsystem]) -> list[str] | None:
    """Kahn's algorithm, alphabetical tiebreak for deterministic resume order.
    Returns names deps-before-dependents, or None if a cycle exists."""
    in_degree = {s.name: 0 for s in subs}
    graph: dict[str, list[str]] = {s.name: [] for s in subs}
    for s in subs:
        for d in s.depends_on:
            graph[d].append(s.name)
            in_degree[s.name] += 1
    queue = sorted(n for n, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for nxt in sorted(graph[node]):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
        queue.sort()
    if len(order) != len(subs):
        return None
    return order


def _kebab(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
