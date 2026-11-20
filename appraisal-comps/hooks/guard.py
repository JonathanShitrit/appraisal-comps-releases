#!/usr/bin/env python3
"""
guard.py - the plugin's PreToolUse hook. Allows, denies or asks on every
tool call matched by hooks.json: Edit, Write, MultiEdit, NotebookEdit,
WebFetch (and the chat side's mcp__workspace__web_fetch), WebSearch (always denied), Bash (both the desktop app's own Bash tool and the sandbox's
mcp__workspace__bash), and (Task 6) the browser's MCP tools.

This is a deterministic limit (Design Principles, "Trust"), not an
instruction: it fails closed. Unreadable hook input is a deny with a
reason, and so is any unexpected error - including a missing or broken
tools/workspace.py - never a traceback, and never a silent allow.

    python3 -I guard.py            # reads the hook's JSON payload on stdin

Hook output follows the hooks contract: allow is exit 0 with nothing
printed; deny or ask is exit 0 with
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
 "permissionDecision": "deny"|"ask", "permissionDecisionReason": "<reason>"}}.
"""
from __future__ import annotations

import json
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlparse


def _deny_and_exit(reason: str) -> None:
    """Print the hooks-contract deny JSON and exit 0, then stop the
    process here. Used only when this module can't load far enough to
    reach main() normally (see the import below): a guard that can't
    start must still fail closed, never a traceback and never exit 1
    (PreToolUse treats a non-zero exit as no opinion, i.e. an open gate)."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
try:
    from workspace import plugin_root  # noqa: E402
except Exception as _import_error:  # tools/workspace.py missing or broken: fail closed
    _deny_and_exit(f"guard failed to load workspace.py: {_import_error}")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "browser"))
try:
    from sites import origin_of, site_origins  # noqa: E402
except Exception as _import_error:  # browser/sites.py missing or broken: fail closed
    _deny_and_exit(f"guard failed to load sites.py: {_import_error}")

MALFORMED_INPUT_REASON = "guard could not read the tool call"
REPORT_INSTEAD = "report the failing check instead."

DATA_HOSTS = ("data.cityofnewyork.us", "a836-acris.nyc.gov")

NETWORK_COMMAND_WORDS = ("curl", "wget", "scp", "ssh", "nc", "ftp", "sftp")

# Segments that mark a path as belonging to a plugin install, wherever it is
# mounted. The last one carries no leading slash on purpose: it names a
# folder that can sit at different depths. Compared case-folded below,
# because the real filesystem (macOS/APFS) may be case-insensitive.
PLUGIN_PATH_SEGMENTS = ("/.claude/plugins/", "/.local-plugins/", "claude-hostloop-plugins/")

CALIBRATION_FILENAME = "calibration.csv"
WEBSEARCH_REASON = ("web search is not allowed in this project. Network use is read-only queries to "
                    "NYC Open Data (data.cityofnewyork.us) and ACRIS (a836-acris.nyc.gov), and the "
                    "browser on the plugin's work sites.")

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
BASH_TOOLS = ("Bash", "mcp__workspace__bash")
# WebFetch, and the desktop chat side's own fetch tool (desktop check, 2026-11-19).
WEBFETCH_TOOLS = ("WebFetch", "mcp__workspace__web_fetch")

# The app names plugin MCP tools mcp__plugin_<plugin>_<server>__<tool>; this
# plugin's browser MCP server is named "browser", and its tools (Playwright's)
# are themselves named browser_navigate, browser_snapshot, and so on.
BROWSER_TOOL_PREFIX = "mcp__plugin_appraisal-comps_browser__browser_"

# Read tier only (Design Principles, "Trust", Rule of Two): every browser
# tool that only looks at or navigates the page. file_upload, evaluate,
# run_code_unsafe, drag, drop, and any future tool are denied by omission.
BROWSER_ALLOWED_TOOLS = (
    "navigate", "navigate_back", "snapshot", "take_screenshot", "click", "hover",
    "type", "press_key", "select_option", "fill_form", "wait_for", "tabs", "close", "find",
)

# A click or fill_form naming one of these, as a whole word, looks like it
# changes something on the site rather than just reading it: ask instead of
# allowing. This is a speed bump, not a limit - the real limits are the
# origin check on every address Claude opens and the tools left off
# BROWSER_ALLOWED_TOOLS. (A page can still redirect to another origin:
# Playwright's --allowed-origins doesn't stop redirects.)
BROWSER_ASK_WORDS = ("submit", "send", "deliver", "accept", "decline", "sign", "upload",
                     "pay", "delete", "complete", "assign", "finalize")

NETWORK_WORD_RE = re.compile(r"\b(?:" + "|".join(NETWORK_COMMAND_WORDS) + r")\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
BROWSER_ASK_WORD_RE = re.compile(r"\b(?:" + "|".join(BROWSER_ASK_WORDS) + r")\b", re.IGNORECASE)

Action = Literal["allow", "deny", "ask"]


@dataclass
class Decision:
    action: Action
    reason: str = ""


ALLOW = Decision("allow")


def _lexical_parts(path_str: str) -> tuple[str, ...]:
    """path_str's parts after resolving ".." lexically, without touching
    the filesystem - the path may belong to another machine, or not exist
    at all (a sandbox path, or one not yet written)."""
    return PurePosixPath(posixpath.normpath(path_str)).parts


def _is_calibration_csv(path_str: str) -> bool:
    """True when path_str's basename casefolds to calibration.csv,
    regardless of what directory holds it (R11a: a copy or a symlink
    placed elsewhere is still the appraiser's answer key, and the real
    filesystem may be case-insensitive)."""
    parts = _lexical_parts(path_str)
    return bool(parts) and parts[-1].casefold() == CALIBRATION_FILENAME.casefold()


def _is_plugin_path(path_str: str) -> bool:
    """True when path_str names a file inside some plugin install: this
    guard's own plugin root, or a path carrying a known plugin-cache
    segment under any HOME or mount point. Case-folded throughout, since
    the real filesystem may be case-insensitive."""
    normalized = posixpath.normpath(path_str).casefold()
    if any(segment.casefold() in normalized for segment in PLUGIN_PATH_SEGMENTS):
        return True
    root_parts = tuple(part.casefold() for part in _lexical_parts(str(plugin_root())))
    target_parts = tuple(part.casefold() for part in _lexical_parts(path_str))
    return target_parts[:len(root_parts)] == root_parts


def _edit_path_key(tool_name: str) -> str:
    """Which tool_input key carries the path for tool_name: NotebookEdit
    uses notebook_path, everything else in EDIT_TOOLS uses file_path."""
    return "notebook_path" if tool_name == "NotebookEdit" else "file_path"


def _decide_edit(tool_name: str, tool_input: dict[str, object]) -> Decision:
    value = tool_input.get(_edit_path_key(tool_name))
    if not isinstance(value, str) or not value:
        return Decision("deny", MALFORMED_INPUT_REASON)
    if _is_calibration_csv(value):
        return Decision("deny", f"{value} is the appraiser's calibration answer key; {REPORT_INSTEAD}")
    if _is_plugin_path(value):
        return Decision("deny", f"{value} is inside the plugin's own files; {REPORT_INSTEAD}")
    return ALLOW


def _url_host(url_str: str) -> str | None:
    """The lowercased host of url_str, or None when it doesn't parse as
    an http(s) URL, or is ambiguous between parsers: a backslash (the
    WHATWG URL parser WebFetch and Node actually use treats "\\" as a
    path separator; Python's urlparse doesn't) or userinfo before the
    host (an "x@host" prefix can smuggle a different real host past a
    parser that stops at the first "@")."""
    if "\\" in url_str:
        return None
    try:
        parsed = urlparse(url_str)
    except ValueError:
        return None
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    return parsed.hostname


def _is_data_host_url(url_str: str) -> bool:
    host = _url_host(url_str)
    return host is not None and host in DATA_HOSTS


def _decide_webfetch(tool_input: dict[str, object]) -> Decision:
    url_value = tool_input.get("url")
    if not isinstance(url_value, str):
        return Decision("deny", "WebFetch has no URL to check")
    host = _url_host(url_value)
    if host is None:
        return Decision("deny", f"{url_value} does not parse as an unambiguous http(s) URL")
    if host not in DATA_HOSTS:
        return Decision("deny", f"{host} is not a data host")
    return ALLOW


def _decide_bash(tool_input: dict[str, object]) -> Decision:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return Decision("deny", MALFORMED_INPUT_REASON)
    if CALIBRATION_FILENAME in command.casefold():
        return Decision("deny", f"command names {CALIBRATION_FILENAME}; {REPORT_INSTEAD}")
    word_match = NETWORK_WORD_RE.search(command)
    if word_match is not None:
        return Decision("deny", f"command uses {word_match.group()}, which is not allowed")
    for url_match in URL_RE.finditer(command):
        url_str = url_match.group()
        if not _is_data_host_url(url_str):
            return Decision("deny", f"{url_str} is not a data host")
    return ALLOW


BROWSER_ASK_DECISION = Decision("ask", "this looks like it changes something on this site; the appraiser decides")


def _browser_ask_texts(browser_tool: str, tool_input: dict[str, object]) -> list[str]:
    """The text fields to check against BROWSER_ASK_WORD_RE for browser_tool:
    a click's or fill_form's own "element" (if present - falls back to no
    text, never a crash, when it's missing), plus - for fill_form - each
    item in "fields"' own "name" and "element" (Playwright's fill_form input
    is {"fields": [{"name": .., "type": .., "value": .., "element"? .., ..}]};
    binding decision 5 checks both)."""
    texts: list[str] = []
    element = tool_input.get("element")
    if isinstance(element, str):
        texts.append(element)
    if browser_tool == "fill_form":
        fields = tool_input.get("fields")
        if isinstance(fields, list):
            for field in fields:
                if isinstance(field, dict):
                    for key in ("name", "element"):
                        value = field.get(key)
                        if isinstance(value, str):
                            texts.append(value)
    return texts


def _decide_browser_url(url_value: object) -> Decision:
    """Shared by navigate and by a tabs call that carries a url (R12: a new
    tab can load a page exactly like navigate does, so it needs the same
    origin check - a tabs call with no url, e.g. list/switch/close, is left
    to the caller, since it never leaves the allowed origins at all)."""
    if not isinstance(url_value, str) or not url_value:
        return Decision("deny", MALFORMED_INPUT_REASON)
    origin = origin_of(url_value)
    if origin and origin in site_origins():
        return ALLOW
    return Decision("deny", f"{origin or url_value} is not one of the plugin's work sites")


def _decide_browser_filename(filename: object) -> Decision:
    """Browser tools that save a file (take_screenshot, snapshot, ...) take a
    "filename". Only a plain file name, which Playwright saves in its own
    output folder, is allowed: anything absolute, with a folder separator
    or "..", could land anywhere, and a protected name is refused outright."""
    if not isinstance(filename, str):
        return Decision("deny", MALFORMED_INPUT_REASON)
    if "/" in filename or "\\" in filename or ".." in filename:
        return Decision("deny", f"browser filename {filename!r} must be a plain file name, like page.png")
    if filename.casefold() == CALIBRATION_FILENAME.casefold():
        return Decision("deny", f"browser filename {filename!r} is a protected file name")
    return ALLOW


def _decide_browser(tool_name: str, tool_input: dict[str, object]) -> Decision:
    browser_tool = tool_name[len(BROWSER_TOOL_PREFIX):]
    if browser_tool not in BROWSER_ALLOWED_TOOLS:
        return Decision("deny", f"browser_{browser_tool} is not a read-tier tool")
    if "filename" in tool_input:
        filename_decision = _decide_browser_filename(tool_input.get("filename"))
        if filename_decision.action != "allow":
            return filename_decision
    if browser_tool == "navigate":
        return _decide_browser_url(tool_input.get("url"))
    if browser_tool == "tabs" and "url" in tool_input:
        return _decide_browser_url(tool_input.get("url"))
    if browser_tool in ("click", "fill_form"):
        texts = _browser_ask_texts(browser_tool, tool_input)
        if BROWSER_ASK_WORD_RE.search(" ".join(texts)):
            # Playwright's click/fill_form input carries no URL (just
            # element/target, or field names), so there's no site to name here.
            return BROWSER_ASK_DECISION
    if browser_tool == "type" and tool_input.get("submit") is True:
        # R14: typing into a field and pressing Enter/submitting it can act
        # just like a submit-like click. "is True" on purpose: a truthy
        # string ("true") or 1 is not Playwright's own boolean and must not
        # trigger this by accident.
        return BROWSER_ASK_DECISION
    return ALLOW


def decide(tool_name: str, tool_input: dict[str, object]) -> Decision:
    """Pure: no I/O, no filesystem writes."""
    if tool_name in EDIT_TOOLS:
        return _decide_edit(tool_name, tool_input)
    if tool_name in WEBFETCH_TOOLS:
        return _decide_webfetch(tool_input)
    if tool_name == "WebSearch":
        return Decision("deny", WEBSEARCH_REASON)
    if tool_name in BASH_TOOLS:
        return _decide_bash(tool_input)
    if tool_name.startswith(BROWSER_TOOL_PREFIX):
        return _decide_browser(tool_name, tool_input)
    return ALLOW


def _parse_payload(raw: str) -> object:
    """The JSON value on stdin, or None if raw is empty or not valid JSON."""
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def decide_from_raw(raw: str) -> Decision:
    """Validate the hook's raw stdin into a tool_name/tool_input pair and
    call decide(). Any malformed shape denies with
    MALFORMED_INPUT_REASON, before decide() ever sees it."""
    payload = _parse_payload(raw)
    if not isinstance(payload, dict):
        return Decision("deny", MALFORMED_INPUT_REASON)
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str):
        return Decision("deny", MALFORMED_INPUT_REASON)
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return Decision("deny", MALFORMED_INPUT_REASON)
    return decide(tool_name, tool_input)


def _hook_output(decision: Decision) -> str | None:
    """The line to print for decision, or None to print nothing (allow)."""
    if decision.action == "allow":
        return None
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision.action,
            "permissionDecisionReason": decision.reason,
        }
    })


def main() -> None:
    try:
        raw = sys.stdin.read()
        decision = decide_from_raw(raw)
    except Exception as error:  # never a traceback: deny, naming the error
        decision = Decision("deny", f"guard failed: {error}")
    output = _hook_output(decision)
    if output is not None:
        print(output)


if __name__ == "__main__":
    main()
