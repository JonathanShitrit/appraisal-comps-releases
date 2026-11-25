#!/usr/bin/env python3
"""
browser_scripts.py - the plugin's two page scripts and how to recognise them.

browser/scripts/read.js reads a page; browser/scripts/elements.js lists what
can be clicked. The guard lets browser_js run only these, and post_tool.py
needs to know which of the two a call ran. Both ask here: a script is
identified by the SHA-256 of its text with every run of whitespace collapsed
to one space, so line endings and indentation don't matter and nothing else
does.

Imported by guard.py and post_tool.py, which put this folder and tools/ on
sys.path first (python3 -I does not).
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from workspace import plugin_root  # noqa: E402

SCRIPTS_DIR = plugin_root() / "browser" / "scripts"
WHITESPACE_RUN = re.compile(r"\s+")


def normalize_script(text: str) -> str:
    """text, stripped, with every run of whitespace turned into one space."""
    return WHITESPACE_RUN.sub(" ", text.strip())


def script_hash(text: str) -> str:
    return hashlib.sha256(normalize_script(text).encode("utf-8")).hexdigest()


def known_scripts() -> dict[str, str]:
    """Hash -> file name, for every .js file in SCRIPTS_DIR. Empty when the
    folder is missing or unreadable, so every script is then refused."""
    scripts: dict[str, str] = {}
    try:
        script_files = sorted(SCRIPTS_DIR.glob("*.js"))
        for script_file in script_files:
            scripts[script_hash(script_file.read_text(encoding="utf-8"))] = script_file.name
    except (OSError, UnicodeDecodeError):
        return {}
    return scripts


def script_name(text: object) -> str | None:
    """The file name of the plugin script that text is, or None."""
    if not isinstance(text, str):
        return None
    return known_scripts().get(script_hash(text))
