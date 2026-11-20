#!/usr/bin/env python3
"""
setup_workspace.py - copy the Appraisal Comps workspace template into the
appraiser's folder.

Run once, when the appraiser first installs the appraisal-comps plugin:

    python3 -I setup_workspace.py --into <the folder he selected>

Copies every file from templates/workspace/ that is missing from the target
folder, and prints "created <path>" or "kept <path>" for each one. A file
that is already there is never overwritten, so running this again after an
update leaves his settings and data exactly as he left them.

With --name dcohen it also saves his name, for the learning export, in
settings/export.json (replacing any saved name). The name is checked before
anything is copied.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_name import NAME_RULE_MESSAGE, is_valid_name
from template_copy import copy_missing
from workspace import plugin_root

TEMPLATE = plugin_root() / "templates" / "workspace"


def save_name(workspace: Path, name: str) -> None:
    settings_file = workspace / "settings" / "export.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps({"name": name}, indent=1) + "\n")
    print(f"saved settings/export.json (name {name})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--into", required=True, help="the folder he selected for this workflow")
    parser.add_argument("--name", help="his name for the learning export, like dcohen")
    args = parser.parse_args()
    if args.name is not None and not is_valid_name(args.name):
        print(f"ERROR: {NAME_RULE_MESSAGE}", file=sys.stderr)
        sys.exit(2)
    workspace = Path(args.into)
    for line in copy_missing(TEMPLATE, workspace):
        print(line)
    if args.name is not None:
        save_name(workspace, args.name)


if __name__ == "__main__":
    main()
