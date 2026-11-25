#!/usr/bin/env python3
"""
post_tool.py - the plugin's PostToolUse hook for the browser's MCP tools.

After each call to the browser it keeps two small state files in the folder
$CLAUDE_PLUGIN_DATA names, for guard.py to read:

- browser-elements.json: the last element list (what elements.js returned):
  the page's origin and address, when it was saved, and each element's name,
  selector and box. Never an element's value. The guard uses it to decide a
  click by coordinates, since a coordinate has no name of its own.
- browser-page.json: the origin and address of the page the browser is on,
  from read.js, elements.js or page_info. No page text.

Any call that changes the page or moves it under the viewport coordinates
(goto, click, type, scroll, ...) deletes the element list, so the pattern is
elements.js, then one click.

It fails quiet: any error writes nothing, prints nothing, and exits 0.

    python3 -I post_tool.py        # reads the hook's JSON payload on stdin
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_scripts import script_name  # noqa: E402

# Must equal guard.BROWSER_TOOL_PREFIX (a test checks it).
BROWSER_TOOL_PREFIX = "mcp__plugin_appraisal-comps_browser__browser_"
ELEMENTS_FILE = "browser-elements.json"
PAGE_FILE = "browser-page.json"
ACTING_TOOLS = ("goto", "new_tab", "click", "type", "fill", "press", "scroll", "close_tab", "ensure_real_tab")
JS_EXPRESSION_KEY = "expression"
MAX_ITEMS = 200
MAX_NAME_LENGTH = 200
REPLY_KEYS = ("url", "items", "text", "error")


def parse_json_object(text: object) -> dict[str, object] | None:
    if not isinstance(text, str):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def object_from_blocks(blocks: object) -> dict[str, object] | None:
    """The first text block holding a JSON object."""
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parsed = parse_json_object(block.get("text"))
            if parsed is not None:
                return parsed
    return None


def reply_object(tool_response: object) -> dict[str, object] | None:
    """The tool's JSON object, whichever shape the app hands it over in: the
    object itself, a JSON string, a list of text blocks, or an object wrapping
    them in "content". None when there is none, or when it is an error."""
    reply: dict[str, object] | None = None
    if isinstance(tool_response, dict):
        if any(key in tool_response for key in REPLY_KEYS):
            reply = tool_response
        else:
            reply = object_from_blocks(tool_response.get("content"))
    elif isinstance(tool_response, str):
        reply = parse_json_object(tool_response)
    elif isinstance(tool_response, list):
        reply = object_from_blocks(tool_response)
    if reply is None or "error" in reply:
        return None
    return reply


def origin_of(url: str) -> str:
    """"https://host[:port]" for an http or https address; "" otherwise."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def narrow_item(item: object, number: int) -> dict[str, object] | None:
    """The fields of one listed element worth keeping, or None when it is malformed."""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    selector = item.get("selector")
    if not isinstance(name, str) or not isinstance(selector, str):
        return None
    box_values = [item.get(key) for key in ("x", "y", "w", "h")]
    if not all(is_number(value) for value in box_values):
        return None
    kind = item.get("kind")
    if not isinstance(kind, str):
        kind = ""
    return {
        "n": number, "kind": kind, "name": name[:MAX_NAME_LENGTH], "selector": selector,
        "x": box_values[0], "y": box_values[1], "w": box_values[2], "h": box_values[3],
    }


def narrow_items(items: list[object]) -> list[dict[str, object]]:
    kept: list[dict[str, object]] = []
    for item in items[:MAX_ITEMS]:
        narrowed = narrow_item(item, len(kept) + 1)
        if narrowed is not None:
            kept.append(narrowed)
    return kept


def write_state(plugin_data: Path, file_name: str, state: dict[str, object]) -> None:
    """Write state as JSON to a temp file in plugin_data, then rename it into place."""
    handle, temp_name = tempfile.mkstemp(dir=str(plugin_data), prefix=file_name + ".")
    try:
        with os.fdopen(handle, "w") as temp_file:
            json.dump(state, temp_file)
        os.replace(temp_name, plugin_data / file_name)
    except OSError:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def delete_state(plugin_data: Path, file_name: str) -> None:
    try:
        (plugin_data / file_name).unlink()
    except FileNotFoundError:
        pass


def origin_of_reply(reply: dict[str, object], url: str) -> str:
    origin = reply.get("origin")
    if isinstance(origin, str):
        return origin
    return origin_of(url)


def save_page(plugin_data: Path, reply: dict[str, object], now: float) -> None:
    url = reply.get("url")
    if not isinstance(url, str):
        return
    write_state(plugin_data, PAGE_FILE, {"origin": origin_of_reply(reply, url), "url": url, "saved_at": now})


def save_elements(plugin_data: Path, reply: dict[str, object] | None, now: float) -> None:
    if reply is None:
        delete_state(plugin_data, ELEMENTS_FILE)
        return
    url = reply.get("url")
    items = reply.get("items")
    if not isinstance(url, str) or not isinstance(items, list):
        delete_state(plugin_data, ELEMENTS_FILE)
        return
    origin = origin_of_reply(reply, url)
    write_state(plugin_data, ELEMENTS_FILE, {
        "origin": origin, "url": url, "saved_at": now, "items": narrow_items(items)})
    write_state(plugin_data, PAGE_FILE, {"origin": origin, "url": url, "saved_at": now})


def handle(payload: object, plugin_data: Path, now: float) -> None:
    if not isinstance(payload, dict):
        return
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.startswith(BROWSER_TOOL_PREFIX):
        return
    browser_tool = tool_name[len(BROWSER_TOOL_PREFIX):]
    if browser_tool in ACTING_TOOLS:
        delete_state(plugin_data, ELEMENTS_FILE)
        return
    reply = reply_object(payload.get("tool_response"))
    if browser_tool == "page_info":
        if reply is not None:
            save_page(plugin_data, reply, now)
        return
    if browser_tool != "js":
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    ran = script_name(tool_input.get(JS_EXPRESSION_KEY))
    if ran == "elements.js":
        save_elements(plugin_data, reply, now)
    elif ran == "read.js" and reply is not None:
        save_page(plugin_data, reply, now)


def main() -> None:
    try:
        data_folder = os.environ.get("CLAUDE_PLUGIN_DATA")
        if not data_folder or not Path(data_folder).is_dir():
            return
        payload = json.loads(sys.stdin.read())
        handle(payload, Path(data_folder), time.time())
    except Exception:  # fail quiet: no traceback, no output, exit 0
        return


if __name__ == "__main__":
    main()
