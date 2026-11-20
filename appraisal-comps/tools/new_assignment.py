#!/usr/bin/env python3
"""
new_assignment.py - create an assignment folder from the plugin's
templates/assignment/.

Run from inside the Appraisal Comps folder:

    python3 -I new_assignment.py --name 2026-01-05_1-test-st_2a

The name is <date YYYY-MM-DD>_<street-number>-<street>_<unit> (houses with no
unit: drop _<unit>). The folder is created under assignments/, with normal
(writable) permissions even though the plugin itself is installed read-only.
An existing folder is never touched.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from template_copy import copy_missing
from workspace import plugin_root, require_workspace

TEMPLATE = plugin_root() / "templates" / "assignment"
NAME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}_[A-Za-z0-9][A-Za-z0-9_-]*")


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="folder name, like 2026-01-05_1-test-st_2a")
    args = parser.parse_args()

    if NAME_PATTERN.fullmatch(args.name) is None:
        die(f"bad assignment name {args.name!r}. Use YYYY-MM-DD_<street-number>-<street>_<unit>, "
            "letters, digits, - and _ only, like 2026-01-05_1-test-st_2a.")

    workspace = require_workspace(Path.cwd())
    assignment = workspace / "assignments" / args.name
    if assignment.exists():
        die(f"{assignment} already exists. Pick another name, or keep working in that folder.")

    copy_missing(TEMPLATE, assignment)
    print(f"created {assignment}")


if __name__ == "__main__":
    main()
