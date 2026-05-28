"""Entry point for oc2.

Invoked two ways, both supported:
  - `python3 /root/.openclaw/workspace/tools/oc2/__main__.py ...`  (the `oc2` alias)
  - `python3 -m oc2 ...`                                            (from tools/)

When run as a script file, the package's parent (tools/) is not on sys.path, so
we add it before importing the package, then dispatch to cli.main().
"""
import os
import sys


def _bootstrap_path() -> None:
    # tools/oc2/__main__.py -> tools/oc2 -> tools
    tools_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)


if __name__ == "__main__":
    _bootstrap_path()
    from oc2.cli import main
    sys.exit(main())
