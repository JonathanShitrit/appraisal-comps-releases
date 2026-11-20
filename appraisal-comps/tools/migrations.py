#!/usr/bin/env python3
"""
migrations.py - runs the migrations that bring the appraiser's own data
(settings/, shared/, assignments/, exports/) up to date with a newer plugin
version.

Used by hooks/session_start.py at the first session on a new version. See
product/migrations/README.md for how to write a migration file.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import plugin_root

VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
MIGRATION_NAME_PATTERN = re.compile(r"^(\d+)_(\d+)_(\d+)_[A-Za-z0-9_-]+\.py$")


class MigrationError(Exception):
    """Raised when a migration file can't be run.

    name is the migration file's stem (for example "0_5_0_boom"); detail is
    what went wrong.
    """

    def __init__(self, name: str, detail: str) -> None:
        super().__init__(f"{name}: {detail}")
        self.name = name
        self.detail = detail


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse a strict X.Y.Z version string into a tuple that compares as numbers."""
    match = VERSION_PATTERN.match(text)
    if match is None:
        raise MigrationError("parse_version", f"not a valid version: {text!r}. Expected X.Y.Z, e.g. 0.5.0.")
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch))


def migrations_to_run(migrations_dir: Path, from_version: str, to_version: str) -> list[Path]:
    """The migration files in migrations_dir to run when going from
    from_version to to_version: files named <X>_<Y>_<Z>_<slug>.py whose
    version is above from_version and at or below to_version, sorted by
    version, then name. Any other file is ignored."""
    if not migrations_dir.is_dir():
        return []
    lowest_excluded = parse_version(from_version)
    highest_included = parse_version(to_version)
    selected: list[tuple[tuple[int, int, int], str, Path]] = []
    for path in migrations_dir.iterdir():
        match = MIGRATION_NAME_PATTERN.match(path.name)
        if match is None or not path.is_file():
            continue
        major, minor, patch = match.groups()
        version = (int(major), int(minor), int(patch))
        if lowest_excluded < version <= highest_included:
            selected.append((version, path.name, path))
    selected.sort()
    return [path for _version, _name, path in selected]


def _run_one_migration(root: Path, migration_path: Path, position: int) -> None:
    """Import one migration file under a unique module name and call its
    migrate(root). Any failure raises MigrationError(name, detail)."""
    name = migration_path.stem
    module_name = f"appraisal_comps_migration_{position}_{name}"
    spec = importlib.util.spec_from_file_location(module_name, migration_path)
    if spec is None or spec.loader is None:
        raise MigrationError(name, "could not load the migration file.")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise MigrationError(name, f"could not load the migration: {error}") from error

    migrate = getattr(module, "migrate", None)
    if not callable(migrate):
        raise MigrationError(name, "has no migrate(root) function.")
    try:
        migrate(root)
    except Exception as error:
        raise MigrationError(name, str(error)) from error


def run_pending(workspace: Path, from_version: str, to_version: str) -> list[str]:
    """Run every migration in this plugin's migrations/ folder above
    from_version and at or below to_version, in version then name order.

    Returns the stems of the migrations that ran. Raises MigrationError on
    the first failure, leaving any later migrations unrun.
    """
    migrations_dir = plugin_root() / "migrations"
    migrations = migrations_to_run(migrations_dir, from_version, to_version)
    names_run: list[str] = []
    for position, migration_path in enumerate(migrations):
        _run_one_migration(workspace, migration_path, position)
        names_run.append(migration_path.stem)
    return names_run
