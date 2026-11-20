#!/usr/bin/env python3
"""
launch.py - the browser's MCP stdio server command, started by the app from
.mcp.json. Execs the pinned Playwright MCP server in extension mode: Claude
works in the appraiser's everyday Google Chrome, through the Playwright
Extension. The first browser call opens the extension's connect page, which
he approves once per session, and Claude's tabs open in their own tab group.
The only address limit is guard.py's check against the built-in work sites
(browser/sites.py). It needs nothing from the appraiser's folder, so it
starts at once.

The old browser-profile folder (~/Library/Application Support/Appraisal
Comps/browser-profile) is no longer created, and is left alone.

stdout is the MCP channel: this script must never print to it. If npx or
Google Chrome is missing it says so on stderr and exits 1.

    python3 -I launch.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PLAYWRIGHT_MCP_VERSION = "0.0.85"

# Where Google Chrome lives on a Mac: installed for everyone, or just for him.
CHROME_CANDIDATES: tuple[Path, ...] = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def playwright_command() -> list[str]:
    """The argv for the pinned Playwright MCP server, attached to his Chrome
    through the extension. Pure: no filesystem writes, no exec."""
    return [
        "npx", "-y", f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}",
        "--extension", "--browser", "chrome",
    ]


def find_chrome(candidates: tuple[Path, ...]) -> Path | None:
    """The first candidate path that exists, or None if Chrome isn't there."""
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def main() -> None:
    npx_path = shutil.which("npx")
    if npx_path is None:
        print("launch.py: npx not found on PATH; the browser needs Node installed", file=sys.stderr)
        sys.exit(1)

    if find_chrome(CHROME_CANDIDATES) is None:
        print("launch.py: Google Chrome not found in /Applications or ~/Applications; "
              "the browser runs in the appraiser's Chrome", file=sys.stderr)
        sys.exit(1)

    # This Mac's npm registry stalled the spike; pin the public one before exec.
    os.environ["npm_config_registry"] = "https://registry.npmjs.org/"

    command = playwright_command()
    os.execvp(npx_path, [npx_path, *command[1:]])


if __name__ == "__main__":
    main()
