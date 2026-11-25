#!/usr/bin/env python3
"""
launch.py - the browser's MCP stdio server command, started by the app from
.mcp.json. Execs the pinned Browser Harness MCP server through uvx. Harness
drives the appraiser's own Google Chrome over CDP: he ticks "Allow remote
debugging" once at chrome://inspect/#remote-debugging, and Chrome asks him
to click Allow when the first call of a session connects. Claude works in a
tab of its own. There is no address limit in the launcher; guard.py holds the
read-tier rules. It needs nothing from the appraiser's folder, so it starts
at once.

Three changes to the environment the server starts with:
- BROWSER_USE_OFFLINE=1: Harness's browser_feedback and browser_report_run
  send nothing to the vendor.
- BU_CAPTCHA_SOLVER=off: browser_solve_captcha can't turn the solver on.
- BROWSER_USE_API_KEY removed: with a key set, Harness's http_get goes
  through the vendor's fetch service. The key is for cloud_read.py only.

The pin is BROWSER_HARNESS_VERSION. To change it, change the constant, then
refresh the tool-list fixture the guard's tests read:
    python3 -I maintainer/dump_mcp_tools.py \
      --out product/tests/fixtures/browser/harness-tools.json \
      -- uvx --from 'browser-harness[mcp]==<version>' browser-harness-mcp
uvx downloads the package the first time it starts.

stdout is the MCP channel: this script must never print to it. If uvx or
Google Chrome is missing it says so on stderr and exits 1.

    python3 -I launch.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

BROWSER_HARNESS_VERSION = "0.1.14"

# The desktop app starts this with a short PATH, so uvx is also looked for
# where its installer and Homebrew put it. Tests set the variable below to an
# os.pathsep list of folders to use instead.
UVX_DIRS_TEST_ENV = "APPRAISAL_COMPS_TEST_UVX_DIRS"
UVX_FALLBACK_DIRS: tuple[Path, ...] = (
    Path.home() / ".local/bin",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)

# Where Google Chrome lives on a Mac: installed for everyone, or just for him.
CHROME_CANDIDATES: tuple[Path, ...] = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def harness_command() -> list[str]:
    """The argv for the pinned Browser Harness MCP server. Pure: no exec."""
    return ["uvx", "--from", f"browser-harness[mcp]=={BROWSER_HARNESS_VERSION}", "browser-harness-mcp"]


def harness_environment(existing: dict[str, str]) -> dict[str, str]:
    """A copy of existing with the vendor-facing features off and the browser-use key removed."""
    environment = dict(existing)
    environment["BROWSER_USE_OFFLINE"] = "1"
    environment["BU_CAPTCHA_SOLVER"] = "off"
    environment.pop("BROWSER_USE_API_KEY", None)
    return environment


def find_uvx(path_value: str, fallback_dirs: tuple[Path, ...]) -> Path | None:
    """uvx on path_value, else in the first fallback folder that holds an executable uvx."""
    found = shutil.which("uvx", path=path_value)
    if found is not None:
        return Path(found)
    for folder in fallback_dirs:
        candidate = folder / "uvx"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def find_chrome(candidates: tuple[Path, ...]) -> Path | None:
    """The first candidate path that exists, or None if Chrome isn't there."""
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def fallback_dirs() -> tuple[Path, ...]:
    test_dirs = os.environ.get(UVX_DIRS_TEST_ENV)
    if test_dirs is None:
        return UVX_FALLBACK_DIRS
    return tuple(Path(folder) for folder in test_dirs.split(os.pathsep) if folder)


def main() -> None:
    uvx_path = find_uvx(os.environ.get("PATH", ""), fallback_dirs())
    if uvx_path is None:
        print("launch.py: uvx not found; the browser needs uv installed (https://docs.astral.sh/uv/)",
              file=sys.stderr)
        sys.exit(1)

    if find_chrome(CHROME_CANDIDATES) is None:
        print("launch.py: Google Chrome not found in /Applications or ~/Applications; "
              "the browser runs in the appraiser's Chrome", file=sys.stderr)
        sys.exit(1)

    os.execve(str(uvx_path), [str(uvx_path), *harness_command()[1:]], harness_environment(dict(os.environ)))


if __name__ == "__main__":
    main()
