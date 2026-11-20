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
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from template_copy import copy_missing
from workspace import plugin_root

TEMPLATE = plugin_root() / "templates" / "workspace"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--into", required=True, help="the folder he selected for this workflow")
    args = parser.parse_args()
    for line in copy_missing(TEMPLATE, Path(args.into)):
        print(line)


if __name__ == "__main__":
    main()
