#!/usr/bin/env python3
"""
session_start.py - the plugin's SessionStart hook. Loads product/rules.md
as the session's context, finds the appraiser's Appraisal Comps folder (the
"workspace"), and on a new plugin version runs any pending migrations and
ledger.py check before recording the new version as seen. It also runs
the daily update check.

Reads the hook's JSON payload on stdin and uses its "cwd" to find the
workspace (see tools/workspace.py for the search order). If stdin is empty
or not JSON, the process's own current directory is used instead.

    python3 -I session_start.py            # prints the hooks contract JSON
    python3 -I session_start.py --print     # prints the same context as plain text

Never raises past main(): an unexpected failure still prints valid hook
JSON whose context says what went wrong, not a traceback. The rules always
come first; a failure in the status or update work only adds an
"ERROR: ..." line after them.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from workspace import plugin_root, plugin_version, workspace_candidates  # noqa: E402
from migrations import MigrationError, parse_version, run_pending  # noqa: E402
from update_check import DEFAULT_SOURCE, INSTALL_HOW, failure_reason, fetch_latest, open_jobs  # noqa: E402

MIGRATION_FAILED_FOLLOW_UP = (
    "Start no assignment work. Tell him. If an assignment is open, log "
    "`ledger.py error --assignment <it> --stage <its stage> --code UPDATE_FAILED`; "
    "otherwise just tell him."
)

CheckResult = Literal["ok", "failed", ""]


@dataclass
class PluginState:
    """settings/plugin-state.json. A missing file means first run."""

    last_seen_version: str = "0.0.0"
    last_check_date: str = ""
    latest_version: str = ""
    last_check_result: CheckResult = ""


def plugin_state_path(workspace: Path) -> Path:
    return workspace / "settings" / "plugin-state.json"


def _is_version(value: object) -> bool:
    """True when value is a strict X.Y.Z version string."""
    if not isinstance(value, str):
        return False
    try:
        parse_version(value)
    except MigrationError:
        return False
    return True


def _text_field(raw: dict, key: str) -> str:
    """raw[key] when it is a string, else "" (missing or the wrong type)."""
    value = raw.get(key)
    if isinstance(value, str):
        return value
    return ""


def _check_result_field(raw: dict) -> CheckResult:
    """raw["last_check_result"] when it is "ok" or "failed", else ""."""
    value = raw.get("last_check_result")
    if value == "ok":
        return "ok"
    if value == "failed":
        return "failed"
    return ""


def read_plugin_state(workspace: Path) -> PluginState:
    """settings/plugin-state.json, or the first-run defaults if it is
    missing or unreadable. It is hand-editable JSON, so every field is
    narrowed: a field of the wrong type, or a version that isn't X.Y.Z,
    is treated as missing and gets its default."""
    try:
        raw = json.loads(plugin_state_path(workspace).read_text())
    except (OSError, ValueError):
        return PluginState()
    if not isinstance(raw, dict):
        return PluginState()
    defaults = PluginState()
    last_seen_version = raw.get("last_seen_version")
    if not _is_version(last_seen_version):
        last_seen_version = defaults.last_seen_version
    latest_version = raw.get("latest_version")
    if not _is_version(latest_version):
        latest_version = defaults.latest_version
    return PluginState(
        last_seen_version=last_seen_version,
        last_check_date=_text_field(raw, "last_check_date"),
        latest_version=latest_version,
        last_check_result=_check_result_field(raw),
    )


def write_plugin_state(workspace: Path, state: PluginState) -> None:
    path = plugin_state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataclasses.asdict(state), indent=2) + "\n")


def load_rules() -> str:
    """product/rules.md with every ${CLAUDE_PLUGIN_ROOT} token replaced by
    this plugin's real root."""
    rules_text = (plugin_root() / "rules.md").read_text()
    return rules_text.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root()))


def no_workspace_line(candidates: list[Path]) -> str:
    """The NO_WORKSPACE status line. For several candidates, names them."""
    if not candidates:
        return ("NO_WORKSPACE no Appraisal Comps folder found. "
                "Ask him to open the Appraisal Comps folder, or run the setup skill.")
    names = ", ".join(str(candidate) for candidate in candidates)
    return f"NO_WORKSPACE found {len(candidates)} Appraisal Comps folders: {names}. Ask him to open just one of them."


def migration_failed_line(name: str, detail: str) -> str:
    return f"MIGRATION_FAILED {name}: {detail} {MIGRATION_FAILED_FOLLOW_UP}"


def ledger_check_problem(workspace: Path) -> str | None:
    """Run this plugin's own ledger.py check with cwd = workspace. None if
    it passes; otherwise the first PROBLEM line of its output (or a short
    fallback if it printed none)."""
    ledger_path = plugin_root() / "tools" / "ledger.py"
    result = subprocess.run(
        [sys.executable, "-I", str(ledger_path), "check"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("PROBLEM "):
            return line
    return result.stdout.strip() or result.stderr.strip() or "ledger.py check failed"


def run_migrations_and_check(workspace: Path, from_version: str, to_version: str) -> str | None:
    """Run every pending migration, then ledger.py check. Returns a
    MIGRATION_FAILED line on the first failure, else None."""
    try:
        run_pending(workspace, from_version, to_version)
    except MigrationError as error:
        return migration_failed_line(error.name, error.detail)
    problem = ledger_check_problem(workspace)
    if problem is not None:
        return migration_failed_line("ledger-check", problem)
    return None


def changelog_section(version: str) -> str:
    """The "## <version>" section of this plugin's CHANGELOG.md (the
    heading may carry a suffix like " (draft)"), up to the next "## "
    heading. Empty string if the file or the section is missing."""
    try:
        text = (plugin_root() / "CHANGELOG.md").read_text()
    except OSError:
        return ""
    heading = re.search(rf"^## {re.escape(version)}\b.*$", text, re.MULTILINE)
    if heading is None:
        return ""
    next_heading = re.search(r"^## ", text[heading.end():], re.MULTILINE)
    end = heading.end() + next_heading.start() if next_heading else len(text)
    return text[heading.start():end].strip()


def new_version_lines(workspace: Path, current_version: str) -> list[str]:
    """Run pending migrations and ledger.py check, record the version seen
    on success, and return the status lines to show."""
    state = read_plugin_state(workspace)
    failure_line = run_migrations_and_check(workspace, state.last_seen_version, current_version)
    if failure_line is not None:
        return [failure_line]
    write_plugin_state(workspace, dataclasses.replace(state, last_seen_version=current_version))
    return [f"NEW_VERSION {current_version}", changelog_section(current_version)]


def status_lines(workspace: Path) -> list[str]:
    state = read_plugin_state(workspace)
    current_version = plugin_version()
    try:
        is_current = parse_version(state.last_seen_version) >= parse_version(current_version)
    except MigrationError:
        is_current = False
    if is_current:
        return []
    return new_version_lines(workspace, current_version)


def _open_job_suffix(workspace: Path) -> str:
    """The ' Open job: <folder1>, <folder2>; finish it first.' suffix for
    every open assignment, or '' if none are open."""
    jobs = open_jobs(workspace)
    if not jobs:
        return ""
    names = ", ".join(job.name for job in jobs)
    return f" Open job: {names}; finish it first."


def _version_line(workspace: Path, latest_version: str) -> str:
    """UPDATE_READY (with the Install: steps and, if one is open, the open
    job warning) or UP_TO_DATE, comparing latest_version with the installed
    plugin_version()."""
    if parse_version(latest_version) <= parse_version(plugin_version()):
        return "UP_TO_DATE"
    return f"UPDATE_READY {latest_version}. Install: {INSTALL_HOW}" + _open_job_suffix(workspace)


def update_line(workspace: Path, state: PluginState, today: str) -> str:
    """The update-check line to add to the session context.

    Fetches latest.json at most once a day, caching latest_version and
    last_check_result in settings/plugin-state.json. Returns
    "UPDATE_READY <v>. Install: ..." or "UP_TO_DATE", computed fresh from
    the cached version on a day already checked; or "CHECK_FAILED <reason>"
    the first time that failure is seen today, and "" (print nothing) on
    every later session the same day.
    """
    checked_today = state.last_check_date == today
    if checked_today and state.last_check_result == "failed":
        return ""
    if checked_today and state.latest_version != "":
        return _version_line(workspace, state.latest_version)
    try:
        latest = fetch_latest(DEFAULT_SOURCE)
    except Exception as error:
        reason, _fix = failure_reason(error)
        write_plugin_state(workspace, dataclasses.replace(
            state, last_check_date=today, last_check_result="failed"))
        return f"CHECK_FAILED {reason}"
    write_plugin_state(workspace, dataclasses.replace(
        state, last_check_date=today, latest_version=latest.version, last_check_result="ok"))
    return _version_line(workspace, latest.version)


def workspace_lines(start: Path) -> list[str]:
    """Every status line to show after the rules. If any step fails, the
    lines gathered so far are kept and an "ERROR: ..." line is added."""
    lines: list[str] = []
    try:
        candidates = workspace_candidates(start)
        if len(candidates) != 1:
            return [no_workspace_line(candidates)]
        workspace = candidates[0]
        lines.extend(status_lines(workspace))
        update = update_line(workspace, read_plugin_state(workspace), date.today().isoformat())
        if update:
            lines.append(update)
    except Exception as error:  # keep the rules: report what failed after them
        lines.append(f"ERROR: session_start.py failed: {error}")
    return lines


def build_context(start: Path) -> str:
    rules_text = load_rules()
    lines = workspace_lines(start)
    if lines:
        return rules_text + "\n\n" + "\n".join(lines)
    return rules_text


def read_hook_payload() -> dict:
    """The hook's JSON payload on stdin, or {} if stdin is empty or not a
    JSON object."""
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def start_hint(payload: dict) -> Path:
    """Where to start looking for the workspace: the hook's own cwd, or the
    process's current directory if the payload has none."""
    cwd_value = payload.get("cwd")
    if isinstance(cwd_value, str) and cwd_value:
        return Path(cwd_value)
    return Path.cwd()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--print", action="store_true", dest="print_only",
                        help="print the context as plain text instead of the hooks contract JSON")
    args = parser.parse_args()

    try:
        context = build_context(start_hint(read_hook_payload()))
    except Exception as error:  # never a traceback: say what failed instead
        context = f"ERROR: session_start.py failed: {error}"

    if args.print_only:
        print(context)
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}))


if __name__ == "__main__":
    main()
