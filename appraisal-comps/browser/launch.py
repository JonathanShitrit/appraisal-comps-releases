#!/usr/bin/env python3
"""
launch.py - the browser's MCP stdio server command, started by the app from
.mcp.json. Execs the pinned Playwright MCP server restricted to the work
sites built into the plugin (browser/sites.py). It needs nothing from the
appraiser's folder, so it starts at once.

stdout is the MCP channel: this script must never print to it. If npx is
missing it says so on stderr and exits 1.

    python3 -I launch.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sites import site_origins  # noqa: E402

PLAYWRIGHT_MCP_VERSION = "0.0.85"


def playwright_command(profile_dir: Path) -> list[str]:
    """The argv for the pinned Playwright MCP server, restricted to the
    built-in sites' origins. Pure: no filesystem writes, no exec."""
    origins = site_origins()
    return [
        "npx", "-y", f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}",
        "--user-data-dir", str(profile_dir),
        "--allowed-origins", ";".join(origins),
    ]


def main() -> None:
    # Fix round 1, item 5: check npx is there before creating anything. If the
    # browser isn't going to start, there's no profile folder to create either.
    npx_path = shutil.which("npx")
    if npx_path is None:
        print("launch.py: npx not found on PATH; the browser needs Node installed", file=sys.stderr)
        sys.exit(1)

    # The profile stays out of CLAUDE_PLUGIN_DATA (it changes every session -
    # see the spike results) and out of the workspace (never exported or
    # synced). Created only now, because the browser is actually launching.
    profile_dir = Path.home() / "Library/Application Support/Appraisal Comps/browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    # This Mac's npm registry stalled the spike; pin the public one before exec.
    os.environ["npm_config_registry"] = "https://registry.npmjs.org/"

    command = playwright_command(profile_dir)
    os.execvp(npx_path, [npx_path, *command[1:]])


if __name__ == "__main__":
    main()
