#!/usr/bin/env python3
"""
browser_use_api.py - the browser-use Cloud v4 calls that cloud_read.py needs.

This module is the only code that reads the key (settings/browser-use.key in
the appraiser's folder). The key goes into one request header and nowhere
else: never into a URL, a log line, an exception message or a file.

Two transports sit behind one client:
  * HTTPS to api.browser-use.com (the real one);
  * replay, when APPRAISAL_BROWSER_USE_FIXTURES names a folder holding
    replies.json (the tests). No key and no network are used in replay.

Not a command: cloud_read.py imports it.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Union

API_BASE = "https://api.browser-use.com/api/v4"
BILLING_URL = "https://api.browser-use.com/api/v2/billing/account"
BILLING_PATH = "/billing/account"
FIXTURES_ENV = "APPRAISAL_BROWSER_USE_FIXTURES"   # a folder; replaces HTTPS (tests)
DEBUG_LOG_ENV = "APPRAISAL_BROWSER_USE_DEBUG_LOG"  # an absolute file path; live check L5
KEY_RELATIVE_PATH = Path("settings") / "browser-use.key"
KEY_HEADER = "X-Browser-Use-API-Key"
HTTP_TIMEOUT_S = 30
DEFAULT_RETRY_AFTER_S = 5.0
KEEP_ALIVE_S = 120
PROXY_COUNTRY = "us"

ApiErrorKind = Literal["no_key", "budget", "rate_limited", "http", "network"]


class ApiError(Exception):
    """A failed call. detail is short and never holds the key or a request body."""

    def __init__(self, kind: ApiErrorKind, status: int, detail: str) -> None:
        self.kind = kind
        self.status = status
        self.detail = detail
        super().__init__(f"{kind} {status} {detail}".strip())


@dataclass
class RunCreated:
    run_id: str
    session_id: str
    workspace_id: str


@dataclass
class RunResult:
    status: str
    output: object
    error: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    session_id: str
    workspace_id: str


@dataclass
class CloudBrowser:
    browser_id: str
    cdp_url: str      # "" when the API gives null


@dataclass
class Account:
    credit_usd: Decimal
    concurrent_limit: int


@dataclass
class Timing:
    poll_seconds: float = 3.0
    run_timeout_s: float = 300
    retry_after_cap_s: float = 60
    capture_timeout_s: float = 30


@dataclass
class RawReply:
    status: int
    headers: dict[str, str]
    body: object


def read_key(workspace: Path) -> str:
    """The key from settings/browser-use.key: the first non-blank line, stripped.
    Raises ApiError("no_key") when it is missing, empty or not a single printable word."""
    key_path = workspace / KEY_RELATIVE_PATH
    try:
        text = key_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise ApiError("no_key", 0, "no readable key file") from None
    key = ""
    for line in text.splitlines():
        if line.strip():
            key = line.strip()
            break
    if key == "":
        raise ApiError("no_key", 0, "the key file is empty")
    if not key.isprintable() or any(character.isspace() for character in key):
        raise ApiError("no_key", 0, "the key file does not hold one key")
    return key


def _budget_detail(body: object) -> str:
    """'cap $25, spent $25.4' when a 402 body carries both numbers, else ''."""
    if not isinstance(body, dict):
        return ""
    detail = body.get("detail")
    if not isinstance(detail, dict):
        return ""
    cap = detail.get("cap")
    spent = detail.get("spent")
    if not _is_number(cap) or not _is_number(spent):
        return ""
    return f"cap ${_plain_number(cap)}, spent ${_plain_number(spent)}"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _plain_number(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class HttpsTransport:
    """Real HTTPS to browser-use. Appends one JSON line per request to the
    debug log when DEBUG_LOG_ENV is set. Headers are never logged."""

    def __init__(self, key: str) -> None:
        self._key = key
        self._tls_context = ssl.create_default_context()

    def send(self, method: str, path: str, body: object) -> RawReply:
        if path == BILLING_PATH:
            url = BILLING_URL
        else:
            url = API_BASE + path
        self._write_debug_line(method, path, body)
        headers = {KEY_HEADER: self._key}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S, context=self._tls_context) as response:
                return RawReply(response.status, dict(response.headers.items()), _parse_body(response.read()))
        except urllib.error.HTTPError as error:
            reply_headers = dict(error.headers.items()) if error.headers is not None else {}
            return RawReply(error.code, reply_headers, _parse_body(error.read()))
        except (urllib.error.URLError, OSError, ValueError):
            raise ApiError("network", 0, f"could not reach browser-use for {method} {path.split('?')[0]}") from None

    def _write_debug_line(self, method: str, path: str, body: object) -> None:
        log_path = os.environ.get(DEBUG_LOG_ENV, "")
        if log_path == "":
            return
        line = json.dumps({"method": method, "path": path, "body": body})
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")


def _parse_body(raw: bytes) -> object:
    if not raw.strip():
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


class ReplayTransport:
    """Answers from <folder>/replies.json and records every request in <folder>/sent.jsonl."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self.timing = Timing()
        self._replies: dict[str, list[object]] = {}
        try:
            document = json.loads((folder / "replies.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ApiError("http", 599, "no readable replies.json in the fixtures folder") from None
        if not isinstance(document, dict):
            raise ApiError("http", 599, "replies.json is not an object")
        timing = document.get("timing")
        if isinstance(timing, dict):
            self.timing = _timing_from(timing)
        replies = document.get("replies")
        if isinstance(replies, dict):
            for key, entries in replies.items():
                if isinstance(entries, list):
                    self._replies[key] = entries

    def send(self, method: str, path: str, body: object) -> RawReply:
        key = f"{method} {path}"
        with self._lock:
            with open(self._folder / "sent.jsonl", "a", encoding="utf-8") as sent_file:
                sent_file.write(json.dumps({"method": method, "path": path, "body": body}) + "\n")
            entries = self._replies.get(key)
            if not entries:
                raise ApiError("http", 599, f"no fixture for {key}")
            position = self._counters.get(key, 0)
            self._counters[key] = position + 1
            entry = entries[min(position, len(entries) - 1)]
        if not isinstance(entry, dict):
            raise ApiError("http", 599, f"bad fixture entry for {key}")
        if entry.get("raise") == "network":
            raise ApiError("network", 0, f"could not reach browser-use for {method} {path.split('?')[0]}")
        status = entry.get("status", 200)
        headers = entry.get("headers", {})
        reply_headers: dict[str, str] = {}
        if isinstance(headers, dict):
            for name, value in headers.items():
                reply_headers[str(name)] = str(value)
        if not isinstance(status, int):
            raise ApiError("http", 599, f"bad fixture status for {key}")
        return RawReply(status, reply_headers, entry.get("body"))


def _timing_from(values: dict[str, object]) -> Timing:
    timing = Timing()
    poll_seconds = values.get("poll_seconds")
    run_timeout_s = values.get("run_timeout_s")
    retry_after_cap_s = values.get("retry_after_cap_s")
    capture_timeout_s = values.get("capture_timeout_s")
    if _is_number(poll_seconds):
        timing.poll_seconds = float(poll_seconds)
    if _is_number(run_timeout_s):
        timing.run_timeout_s = float(run_timeout_s)
    if _is_number(retry_after_cap_s):
        timing.retry_after_cap_s = float(retry_after_cap_s)
    if _is_number(capture_timeout_s):
        timing.capture_timeout_s = float(capture_timeout_s)
    return timing


Transport = Union[HttpsTransport, ReplayTransport]


class BrowserUseClient:
    """The calls cloud_read.py makes. Safe to share between threads."""

    def __init__(self, transport: Transport, timing: Timing, replaying: bool) -> None:
        self._transport = transport
        self.timing = timing
        self.replaying = replaying

    # -- one place where every status is mapped ---------------------------------

    def _request(self, method: str, path: str, body: object = None) -> object:
        slept_seconds = 0.0
        while True:
            reply = self._transport.send(method, path, body)
            if reply.status == 429:
                wait_seconds = _retry_after_seconds(reply.headers)
                if slept_seconds >= self.timing.retry_after_cap_s:
                    raise ApiError("rate_limited", 429, "browser-use asked us to slow down")
                time.sleep(wait_seconds)
                slept_seconds += wait_seconds
                continue
            if reply.status == 402:
                raise ApiError("budget", 402, _budget_detail(reply.body))
            if reply.status == 403:
                raise ApiError("no_key", 403, "browser-use refused the key")
            if reply.status >= 400:
                raise ApiError("http", reply.status, f"{method} {path.split('?')[0]} failed")
            return reply.body

    def _unexpected(self, method: str, path: str) -> ApiError:
        return ApiError("http", 200, f"unexpected reply from {method} {path.split('?')[0]}")

    # -- the calls -----------------------------------------------------------------

    def create_run(self, task: str, output_schema: dict[str, object], model: str, max_cost_usd: str) -> RunCreated:
        path = "/runs"
        request_body = {"task": task, "outputSchema": output_schema, "model": model,
                        "browserSettings": {"proxyCountryCode": PROXY_COUNTRY},
                        "maxCostUsd": max_cost_usd, "keepAlive": KEEP_ALIVE_S}
        reply = self._request("POST", path, request_body)
        if not isinstance(reply, dict):
            raise self._unexpected("POST", path)
        run_id = reply.get("id")
        session_id = reply.get("sessionId")
        workspace_id = reply.get("workspaceId")
        if not isinstance(run_id, str) or not isinstance(session_id, str) or not isinstance(workspace_id, str):
            raise self._unexpected("POST", path)
        return RunCreated(run_id, session_id, workspace_id)

    def run_status(self, run_id: str) -> str:
        path = f"/runs/{urllib.parse.quote(run_id, safe='')}/status"
        reply = self._request("GET", path)
        if not isinstance(reply, dict) or not isinstance(reply.get("status"), str):
            raise self._unexpected("GET", path)
        return reply["status"]

    def cancel_run(self, run_id: str) -> None:
        self._request("POST", f"/runs/{urllib.parse.quote(run_id, safe='')}/cancel")

    def get_run(self, run_id: str) -> RunResult:
        path = f"/runs/{urllib.parse.quote(run_id, safe='')}"
        reply = self._request("GET", path)
        if not isinstance(reply, dict):
            raise self._unexpected("GET", path)
        status = reply.get("status")
        error = reply.get("error")
        input_tokens = reply.get("totalInputTokens")
        output_tokens = reply.get("totalOutputTokens")
        session_id = reply.get("sessionId")
        workspace_id = reply.get("workspaceId")
        if not isinstance(status, str):
            raise self._unexpected("GET", path)
        if error is not None and not isinstance(error, str):
            raise self._unexpected("GET", path)
        if not _is_whole_number(input_tokens) or not _is_whole_number(output_tokens):
            raise self._unexpected("GET", path)
        if not isinstance(session_id, str):
            raise self._unexpected("GET", path)
        if workspace_id is not None and not isinstance(workspace_id, str):
            raise self._unexpected("GET", path)
        try:
            cost = Decimal(str(reply.get("totalCostUsd")))
        except InvalidOperation:
            raise self._unexpected("GET", path) from None
        return RunResult(status=status, output=reply.get("output"), error=error or "",
                         input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost,
                         session_id=session_id, workspace_id=workspace_id or "")

    def active_browsers(self, session_id: str) -> list[CloudBrowser]:
        path = f"/browsers?agentSessionId={urllib.parse.quote(session_id, safe='')}&filterBy=active"
        reply = self._request("GET", path)
        if not isinstance(reply, dict) or not isinstance(reply.get("items"), list):
            raise self._unexpected("GET", path)
        browsers = []
        for item in reply["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise self._unexpected("GET", path)
            cdp_url = item.get("cdpUrl")
            if cdp_url is not None and not isinstance(cdp_url, str):
                raise self._unexpected("GET", path)
            browsers.append(CloudBrowser(item["id"], cdp_url or ""))
        return browsers

    def stop_browser(self, browser_id: str) -> None:
        self._request("PATCH", f"/browsers/{urllib.parse.quote(browser_id, safe='')}", {"action": "stop"})

    def delete_session(self, session_id: str) -> None:
        self._request("DELETE", f"/sessions/{urllib.parse.quote(session_id, safe='')}")

    def delete_workspace(self, workspace_id: str) -> None:
        self._request("DELETE", f"/workspaces/{urllib.parse.quote(workspace_id, safe='')}")

    def account(self) -> Account:
        reply = self._request("GET", BILLING_PATH)
        if not isinstance(reply, dict):
            raise self._unexpected("GET", BILLING_PATH)
        credit = reply.get("totalCreditsBalanceUsd")
        limit = reply.get("concurrentSessionLimit")
        if not _is_number(credit) or not _is_whole_number(limit):
            raise self._unexpected("GET", BILLING_PATH)
        return Account(Decimal(str(credit)), limit)


def _is_whole_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _retry_after_seconds(headers: dict[str, str]) -> float:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                return max(0.0, float(value))
            except ValueError:
                return DEFAULT_RETRY_AFTER_S
    return DEFAULT_RETRY_AFTER_S


def make_client(workspace: Path) -> BrowserUseClient:
    """A client for this workspace. Replay when FIXTURES_ENV is set (no key needed),
    else HTTPS with the key from settings/browser-use.key (ApiError "no_key" without it)."""
    fixtures_folder = os.environ.get(FIXTURES_ENV, "")
    if fixtures_folder != "":
        replay = ReplayTransport(Path(fixtures_folder))
        return BrowserUseClient(replay, replay.timing, replaying=True)
    key = read_key(workspace)
    return BrowserUseClient(HttpsTransport(key), Timing(), replaying=False)


if __name__ == "__main__":
    print("browser_use_api.py is a library; run cloud_read.py instead.", file=sys.stderr)
    raise SystemExit(2)
