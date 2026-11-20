#!/usr/bin/env python3
"""
update_check.py - the SessionStart hook's once-a-day update check.

Fetches latest.json from GitHub and tells Claude whether an update is ready,
without installing anything: on the desktop app, only the appraiser can
install an update, through the Plugins screen (see INSTALL_HOW below).
Claude never contacts GitHub on his own; this module is only ever called by
hooks/session_start.py.

In tests, the APPRAISAL_COMPS_TEST_LATEST environment variable names a local
file whose bytes stand in for latest.json, so no test ever touches the
network.

This module fetches latest.json (ReleaseSource, fetch_latest, reading only
the "version" field), explains a fetch failure (failure_reason), and checks
whether any assignment is still open (open_jobs).
"""
from __future__ import annotations

import csv
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

TEST_LATEST_ENV = "APPRAISAL_COMPS_TEST_LATEST"

DOWNLOAD_HOST = "release-assets.githubusercontent.com"  # Confirmed 2026-11-17: a single redirect from github.com lands here.

INSTALL_HOW = (
    "In Claude, open Customize, then Plugins, then Discover, then Personal. "
    "Click the ... next to appraisal-comps, then Check for updates. Then open "
    "Settings, then Plugins, then Appraisal Comps, and click Update. Start a "
    "new chat when it's done."
)


class UpdateCheckError(Exception):
    """Raised when latest.json can't be fetched or parsed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse a strict X.Y.Z version string into a tuple that compares as numbers."""
    match = VERSION_PATTERN.match(text)
    if match is None:
        raise UpdateCheckError("BAD_VERSION", f"Not a valid version: {text!r}. Expected X.Y.Z, e.g. 0.5.0.")
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch))


@dataclass(frozen=True)
class ReleaseSource:
    """Where the update check gets latest.json from, and which hosts and
    schemes are allowed to serve it."""

    base_url: str
    allowed_hosts: tuple[str, ...]
    allowed_schemes: tuple[str, ...] = ("https",)


DEFAULT_SOURCE = ReleaseSource(
    base_url="https://github.com/JonathanShitrit/appraisal-comps-releases/releases/latest/download/",
    allowed_hosts=("github.com", DOWNLOAD_HOST),
)


@dataclass
class LatestRelease:
    """The version parsed out of latest.json."""

    version: str


def _ensure_allowed(url: str, source: ReleaseSource) -> None:
    """Raise UpdateCheckError("BAD_HOST", ...) unless url's scheme and host
    are both on source's allowed list. Called for the initial request and
    every redirect, so a compromised or misconfigured server can't send
    fetch_bytes anywhere else."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in source.allowed_schemes or parsed.hostname not in source.allowed_hosts:
        raise UpdateCheckError(
            "BAD_HOST",
            f"Refused to contact {parsed.hostname} over {parsed.scheme}: not on the allowed list.",
        )


def fetch_bytes(url: str, source: ReleaseSource) -> bytes:
    """Download url's bytes, refusing any scheme or host -- for the initial
    request or a redirect -- that source doesn't allow. timeout=10 seconds,
    so a slow network never stalls a session start."""
    _ensure_allowed(url, source)

    class AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            req: urllib.request.Request,
            fp: object,
            code: int,
            msg: str,
            headers: object,
            newurl: str,
        ) -> urllib.request.Request | None:
            _ensure_allowed(newurl, source)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(AllowlistRedirectHandler)
    try:
        with opener.open(url, timeout=10) as response:
            return response.read()
    except UpdateCheckError:
        raise
    except urllib.error.URLError as error:
        # If urllib wraps the UpdateCheckError raised above in redirect_request
        # inside a URLError, unwrap it so callers see the UpdateCheckError itself.
        if isinstance(error.reason, UpdateCheckError):
            raise error.reason from error
        raise


def _latest_json_bytes(source: ReleaseSource) -> bytes:
    """latest.json's bytes. In tests, APPRAISAL_COMPS_TEST_LATEST names a
    local file to read instead, so the network is never touched. A missing
    test file raises its own OSError, exactly as a real fetch failure would."""
    test_path = os.environ.get(TEST_LATEST_ENV)
    if test_path is not None:
        return Path(test_path).read_bytes()
    return fetch_bytes(source.base_url + "latest.json", source)


def fetch_latest(source: ReleaseSource) -> LatestRelease:
    """Download and parse latest.json, reading only its "version" field.
    Bad JSON, a non-object, or a missing or non-string version raises
    UpdateCheckError("BAD_LATEST_JSON", ...); the version string is
    validated with parse_version."""
    raw_bytes = _latest_json_bytes(source)
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as error:
        raise UpdateCheckError("BAD_LATEST_JSON", f"latest.json is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise UpdateCheckError("BAD_LATEST_JSON", "latest.json is not a JSON object.")
    version = data.get("version")
    if not isinstance(version, str):
        raise UpdateCheckError("BAD_LATEST_JSON", "latest.json is missing a string 'version' field.")
    parse_version(version)
    return LatestRelease(version=version)


def failure_reason(error: Exception) -> tuple[str, str]:
    """Map an exception from fetch_latest to the (reason, fix) pair for one
    CHECK_FAILED line.

    Order matters: HTTPError is also a URLError, and
    ssl.SSLCertVerificationError can arrive bare or wrapped inside a
    URLError's .reason, so both are checked before the general
    URLError/offline case. A plain OSError (for example, the test-latest
    file named by APPRAISAL_COMPS_TEST_LATEST is missing) is treated the
    same as offline.
    """
    tls_fix = (
        'Mac: run "Install Certificates.command" in the Python folder; '
        "work network: ask IT about HTTPS inspection"
    )
    offline_fix = "Check the internet connection; work carries on"

    if isinstance(error, urllib.error.HTTPError):
        return f"http_{error.code}", "Try again later; tell the maintainer if it persists"
    if isinstance(error, ssl.SSLCertVerificationError):
        return "tls", tls_fix
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, ssl.SSLCertVerificationError):
        return "tls", tls_fix
    if isinstance(error, urllib.error.URLError):
        return "offline", offline_fix
    if isinstance(error, (socket.timeout, TimeoutError, ConnectionError, OSError)):
        return "offline", offline_fix
    if isinstance(error, UpdateCheckError):
        return error.code.lower(), error.message
    return "other", "Tell the maintainer"


@dataclass
class OpenJob:
    """One assignment folder whose events.csv shows its latest stage is
    still mid-stage (started or waiting on the appraiser)."""

    name: str
    stage: str


def _job_status(events_path: Path) -> OpenJob | None:
    """Return the OpenJob for one assignment's events.csv, or None if that
    job is closed.

    A missing file, or one with only a header row, is closed. A file that
    can't be parsed -- unreadable, bad CSV, bad encoding, or missing the
    stage/event columns in its header or its last row -- is open with
    stage "unknown", the safe side. Otherwise the job is open, with the
    last row's stage, when the last row's event is "start" or "waiting";
    closed when it's "approved" or "done"; and open with stage "unknown"
    when it's none of those four.
    """
    job_name = events_path.parent.name
    try:
        with open(events_path, newline="", encoding="utf-8") as events_file:
            rows = list(csv.DictReader(events_file))
    except FileNotFoundError:
        return None
    except (OSError, csv.Error, UnicodeDecodeError):
        return OpenJob(job_name, "unknown")

    if not rows:
        return None

    last_row = rows[-1]
    event = last_row.get("event")
    stage = last_row.get("stage")
    if event is None or stage is None:
        return OpenJob(job_name, "unknown")
    if event in ("start", "waiting"):
        return OpenJob(job_name, stage)
    if event in ("approved", "done"):
        return None
    return OpenJob(job_name, "unknown")


def open_jobs(root: Path) -> list[OpenJob]:
    """Return the OpenJob for every assignments/<dir> that is still open,
    sorted by job name. Skips files and any folder whose name starts with
    "_" (templates, shelved jobs)."""
    assignments_dir = root / "assignments"
    if not assignments_dir.is_dir():
        return []
    jobs: list[OpenJob] = []
    for entry in sorted(assignments_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        status = _job_status(entry / "events.csv")
        if status is not None:
            jobs.append(status)
    return jobs

