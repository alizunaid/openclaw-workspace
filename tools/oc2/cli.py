"""oc2 CLI dispatcher (argparse).

Subcommands (doc Section 1): design, list, status, approve, build, archive.
design/list/status/approve are real (against the mocked design in Session 1);
build/archive are stubs that announce they are not yet implemented.
"""
from __future__ import annotations

import argparse
import sys

from oc2.approve import cmd_approve
from oc2.build import cmd_build
from oc2.design import cmd_design
from oc2.smoke import cmd_smoke
from oc2.status import cmd_archive, cmd_list, cmd_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oc2",
        description="Tier 2 orchestrator: design -> approve -> build -> status -> revise.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_design = sub.add_parser("design", help="generate an architecture for a task")
    p_design.add_argument("task", help="natural-language description of what to build")
    p_design.add_argument("--name", help="project name (default: kebab-cased first 4 words of task)")
    p_design.add_argument("--from", dest="from_path",
                          help="existing architecture.md to iterate from (not wired yet)")
    p_design.add_argument("--mock", action="store_true",
                          help="use the canned MOCK_ARCHITECTURE instead of calling ollama "
                               "(for fast offline testing of the rest of the pipeline)")
    p_design.set_defaults(func=cmd_design)

    p_list = sub.add_parser("list", help="list all tier-2 projects and their state")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="show one project's per-subsystem state")
    p_status.add_argument("project", nargs="?", help="project name (optional if only one exists)")
    p_status.set_defaults(func=cmd_status)

    p_approve = sub.add_parser("approve", help="re-validate architecture.md and lock for build")
    p_approve.add_argument("project", nargs="?", help="project name (optional if only one exists)")
    p_approve.set_defaults(func=cmd_approve)

    p_build = sub.add_parser("build", help="execute an approved architecture (one ocb subprocess per pending subsystem, halt on failure)")
    p_build.add_argument("project", nargs="?", help="project name (optional if only one exists)")
    p_build.add_argument("--resume", action="store_true",
                         help="resume from the first non-done subsystem (this is the default; the flag exists to surface resumability)")
    p_build.add_argument("--only", help="rebuild a single subsystem (escape hatch for stochastic ocb failures; doc §3)")
    p_build.set_defaults(func=cmd_build)

    p_smoke = sub.add_parser("smoke", help="import all built subsystems in topo order and run the integration chain on a real CSV")
    p_smoke.add_argument("project", nargs="?", help="project name (optional if only one exists)")
    p_smoke.set_defaults(func=cmd_smoke)

    p_archive = sub.add_parser("archive", help="archive a project (stub in Session 1)")
    p_archive.add_argument("project", nargs="?", help="project name")
    p_archive.set_defaults(func=cmd_archive)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
