#!/usr/bin/env python3
"""
template_copy.py - copy a folder from the plugin's templates/ into the
appraiser's workspace.

The desktop app installs the plugin read-only. shutil.copy2, shutil.copytree
and `cp -R` all copy a file's permissions along with its contents, so their
copies come out read-only too, and nothing (the appraiser, Claude, ledger.py)
can write to them. This module copies contents only: every file and folder it
creates gets the normal permissions of a new file.
"""
from __future__ import annotations

import shutil
from pathlib import Path


def copy_missing(template: Path, target: Path) -> list[str]:
    """Copy every file under template that is missing from target.

    A file already in target is never overwritten. Returns one line per
    file, "created <path>" or "kept <path>", with paths relative to target.
    """
    report: list[str] = []
    target.mkdir(parents=True, exist_ok=True)
    for template_path in sorted(template.rglob("*")):
        if template_path.is_dir():
            continue
        relative_path = template_path.relative_to(template)
        target_path = target / relative_path
        if target_path.exists():
            report.append(f"kept {relative_path}")
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template_path, target_path)
        report.append(f"created {relative_path}")
    return report
