#!/usr/bin/env python3
"""
guard.py - the plugin's PreToolUse hook. Allows, denies or asks on every
tool call matched by hooks.json: Edit, Write, MultiEdit, NotebookEdit,
Bash (both the desktop app's own Bash tool and the sandbox's
mcp__workspace__bash), and the browser's MCP tools (Browser Harness's).

There are no network limits (0.10.0): web search, fetch, Bash and the
browser may reach any site. What it checks is protected files (the
calibration answer key, the plugin's own files) and the browser's
read tier: only the read-tier tools; browser_js runs only the plugin's
read.js and elements.js, matched by hash (browser_scripts.py); and a click
by coordinates is checked against the element list that post_tool.py saved
after the last elements.js call, so a click on something named like "Send"
asks and a click with no fresh list is denied.

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
import os
import posixpath
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


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

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from browser_scripts import script_name  # noqa: E402
except Exception as _import_error:  # hooks/browser_scripts.py missing or broken: fail closed
    _deny_and_exit(f"guard failed to load browser_scripts.py: {_import_error}")

MALFORMED_INPUT_REASON = "guard could not read the tool call"
REPORT_INSTEAD = "report the failing check instead."

# Segments that mark a path as belonging to a plugin install, wherever it is
# mounted. The last one carries no leading slash on purpose: it names a
# folder that can sit at different depths. Compared case-folded below,
# because the real filesystem (macOS/APFS) may be case-insensitive.
PLUGIN_PATH_SEGMENTS = ("/.claude/plugins/", "/.local-plugins/", "claude-hostloop-plugins/")

CALIBRATION_FILENAME = "calibration.csv"
# The browser-use API key: only cloud_read.py (through browser_use_api.py) reads it.
# The Read tool is not matched by the hooks yet, so a Read of it is not denied.
KEY_FILENAME = "browser-use.key"
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
BASH_TOOLS = ("Bash", "mcp__workspace__bash")
# The app names plugin MCP tools mcp__plugin_<plugin>_<server>__<tool>; this
# plugin's browser MCP server is named "browser", and its tools (Browser
# Harness's) are themselves named browser_new_tab, browser_js, and so on.
BROWSER_SERVER_PREFIX = "mcp__plugin_appraisal-comps_browser__"
BROWSER_TOOL_PREFIX = BROWSER_SERVER_PREFIX + "browser_"

# Every tool the pinned Browser Harness server lists (the fixture
# tests/fixtures/browser/harness-tools.json is its record). A test keeps this
# in step with the fixture, so a Harness upgrade that adds a tool is noticed.
HARNESS_TOOLS = ("new_tab", "goto", "page_info", "click", "type", "fill", "press", "scroll", "screenshot",
                 "list_tabs", "current_tab", "switch_tab", "close_tab", "ensure_real_tab", "wait",
                 "wait_for_load", "wait_for_element", "js", "cdp", "upload_file", "http_get",
                 "solve_captcha", "start_recording", "stop_recording", "feedback", "report_run",
                 "mark_tab", "name_session", "release_tabs")

# Read tier only (Design Principles, "Trust"): the Harness tools that only
# look at or move around a page, in a tab of its own. Everything else (raw
# CDP, fetches outside the browser, uploads, the CAPTCHA solver, recordings,
# vendor feedback, other tabs) and any tool a later Harness adds is denied
# by omission.
BROWSER_ALLOWED_TOOLS = ("new_tab", "goto", "page_info", "click", "type", "fill", "press", "scroll",
                         "screenshot", "current_tab", "close_tab", "ensure_real_tab", "wait",
                         "wait_for_load", "wait_for_element", "js")

# What to do instead, for a denied tool. A tool with no hint gets the first
# clause of the reason only.
DENIED_TOOL_HINTS = {
    "cdp": "read with read.js or elements.js through browser_js",
    "http_get": "open the page with browser_goto, or use cloud_read.py for a public site",
    "list_tabs": "work in the tab browser_new_tab opened; browser_current_tab names it",
    "switch_tab": "work in the tab browser_new_tab opened; browser_current_tab names it",
    "solve_captcha": "ask him to clear the check in Chrome",
    "upload_file": "nothing is uploaded",
    "start_recording": "use browser_screenshot",
    "stop_recording": "use browser_screenshot",
    "feedback": "nothing is sent to the vendor",
    "report_run": "nothing is sent to the vendor",
}

# The names of the Harness tool inputs the guard reads. A test checks each
# against the fixture.
JS_EXPRESSION_KEY = "expression"
URL_KEY = "url"
CLICK_X_KEY = "x"
CLICK_Y_KEY = "y"
FILL_SELECTOR_KEY = "selector"

# A click or fill on an element named with one of these, as a whole word, looks
# like it changes something on the site rather than just reading it: ask
# instead of allowing. This is a speed bump, not a limit - the real limit is
# the tools left off BROWSER_ALLOWED_TOOLS.
BROWSER_ASK_WORDS = ("submit", "send", "deliver", "accept", "decline", "sign", "upload",
                     "pay", "delete", "complete", "assign", "finalize", "email", "share")

BROWSER_ASK_WORD_RE = re.compile(r"\b(?:" + "|".join(BROWSER_ASK_WORDS) + r")\b", re.IGNORECASE)

# The element list post_tool.py saved (browser-elements.json) is fresh for this long.
ELEMENT_LIST_MAX_AGE_S = 120
ELEMENTS_FILE = "browser-elements.json"

Action = Literal["allow", "deny", "ask"]

# What a click with no fresh element list gets. "ask" only if the maintainer
# ships without PostToolUse (spec H5): then no list is ever saved.
CLICK_WITHOUT_LIST: Action = "deny"


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


def _is_browser_use_key(path_str: str) -> bool:
    """True when path_str's basename casefolds to browser-use.key, in any folder."""
    parts = _lexical_parts(path_str)
    return bool(parts) and parts[-1].casefold() == KEY_FILENAME.casefold()


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
    if _is_browser_use_key(value):
        return Decision("deny", f"{value} is the browser-use key; only cloud_read.py reads it. "
                                "Run cloud_read.py check-key to test it.")
    if _is_plugin_path(value):
        return Decision("deny", f"{value} is inside the plugin's own files; {REPORT_INSTEAD}")
    return ALLOW


def _decide_bash(tool_input: dict[str, object]) -> Decision:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return Decision("deny", MALFORMED_INPUT_REASON)
    if CALIBRATION_FILENAME in command.casefold():
        return Decision("deny", f"command names {CALIBRATION_FILENAME}; {REPORT_INSTEAD}")
    if KEY_FILENAME in command.casefold():
        return Decision("deny", f"command names {KEY_FILENAME}; run cloud_read.py check-key instead.")
    return ALLOW


BROWSER_ASK_DECISION = Decision("ask", "this looks like it changes something on this site; the appraiser decides")


@dataclass
class ListedElement:
    name: str
    selector: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class BrowserState:
    elements: list[ListedElement] | None  # None: no fresh element list


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _listed_element(item: object) -> ListedElement | None:
    """One element of the saved list, or None when it isn't the shape post_tool.py writes."""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    selector = item.get("selector")
    box = [item.get(key) for key in ("x", "y", "w", "h")]
    if not isinstance(name, str) or not isinstance(selector, str):
        return None
    if not all(_is_number(value) for value in box):
        return None
    return ListedElement(name, selector, box[0], box[1], box[2], box[3])


def _read_elements(path: Path, now: float) -> list[ListedElement] | None:
    """The fresh element list in path, or None: missing, unreadable, malformed, from the future or too old."""
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    saved_at = saved.get("saved_at")
    items = saved.get("items")
    if not _is_number(saved_at) or not isinstance(items, list):
        return None
    age_s = now - saved_at
    if age_s < 0 or age_s > ELEMENT_LIST_MAX_AGE_S:
        return None
    elements: list[ListedElement] = []
    for item in items:
        element = _listed_element(item)
        if element is None:
            return None
        elements.append(element)
    return elements


def load_browser_state(plugin_data: str | None, now: float) -> BrowserState:
    """The element list post_tool.py saved, if there is a fresh one. Never raises."""
    if not plugin_data:
        return BrowserState(None)
    try:
        return BrowserState(_read_elements(Path(plugin_data) / ELEMENTS_FILE, now))
    except Exception:
        return BrowserState(None)


def _is_web_address(value: object) -> bool:
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def _decide_browser_address(browser_tool: str, tool_input: dict[str, object]) -> Decision:
    """goto and new_tab open http and https addresses only (new_tab may open a blank tab)."""
    only_web = Decision("deny", f"browser_{browser_tool} opens http and https addresses only")
    if browser_tool == "new_tab" and URL_KEY not in tool_input:
        return ALLOW
    address = tool_input.get(URL_KEY)
    if browser_tool == "new_tab" and address == "about:blank":
        return ALLOW
    if _is_web_address(address):
        return ALLOW
    return only_web


def _names_an_action(element: ListedElement) -> bool:
    return BROWSER_ASK_WORD_RE.search(element.name) is not None


def _decide_browser_click(tool_input: dict[str, object], browser_state: BrowserState) -> Decision:
    x = tool_input.get(CLICK_X_KEY)
    y = tool_input.get(CLICK_Y_KEY)
    if not _is_number(x) or not _is_number(y) or x < 0 or y < 0:
        return Decision("deny", MALFORMED_INPUT_REASON)
    if browser_state.elements is None:
        return Decision(CLICK_WITHOUT_LIST,
                        "run elements.js first (browser_js with the plugin's elements.js), then click")
    for element in browser_state.elements:
        inside_horizontally = element.x <= x <= element.x + element.w
        inside_vertically = element.y <= y <= element.y + element.h
        if inside_horizontally and inside_vertically and _names_an_action(element):
            return BROWSER_ASK_DECISION
    return ALLOW


def _decide_browser_fill(tool_input: dict[str, object], browser_state: BrowserState) -> Decision:
    if browser_state.elements is None:
        return ALLOW
    selector = tool_input.get(FILL_SELECTOR_KEY)
    for element in browser_state.elements:
        if element.selector == selector and _names_an_action(element):
            return BROWSER_ASK_DECISION
    return ALLOW


def _decide_browser(tool_name: str, tool_input: dict[str, object], browser_state: BrowserState) -> Decision:
    browser_tool = tool_name[len(BROWSER_TOOL_PREFIX):]
    if browser_tool not in BROWSER_ALLOWED_TOOLS:
        reason = f"browser_{browser_tool} is not a read-tier tool"
        hint = DENIED_TOOL_HINTS.get(browser_tool)
        if hint:
            reason += f"; {hint}"
        return Decision("deny", reason)
    if browser_tool == "js":
        if script_name(tool_input.get(JS_EXPRESSION_KEY)) is None:
            return Decision("deny", "page scripts run only from the plugin's scripts: read.js to read a page, "
                                    "elements.js to list what can be clicked. Send the file's text unchanged.")
        return ALLOW
    if browser_tool in ("goto", "new_tab"):
        return _decide_browser_address(browser_tool, tool_input)
    if browser_tool == "click":
        return _decide_browser_click(tool_input, browser_state)
    if browser_tool == "fill":
        return _decide_browser_fill(tool_input, browser_state)
    return ALLOW


def decide(tool_name: str, tool_input: dict[str, object], browser_state: BrowserState) -> Decision:
    """Pure: no I/O, no filesystem writes."""
    if tool_name in EDIT_TOOLS:
        return _decide_edit(tool_name, tool_input)
    if tool_name in BASH_TOOLS:
        return _decide_bash(tool_input)
    if tool_name.startswith(BROWSER_TOOL_PREFIX):
        return _decide_browser(tool_name, tool_input, browser_state)
    if tool_name.startswith(BROWSER_SERVER_PREFIX):
        return Decision("deny", f"{tool_name} is not a read-tier browser tool")
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
    browser_state = load_browser_state(os.environ.get("CLAUDE_PLUGIN_DATA"), time.time())
    return decide(tool_name, tool_input, browser_state)


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
