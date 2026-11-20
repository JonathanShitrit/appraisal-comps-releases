#!/usr/bin/env python3
"""
workspace.py - find the appraiser's Appraisal Comps folder (the "workspace")
from wherever a script is running.

The plugin (this folder, `product/`) is installed once in the app's cache; it
is never the workspace. Scripts must find the workspace from the current
directory instead, because the current directory is the only thing that
changes between where this plugin's hooks run (the Mac, cwd unrelated to the
selected folder) and where its skill scripts run (a Linux sandbox, cwd the
sandbox home, with the selected folder mounted under it).

A workspace is a folder holding both settings/conventions.md and shared/.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

NO_WORKSPACE_ERROR = (
    "ERROR: no Appraisal Comps folder here. Open the Appraisal Comps folder "
    "(the one with settings/ and shared/) and try again."
)


def _is_workspace(folder: Path) -> bool:
    """Confirm folder holds both settings/conventions.md and shared/."""
    return (folder / "settings" / "conventions.md").is_file() and (folder / "shared").is_dir()


def find_workspace(start: Path) -> Path | None:
    """Return the nearest folder at or above start that is a workspace, or None."""
    resolved_start = start.resolve()
    for folder in [resolved_start, *resolved_start.parents]:
        if _is_workspace(folder):
            return folder
    return None


WorkspaceSource = Literal["cwd", "CLAUDE_CODE_WORKSPACE_HOST_PATHS", "~/mnt", "none"]


@dataclass
class WorkspaceSearch:
    """The possible workspaces for this run, and which source found them
    ("none" when no source found any)."""

    candidates: list[Path]
    source: WorkspaceSource


def search_workspaces(start: Path) -> WorkspaceSearch:
    """Find the possible workspaces for this run, from the first non-empty source:

    (a) "cwd": the workspace found by climbing from start;
    (b) "CLAUDE_CODE_WORKSPACE_HOST_PATHS": the workspaces among the paths in that
        environment variable, each checked with find_workspace (the desktop app's hooks,
        whose own cwd is never the folder the appraiser selected);
    (c) "~/mnt": the workspaces among the folders directly under ~/mnt/ (skill scripts
        running in the sandbox, where the selected folder is mounted).

    The candidates are deduplicated, in order.
    """
    found = find_workspace(start)
    if found is not None:
        return WorkspaceSearch([found], "cwd")

    candidates: list[Path] = []
    seen: set[Path] = set()

    host_paths = os.environ.get("CLAUDE_CODE_WORKSPACE_HOST_PATHS", "")
    for host_path in host_paths.split(os.pathsep):
        if not host_path:
            continue
        workspace = find_workspace(Path(host_path))
        if workspace is not None and workspace not in seen:
            seen.add(workspace)
            candidates.append(workspace)
    if candidates:
        return WorkspaceSearch(candidates, "CLAUDE_CODE_WORKSPACE_HOST_PATHS")

    mounted = Path.home() / "mnt"
    if mounted.is_dir():
        for child in sorted(mounted.iterdir()):
            if child.is_dir() and _is_workspace(child):
                resolved_child = child.resolve()
                if resolved_child not in seen:
                    seen.add(resolved_child)
                    candidates.append(resolved_child)
    if candidates:
        return WorkspaceSearch(candidates, "~/mnt")
    return WorkspaceSearch([], "none")


def workspace_candidates(start: Path) -> list[Path]:
    """Return the possible workspaces for this run (see search_workspaces)."""
    return search_workspaces(start).candidates


def require_workspace(start: Path) -> Path:
    """Return the one workspace found from start. No workspace or several: print an error, exit 2."""
    candidates = workspace_candidates(start)
    if len(candidates) == 0:
        print(NO_WORKSPACE_ERROR, file=sys.stderr)
        raise SystemExit(2)
    if len(candidates) > 1:
        names = ", ".join(str(candidate) for candidate in candidates)
        print(f"ERROR: found {len(candidates)} Appraisal Comps folders: {names}. "
              "Open the one you want and run again from inside it.", file=sys.stderr)
        raise SystemExit(2)
    return candidates[0]


def plugin_root() -> Path:
    """Return the folder holding .claude-plugin/ (this plugin's own root)."""
    return Path(__file__).resolve().parents[1]


def plugin_version() -> str:
    """Return the plugin's version from plugin.json, or "unknown" if unreadable."""
    plugin_json_path = plugin_root() / ".claude-plugin" / "plugin.json"
    try:
        plugin_data = json.loads(plugin_json_path.read_text())
        return plugin_data["version"]
    except (OSError, ValueError, KeyError):
        return "unknown"
