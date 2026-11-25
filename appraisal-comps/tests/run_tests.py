#!/usr/bin/env python3
"""
run_tests.py - regression tests for the workflow's scripts. Offline, fixed inputs.

Run before every release and after any change to product/:
    python3 -I product/tests/run_tests.py

It copies product/ into a throwaway folder with a fake assignment, so the real
settings/, shared/ and assignments/ are never touched. Every test must pass.
An agent must never edit a test or an expected value to make it pass: fix the
script, or report the failure to the maintainer.
"""
from __future__ import annotations

import argparse, base64, re, contextlib, csv, fnmatch, functools, hashlib, importlib, importlib.util, io, json, os, shutil, stat, subprocess, sys, tempfile, time, zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
PLAN_AREA = PRODUCT / "skills/floorplan-measure/scripts/plan_area.py"
NYC = PRODUCT / "skills/comp-pool/scripts/nyc_sales.py"
BRACKET = PRODUCT / "skills/comp-pool/scripts/bracket.py"
SCORER = PRODUCT / "evals/score_calibration.py"
GUARD = PRODUCT / "hooks/guard.py"
POST_TOOL = PRODUCT / "hooks/post_tool.py"
SOURCES = PRODUCT / "tools/sources.py"
LEDGER = PRODUCT / "tools/ledger.py"
CLOUD_READ = PRODUCT / "tools/cloud_read.py"
CLIENT_CANARIES = ("Chase Bank", "Maria Lopez", "LN-99812")
# The installed version, from the plugin's own manifest, so a version bump
# doesn't touch the tests. NEWER_VERSION and NEXT_NEWER_VERSION stand for
# releases above it, for the update check.
CURRENT_VERSION = json.loads((PRODUCT / ".claude-plugin/plugin.json").read_text())["version"]
NEWER_VERSION = "99.0.0"
NEXT_NEWER_VERSION = "99.0.1"
BROWSER_TOOL_PREFIX = "mcp__plugin_appraisal-comps_browser__browser_"  # must match guard.BROWSER_TOOL_PREFIX
results = []

# A HOME outside the real appraiser's folder, so "no workspace here" stays true even
# when this test runs in a sandbox where ~/mnt/<the real workspace> exists. Set once,
# by main(), before any test runs.
SAFE_HOME: Path | None = None

def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def run(*args, cwd=None, env=None):
    child_env = dict(os.environ)
    child_env["HOME"] = str(SAFE_HOME)
    child_env.pop("CLAUDE_CODE_WORKSPACE_HOST_PATHS", None)
    child_env.pop("CLAUDE_PLUGIN_DATA", None)  # never write into the real session's plugin data
    if env:
        child_env.update(env)
    p = subprocess.run([sys.executable, "-I", *map(str, args)], capture_output=True, text=True,
                       cwd=cwd, env=child_env)
    return p.returncode, p.stdout + p.stderr


def run_streams(*args, cwd=None, env=None):
    """Like run(), but keeps stdout and stderr apart - for a check that cares
    which stream a line went to (launch.py's stdout is the MCP wire protocol;
    nothing may ever print there, not even its own error message)."""
    child_env = dict(os.environ)
    child_env["HOME"] = str(SAFE_HOME)
    child_env.pop("CLAUDE_CODE_WORKSPACE_HOST_PATHS", None)
    child_env.pop("CLAUDE_PLUGIN_DATA", None)  # never write into the real session's plugin data
    if env:
        child_env.update(env)
    p = subprocess.run([sys.executable, "-I", *map(str, args)], capture_output=True, text=True,
                       cwd=cwd, env=child_env)
    return p.returncode, p.stdout, p.stderr


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def run_hook(script, payload, cwd=None, env=None):
    """Run a hook script with payload as JSON on stdin, in the same isolated
    env as run() (env merged on top). Returns (returncode, combined output)."""
    child_env = dict(os.environ)
    child_env["HOME"] = str(SAFE_HOME)
    child_env.pop("CLAUDE_CODE_WORKSPACE_HOST_PATHS", None)
    child_env.pop("CLAUDE_PLUGIN_DATA", None)  # never write into the real session's plugin data
    if env:
        child_env.update(env)
    p = subprocess.run([sys.executable, "-I", str(script)], input=json.dumps(payload),
                       capture_output=True, text=True, cwd=cwd, env=child_env)
    return p.returncode, p.stdout + p.stderr


def session_context(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def make_cloud_assignment(tmp):
    """A workspace with one assignment whose order.json holds the client canaries,
    so a test can prove none of them is ever sent. Returns (workspace, assignment)."""
    workspace = make_workspace(tmp / "ws")
    assignment = workspace / "assignments/a1"
    (assignment / "data").mkdir(parents=True)
    write(assignment / "subject.json", json.dumps({"address": "25-47 77th St, East Elmhurst, NY 11370"}))
    write(assignment / "order.json", json.dumps({"client": CLIENT_CANARIES[0], "borrower": CLIENT_CANARIES[1],
                                                  "loan": CLIENT_CANARIES[2]}))
    return workspace, assignment


def tool_module(name):
    """product/tools/<name>.py, imported into this process. A module of the same
    name loaded from a copied plugin by an earlier test is dropped first."""
    tools_folder = PRODUCT / "tools"
    if str(tools_folder) in sys.path:
        sys.path.remove(str(tools_folder))
    sys.path.insert(0, str(tools_folder))
    for module_name, loaded in list(sys.modules.items()):
        loaded_file = getattr(loaded, "__file__", None)
        if not loaded_file or not (tools_folder / f"{module_name}.py").exists():
            continue
        if Path(loaded_file).parent != tools_folder:
            sys.modules.pop(module_name)
    return importlib.import_module(name)


def cloud_main(argv, fixtures=None):
    """Run cloud_read.main in this process. Returns (exit code, stdout and stderr together)."""
    module = tool_module("cloud_read")
    captured = io.StringIO()
    if fixtures is not None:
        os.environ["APPRAISAL_BROWSER_USE_FIXTURES"] = str(fixtures)
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            try:
                code = module.main(argv)
            except SystemExit as error:
                code = error.code if isinstance(error.code, int) else 1
    finally:
        os.environ.pop("APPRAISAL_BROWSER_USE_FIXTURES", None)
    return code, captured.getvalue()


def test_cloud_ledger(tmp):
    workspace, assignment = make_cloud_assignment(tmp)
    previous_folder = Path.cwd()
    os.chdir(tmp)
    try:
        ledger = tool_module("ledger")
        sources = tool_module("sources")
        check("cloud: ledger and sources import outside a workspace", True)
    finally:
        os.chdir(previous_folder)
    check("cloud: CLOUD_RUNS columns", ledger.CLOUD_RUNS == [
        "id", "site", "job", "status", "model", "input_tokens", "output_tokens", "cost_usd", "seconds",
        "fields_ok", "fields_rejected", "capture", "at", "version"], ledger.CLOUD_RUNS)
    ledger.log_error(assignment, "cloud", "CLOUD_BLOCKED", "Zillow")
    rows = list(csv.DictReader((assignment / "errors.csv").open()))
    check("cloud: log_error writes the row", len(rows) == 1 and rows[0]["stage"] == "cloud"
          and rows[0]["code"] == "CLOUD_BLOCKED" and rows[0]["detail"] == "Zillow", rows)
    try:
        ledger.log_error(assignment, "cloud", "NOPE", "x")
        raised = False
    except ValueError:
        raised = True
    check("cloud: log_error refuses an unknown code", raised)
    runs_csv = assignment / "data/cloud/runs.csv"
    write(runs_csv, ",".join(ledger.CLOUD_RUNS) + "\n")
    code, out = run(LEDGER, "check", cwd=workspace)
    check("cloud: ledger check accepts runs.csv", code == 0, out)
    swapped = [ledger.CLOUD_RUNS[1], ledger.CLOUD_RUNS[0], *ledger.CLOUD_RUNS[2:]]
    write(runs_csv, ",".join(swapped) + "\n")
    code, out = run(LEDGER, "check", cwd=workspace)
    check("cloud: ledger check flags a bad runs.csv header",
          code == 1 and "PROBLEM assignments/a1/data/cloud/runs.csv" in out, out)
    rows = sources.lookup_rows(workspace, "sold-comps", "Queens")
    check("cloud: lookup_rows matches lookup order",
          [row.site for row in rows] == ["OneKey MLS", "Zillow", "StreetEasy"], [row.site for row in rows])
    try:
        sources.lookup_rows(workspace, "comps", "Queens")
        raised = False
    except ValueError:
        raised = True
    check("cloud: lookup_rows refuses a bad job", raised)
    code, out = run(SOURCES, "lookup", "--job", "orders", "--area", "everywhere", cwd=tmp)
    check("cloud: sources outside a workspace still exits 2", code == 2 and "Traceback" not in out, out)


def cloud_fakes_module():
    tests_folder = str(Path(__file__).resolve().parent)
    if tests_folder not in sys.path:
        sys.path.insert(0, tests_folder)
    return importlib.import_module("cloud_fakes")


def api_error_kind(action):
    """The ApiError kind raised by action(), or "" when nothing is raised."""
    api = tool_module("browser_use_api")
    try:
        action()
    except api.ApiError as error:
        return error.kind, error.detail
    return "", ""


def test_cloud_api(tmp):
    api = tool_module("browser_use_api")
    fakes = cloud_fakes_module()
    workspace = make_workspace(tmp / "ws")
    key_file = workspace / "settings/browser-use.key"
    for label, content in (("a newline", "bu_test_KEY123\n"), ("a BOM and CRLFs", "\ufeffbu_test_KEY123\r\n\r\n"),
                           ("blank lines and spaces", "\n  bu_test_KEY123  \n")):
        key_file.write_text(content, encoding="utf-8")
        check(f"cloud api: key read clean from {label}", api.read_key(workspace) == "bu_test_KEY123")
    for label, content in (("empty", ""), ("two words", "two words")):
        key_file.write_text(content, encoding="utf-8")
        kind, detail = api_error_kind(lambda: api.read_key(workspace))
        check(f"cloud api: a {label} key file is no_key", kind == "no_key" and "KEY123" not in detail)
    key_file.unlink()
    kind, _detail = api_error_kind(lambda: api.read_key(workspace))
    check("cloud api: a missing key file is no_key", kind == "no_key")
    os.environ.pop(api.FIXTURES_ENV, None)
    kind, _detail = api_error_kind(lambda: api.make_client(workspace))
    check("cloud api: make_client without key or fixtures is no_key", kind == "no_key")

    def client_for(name, replies):
        folder = fakes.write_replies(tmp / name, replies)
        os.environ[api.FIXTURES_ENV] = str(folder)
        try:
            return api.make_client(workspace), folder
        finally:
            os.environ.pop(api.FIXTURES_ENV, None)

    run_replies = fakes.happy_run("run-1", {"status": "done"}, "wss://cdp.example/x")
    run_replies["POST /runs"] = [{"status": 200, "body": {"id": "run-1", "sessionId": "sess-run-1",
                                                           "workspaceId": "ws-run-1"}}]
    client, folder = client_for("replay", run_replies)
    created = client.create_run("t", {"type": "object"}, "bu-4.0-mini", "0.50")
    check("cloud api: create_run returns the ids",
          (created.run_id, created.session_id, created.workspace_id) == ("run-1", "sess-run-1", "ws-run-1"))
    sent = fakes.sent_requests(folder)
    check("cloud api: create_run sends exactly the documented body",
          len(sent) == 1 and sent[0]["method"] == "POST" and sent[0]["path"] == "/runs"
          and sent[0]["body"] == {"task": "t", "outputSchema": {"type": "object"}, "model": "bu-4.0-mini",
                                  "browserSettings": {"proxyCountryCode": "us"}, "maxCostUsd": "0.50",
                                  "keepAlive": 120}, sent)
    check("cloud api: replay client reports replaying", client.replaying)
    statuses = [client.run_status("run-1") for _ in range(3)]
    check("cloud api: the last status repeats", statuses == ["running", "completed", "completed"], statuses)
    browsers = client.active_browsers("sess-run-1")
    check("cloud api: active_browsers maps the list", [(b.browser_id, b.cdp_url) for b in browsers]
          == [("br-run-1", "wss://cdp.example/x")], browsers)
    check("cloud api: the browsers query keys are in order",
          fakes.sent_requests(folder)[-1]["path"] == "/browsers?agentSessionId=sess-run-1&filterBy=active")
    client.stop_browser("br-run-1")
    last = fakes.sent_requests(folder)[-1]
    check("cloud api: stop_browser sends PATCH with the stop action",
          last["method"] == "PATCH" and last["path"] == "/browsers/br-run-1" and last["body"] == {"action": "stop"}, last)
    client.delete_session("sess-run-1")
    client.delete_workspace("ws-run-1")
    result = client.get_run("run-1")
    check("cloud api: get_run maps the fields", result.status == "completed" and result.input_tokens == 1200
          and result.output_tokens == 300 and str(result.cost_usd) == "0.04" and result.output == {"status": "done"})
    check("cloud api: the key never reaches sent.jsonl", "KEY123" not in (folder / "sent.jsonl").read_text())

    client, _folder = client_for("nulls", {
        "GET /runs/r": [{"status": 200, "body": {"status": "completed", "output": None, "error": None,
                                                  "totalInputTokens": 1, "totalOutputTokens": 2,
                                                  "totalCostUsd": "0.0412", "sessionId": "s", "workspaceId": None}}],
        "GET /browsers?agentSessionId=s&filterBy=active": [{"status": 200, "body": {"items": [{"id": "b", "cdpUrl": None}]}}],
        "GET /runs/list": [{"status": 200, "body": []}],
        "GET /billing/account": [{"status": 200, "body": {"totalCreditsBalanceUsd": 12.5, "concurrentSessionLimit": 10}}]})
    result = client.get_run("r")
    check("cloud api: null workspace and error become empty strings, cost stays a Decimal",
          result.workspace_id == "" and result.error == "" and str(result.cost_usd) == "0.0412", result)
    check("cloud api: a null cdpUrl becomes an empty string", client.active_browsers("s")[0].cdp_url == "")
    account = client.account()
    check("cloud api: account reads credit and limit", str(account.credit_usd) == "12.5" and account.concurrent_limit == 10)
    kind, _detail = api_error_kind(lambda: client.get_run("list"))
    check("cloud api: a list where an object is expected is an http error", kind == "http", kind)

    failing, _folder = client_for("errors", {
        "GET /runs/a/status": [{"status": 402, "body": {"detail": {"code": "api_key_monthly_spend_limit_reached",
                                                                    "message": "m", "cap": 25, "spent": 25.4}}}],
        "GET /runs/b/status": [{"status": 402, "body": {"detail": "No credits"}}],
        "GET /runs/c/status": [{"status": 403, "body": {}}],
        "GET /runs/d/status": [{"status": 500, "body": {}}],
        "GET /runs/e/status": [{"raise": "network"}],
        "GET /runs/f/status": [{"status": 429, "headers": {"Retry-After": "0.01"}}, {"status": 200, "body": {"status": "running"}}],
        "GET /runs/g/status": [{"status": 429, "headers": {"Retry-After": "0.01"}}]})
    expected = {"a": ("budget", "cap $25, spent $25.4"), "b": ("budget", ""), "c": ("no_key", None),
                "d": ("http", None), "e": ("network", None)}
    for run_id, (want_kind, want_detail) in expected.items():
        kind, detail = api_error_kind(lambda: failing.run_status(run_id))
        detail_ok = want_detail is None or detail == want_detail
        check(f"cloud api: reply {run_id} maps to {want_kind}", kind == want_kind and detail_ok, (kind, detail))
    check("cloud api: a 429 then a 200 succeeds", failing.run_status("f") == "running")
    started = time.time()
    kind, _detail = api_error_kind(lambda: failing.run_status("g"))
    check("cloud api: a repeating 429 ends rate_limited quickly", kind == "rate_limited" and time.time() - started < 1)


CLOUD_PAGE = ("123-45 Example Ave, Flushing, NY 11354\nSold on 03/14/2026 for $1,250,000\n"
              "3 bd\t2.5 ba\t1,620 sqft\nLot: 11,200 sq ft\nBuilt in 1931\nMLS# 3541278\nStatus: Closed\n")


def test_cloud_quote_check(tmp):
    quote_check = tool_module("quote_check")
    schema_folder = PRODUCT / "tools/cloud_schemas"
    schemas = {}
    for name in ("listing-details", "property-card", "tax-map-lot"):
        try:
            schemas[name] = quote_check.load_schema(schema_folder / f"{name}.json")
            check(f"cloud quote: {name} schema loads", True)
        except quote_check.SchemaError as error:
            check(f"cloud quote: {name} schema loads", False, repr(error))
    fields = schemas["listing-details"]
    json_schema = quote_check.output_schema(fields)
    required = json_schema["properties"]["fields"]["required"]
    check("cloud quote: output schema requires all 13 names", len(required) == 13 and required == [f.name for f in fields])
    check("cloud quote: output schema has no $ref", "$ref" not in json.dumps(json_schema))
    bad_schema = write(tmp / "bad.json", json.dumps({"fields": [{"name": "Beds", "type": "integer"}]}))
    try:
        quote_check.load_schema(bad_schema)
        raised = False
    except quote_check.SchemaError:
        raised = True
    check("cloud quote: a bad field name is a SchemaError", raised)

    def result_for(name, entry):
        """The FieldResult for one field entry, with every other field not found."""
        output = {"status": "done", "page_url": "https://www.zillow.com/x", "injection_seen": False,
                  "fields": {name: entry}}
        record = quote_check.check_record(output, fields, CLOUD_PAGE)
        return [result for result in record.fields if result.name == name][0]

    rows = [
        ("sale_price", {"value": 1250000, "unit": "usd", "quote": "Sold on 03/14/2026 for $1,250,000"}, "accepted", ""),
        ("sale_date", {"value": "2026-03-14", "quote": "Sold on 03/14/2026"}, "accepted", ""),
        ("baths", {"value": 2.5, "quote": "2.5 ba"}, "accepted", ""),
        ("living_sqft", {"value": 1620, "unit": "sqft", "quote": "1,620 SQFT"}, "accepted", ""),
        ("living_sqft", {"value": 162, "unit": "sqft", "quote": "1,620 sqft"}, "rejected", "quote-value"),
        ("lot_sqft", {"value": 1200, "unit": "sqft", "quote": "Lot: 11,200 sq ft"}, "rejected", "quote-value"),
        ("year_built", {"value": 1931, "quote": "Built in 1913"}, "rejected", "quote-not-in-page"),
        ("beds", {"value": 3, "quote": "3 bd \u2026 1,620 sqft"}, "accepted", ""),
        ("beds", {"value": True, "quote": "3 bd"}, "rejected", "type"),
        ("beds", {"value": 30, "quote": "3 bd"}, "rejected", "range"),
        ("living_sqft", {"value": 1620, "unit": "m2", "quote": "1,620 sqft"}, "rejected", "unit"),
        ("status", {"value": "sold", "quote": "Status: Closed"}, "accepted", ""),
        ("status", {"value": "active", "quote": "Status: Closed"}, "rejected", "quote-value"),
        ("mls_number", {"value": "3541278", "quote": "MLS# 3541278"}, "accepted", ""),
        ("beds", {"value": 3}, "rejected", "quote-missing"),
        ("beds", {"value": 3, "quote": "x" * 201}, "rejected", "quote-long"),
        ("beds", {"not_found": True}, "not_found", ""),
        ("lot_sqft", {"value": 11200, "unit": "sqft", "quote": "Lot: 11,200 sq ft"}, "accepted", ""),
        ("sale_price", {"value": 1250000, "unit": "usd", "quote": "for $1.25M"}, "rejected", "quote-not-in-page"),
        ("beds", {"value": 3, "quote": "3 bd ... 2.5 ba ... 1,620 sqft ... Built in 1931"}, "rejected", "quote-not-in-page"),
    ]
    for name, entry, outcome, rule in rows:
        result = result_for(name, entry)
        check(f"cloud quote: {name} {json.dumps(entry)[:50]} is {outcome} {rule}".strip(),
              result.outcome == outcome and result.rule == rule, (result.outcome, result.rule))
    accepted = result_for("sale_price", {"value": 1250000, "unit": "usd", "quote": "Sold on 03/14/2026 for $1,250,000"})
    check("cloud quote: an accepted field carries value, unit and quote",
          (accepted.value, accepted.unit, accepted.quote) == (1250000, "usd", "Sold on 03/14/2026 for $1,250,000"))
    onekey_page = "Source: OneKey MLS as distributed by MLS GRID,MLS#: LP1436116\n"
    onekey = quote_check.check_record({"status": "done", "page_url": "u", "injection_seen": False,
                                       "fields": {"mls_number": {"value": "LP1436116", "quote": "MLS#: LP1436116"}}},
                                      fields, onekey_page)
    check("cloud quote: an MLS number with a letter prefix is accepted",
          [r.outcome for r in onekey.fields if r.name == "mls_number"] == ["accepted"])
    price_properties = quote_check.output_schema(fields)["properties"]["fields"]["properties"]["sale_price"]
    check("cloud quote: the output schema fixes a field's unit to the spec's unit",
          price_properties["anyOf"][0]["properties"]["unit"] == {"type": "string", "enum": ["usd"]})
    absent = quote_check.check_record({"status": "done", "page_url": "u", "injection_seen": False, "fields": {}},
                                      fields, CLOUD_PAGE)
    check("cloud quote: an absent field is not_found", all(result.outcome == "not_found" for result in absent.fields))
    extra = quote_check.check_record({"status": "done", "page_url": "u", "injection_seen": False,
                                      "fields": {"owner_name": {"value": "x", "quote": "x"}}}, fields, CLOUD_PAGE)
    check("cloud quote: an unrequested field is counted and not listed",
          extra.unrequested == 1 and all(result.name != "owner_name" for result in extra.fields))

    good_output = {"status": "done", "page_url": "https://www.zillow.com/x", "injection_seen": True,
                   "fields": {"beds": {"value": 3, "quote": "3 bd"}}}
    for label, output in (("None", None), ("a list", []), ("non-JSON text", "not json"),
                          ("a bad status", dict(good_output, status="weird")),
                          ("an extra key", dict(good_output, owner="x")),
                          ("a wrong type", dict(good_output, injection_seen="yes"))):
        record = quote_check.check_record(output, fields, CLOUD_PAGE)
        check(f"cloud quote: output {label} is shape", not record.valid and record.rule == "shape", record)
    as_string = quote_check.check_record(json.dumps(good_output), fields, CLOUD_PAGE)
    check("cloud quote: a JSON string output is parsed", as_string.valid and as_string.injection_seen
          and as_string.run_status == "done" and as_string.page_url == "https://www.zillow.com/x", as_string)
    huge = dict(good_output, page_url="x" * (quote_check.MAX_OUTPUT_BYTES + 1))
    check("cloud quote: a huge output is too-large", quote_check.check_record(huge, fields, CLOUD_PAGE).rule == "too-large")
    curly = quote_check.check_record(
        {"status": "done", "page_url": "u", "injection_seen": False,
         "fields": {"status": {"value": "sold", "quote": "Owner\u2019s note:\u00a0Status: Closed"}}},
        fields, "The Owner's note: Status: Closed")
    status_result = [result for result in curly.fields if result.name == "status"][0]
    check("cloud quote: curly quotes and non-breaking spaces still match", status_result.outcome == "accepted", status_result)
    imports = [line for line in (PRODUCT / "tools/quote_check.py").read_text().splitlines() if line.startswith(("import ", "from "))]
    allowed = {"__future__", "json", "math", "re", "unicodedata", "dataclasses", "datetime", "decimal", "pathlib", "typing"}
    modules = {line.split()[1].split(".")[0] for line in imports}
    check("cloud quote: only the standard library is imported", modules <= allowed, modules)


def cdp_error_text(action):
    """The CdpError text raised by action(), or "" when nothing is raised."""
    cdp_client = tool_module("cdp_client")
    try:
        action()
    except cdp_client.CdpError as error:
        return str(error)
    return ""


def test_cloud_cdp(tmp):
    cdp_client = tool_module("cdp_client")
    fakes = cloud_fakes_module()
    big_pdf = os.urandom(3 * 1024 * 1024)
    page = fakes.FakePage("t1", "https://www.zillow.com/a", "page text", big_pdf)
    other = fakes.FakePage("t2", "https://www.zillow.com/b", "other text", b"%PDF other")

    server = fakes.FakeCdpServer([page, other], fragment_bytes=65536, ping_before_reply=True)
    server.start()
    try:
        with cdp_client.CdpConnection(server.url, 2, allow_plain=True) as connection:
            targets = connection.call("Target.getTargets", {})
            check("cloud cdp: getTargets lists the pages", [t["targetId"] for t in targets["targetInfos"]] == ["t1", "t2"], targets)
            attached = connection.call("Target.attachToTarget", {"targetId": "t1", "flatten": True})
            session_id = attached["sessionId"]
            started = time.time()
            printed = connection.call("Page.printToPDF", {"printBackground": True}, session_id)
            elapsed = time.time() - started
            same_pdf = hashlib.sha256(base64.b64decode(printed["data"])).hexdigest() == hashlib.sha256(big_pdf).hexdigest()
            check("cloud cdp: a 3 MB PDF comes back whole in fragments, quickly", same_pdf and elapsed < 2, elapsed)
            for _ in range(100):
                if server.pongs:
                    break
                time.sleep(0.01)
            check("cloud cdp: a ping is answered with a pong", server.pongs >= 1, server.pongs)
            check("cloud cdp: an unknown method is a CdpError",
                  cdp_error_text(lambda: connection.call("Nope.nothing", {})) != "")
            connection.call("Page.enable", {}, session_id)
            connection.call("Page.navigate", {"url": "https://www.zillow.com/b"}, session_id)
            event = connection.wait_for_event("Page.loadEventFired", session_id, 1)
            check("cloud cdp: wait_for_event returns after navigate", event == {}, event)
            href = connection.call("Runtime.evaluate", {"expression": "location.href", "returnByValue": True}, session_id)
            check("cloud cdp: navigate moved the page", href["result"]["value"] == "https://www.zillow.com/b", href)
            started = time.time()
            text = cdp_error_text(lambda: connection.wait_for_event("Page.loadEventFired", session_id, 0.2))
            check("cloud cdp: an event that never comes times out", text == "timeout" and time.time() - started < 1, text)
    finally:
        server.stop()

    server = fakes.FakeCdpServer([page], close_on_method="Runtime.evaluate")
    server.start()
    try:
        with cdp_client.CdpConnection(server.url, 1, allow_plain=True) as connection:
            session_id = connection.call("Target.attachToTarget", {"targetId": "t1"})["sessionId"]
            started = time.time()
            text = cdp_error_text(lambda: connection.call("Runtime.evaluate", {"expression": "location.href"}, session_id))
            check("cloud cdp: a socket closed mid-call is a CdpError, no hang", text != "" and time.time() - started < 1.5, text)
    finally:
        server.stop()

    for url, allow_plain, label in (("https://x.example/cdp", False, "https"), ("ws://127.0.0.1:1/x", False, "plain ws"),
                                    ("http://x.example", True, "http")):
        text = cdp_error_text(lambda: cdp_client.CdpConnection(url, 1, allow_plain=allow_plain))
        check(f"cloud cdp: {label} address is refused", text == "cdp-url-scheme", text)


CLOUD_ZILLOW_URL = "https://www.zillow.com/homedetails/123-45-Example-Ave/1_zpid/"
CLOUD_QUERY = "123-45 Example Ave, Flushing, NY 11354"
CLOUD_PDF = b"%PDF-1.4 fake"


def cloud_listing_output(page_url=CLOUD_ZILLOW_URL, status="done", wrong_year=True):
    """A run's output for listing-details: five right fields, year_built (wrong quote) and the rest not found."""
    quote_check = tool_module("quote_check")
    names = [field.name for field in quote_check.load_schema(PRODUCT / "tools/cloud_schemas/listing-details.json")]
    found = {
        "sale_price": {"value": 1250000, "unit": "usd", "quote": "Sold on 03/14/2026 for $1,250,000"},
        "sale_date": {"value": "2026-03-14", "quote": "Sold on 03/14/2026"},
        "beds": {"value": 3, "quote": "3 bd"},
        "baths": {"value": 2.5, "quote": "2.5 ba"},
        "living_sqft": {"value": 1620, "unit": "sqft", "quote": "1,620 sqft"},
        "year_built": {"value": 1931, "quote": "Built in 1913"},
    }
    fields = {name: found.get(name, {"not_found": True}) for name in names}
    return {"status": status, "page_url": page_url, "injection_seen": False, "fields": fields}


def cloud_scenario(tmp, name, output, pages=None, extra_replies=None, cost="0.04", timing=None, **server_options):
    """A running FakeCdpServer and a fixtures folder for one completed run, run-1. Returns (server, fixtures)."""
    fakes = cloud_fakes_module()
    if pages is None:
        pages = [fakes.FakePage("t1", CLOUD_ZILLOW_URL, CLOUD_PAGE, CLOUD_PDF)]
    server = fakes.FakeCdpServer(pages, **server_options)
    server.start()
    replies = fakes.happy_run("run-1", output, server.url, cost=cost)
    replies["POST /runs"] = [{"status": 200, "body": {"id": "run-1", "sessionId": "sess-run-1", "workspaceId": "ws-run-1"}}]
    if extra_replies:
        replies.update(extra_replies)
    return server, fakes.write_replies(tmp / name, replies, timing)


def cloud_read_args(assignment, site="Zillow", job="listing-details", query=CLOUD_QUERY, area="Queens"):
    return ["read", "--assignment", str(assignment), "--site", site, "--job", job, "--area", area, "--query", query]


def test_cloud_read(tmp):
    cloud_read = tool_module("cloud_read")
    ledger = tool_module("ledger")
    fakes = cloud_fakes_module()
    workspace, assignment = make_cloud_assignment(tmp)
    cloud_folder = assignment / "data/cloud"

    server, fixtures = cloud_scenario(tmp, "ok", cloud_listing_output())
    try:
        started = datetime.now()
        code, out = cloud_main(cloud_read_args(assignment), fixtures)
    finally:
        server.stop()
    first_line = out.splitlines()[0] if out else ""
    prefix = "done c-001-zillow: 5 fields, 7 not found, 1 rejected (year_built: quote-not-in-page); page saved; $0.04, "
    check("cloud read: exit 0 and the first line", code == 0 and first_line.startswith(prefix)
          and first_line.endswith(" s") and first_line[len(prefix):-2].isdigit(), out)
    check("cloud read: the page text is saved as captured", (cloud_folder / "c-001-zillow.txt").read_text() == CLOUD_PAGE)
    check("cloud read: the PDF is saved as captured", (cloud_folder / "c-001-zillow.pdf").read_bytes() == CLOUD_PDF)
    record = json.loads((cloud_folder / "c-001-zillow.json").read_text())
    check("cloud read: record page_url, hash and time", record["page_url"] == CLOUD_ZILLOW_URL
          and record["page_sha256"] == hashlib.sha256(CLOUD_PAGE.encode()).hexdigest()
          and abs((datetime.fromisoformat(record["retrieved_at"]) - started).total_seconds()) < 5, record)
    check("cloud read: record values and rejections",
          record["fields"]["sale_price"] == {"value": 1250000, "unit": "usd", "quote": "Sold on 03/14/2026 for $1,250,000"}
          and record["rejected"] == [{"field": "year_built", "rule": "quote-not-in-page"}], record)
    check("cloud read: record keeps neither the query nor the task", CLOUD_QUERY not in json.dumps(record))
    with (cloud_folder / "runs.csv").open(newline="") as handle:
        run_rows = list(csv.DictReader(handle))
    expected_row = {"id": "c-001-zillow", "site": "Zillow", "job": "listing-details", "status": "done",
                    "model": "bu-4.0-mini", "input_tokens": "1200", "output_tokens": "300", "cost_usd": "0.04",
                    "fields_ok": "5", "fields_rejected": "1", "capture": "cdp"}
    check("cloud read: runs.csv has the header and one row", list(run_rows[0].keys()) == ledger.CLOUD_RUNS
          and len(run_rows) == 1 and all(run_rows[0][key] == value for key, value in expected_row.items()), run_rows)
    sent = fakes.sent_requests(fixtures)
    post_body = [entry["body"] for entry in sent if entry["method"] == "POST" and entry["path"] == "/runs"][0]
    check("cloud read: POST /runs has exactly the six keys", sorted(post_body) == sorted(
          ["task", "outputSchema", "model", "browserSettings", "maxCostUsd", "keepAlive"])
          and post_body["model"] == "bu-4.0-mini" and post_body["maxCostUsd"] == "0.50"
          and post_body["keepAlive"] == 120 and post_body["browserSettings"] == {"proxyCountryCode": "us"}, post_body)
    check("cloud read: the task is the fixed text", post_body["task"] == cloud_read.build_task(
          "https://www.zillow.com", CLOUD_QUERY, {}, cloud_read.skill_body("Zillow")))
    everything = (fixtures / "sent.jsonl").read_text() + out
    check("cloud read: no client canary is sent or printed", not any(canary in everything for canary in CLIENT_CANARIES))
    check("cloud read: no page text, host or address is printed",
          not any(text in out for text in ("Sold on", "zillow.com", "Example Ave")), out)
    check("cloud read: the value lines", "  sale_price: 1250000" in out and "  sale_date: 2026-03-14" in out
          and len(out.splitlines()) <= 40, out)
    keys = [f"{entry['method']} {entry['path']}" for entry in sent]
    tail = keys[keys.index("GET /runs/run-1"):]
    check("cloud read: the browser is stopped and the session and workspace deleted", tail[1:] == [
          "GET /browsers?agentSessionId=sess-run-1&filterBy=active", "PATCH /browsers/br-run-1",
          "DELETE /sessions/sess-run-1", "DELETE /workspaces/ws-run-1"], tail)

    # the same run, output as a JSON string, is the second read
    server, fixtures = cloud_scenario(tmp, "as-string", json.dumps(cloud_listing_output()))
    try:
        code, out = cloud_main(cloud_read_args(assignment), fixtures)
    finally:
        server.stop()
    check("cloud read: a JSON-string output reads the same and takes the next id",
          code == 0 and out.startswith(prefix.replace("c-001", "c-002")), out)

    # not_found
    server, fixtures = cloud_scenario(tmp, "not-found", cloud_listing_output(status="not_found"))
    try:
        code, out = cloud_main(cloud_read_args(assignment), fixtures)
    finally:
        server.stop()
    sent_keys = [f"{entry['method']} {entry['path']}" for entry in fakes.sent_requests(fixtures)]
    check("cloud read: not_found line, exit 0, browser still stopped",
          code == 0 and out.splitlines()[0] == "not_found c-003-zillow: no page for this property; $0.04 → next site"
          and "PATCH /browsers/br-run-1" in sent_keys, out)

    # navigation: the run ended on another page of the site
    other_url = "https://www.zillow.com/homedetails/other-page/2_zpid/"
    pages = [fakes.FakePage("t1", CLOUD_ZILLOW_URL, "first page", b"first"),
             fakes.FakePage("t9", other_url, CLOUD_PAGE, CLOUD_PDF, type="background_page")]
    server, fixtures = cloud_scenario(tmp, "navigate", cloud_listing_output(page_url=other_url), pages=pages)
    try:
        code, out = cloud_main(cloud_read_args(assignment), fixtures)
        navigations = [call for call in server.calls if call.get("method") == "Page.navigate"]
    finally:
        server.stop()
    record = json.loads((cloud_folder / "c-004-zillow.json").read_text())
    check("cloud read: the script navigates to the page the run ended on",
          code == 0 and len(navigations) == 1 and record["page_url"] == other_url, (out, navigations, record))

    # a page from another host
    pages = [fakes.FakePage("t1", "https://evil.example/x", CLOUD_PAGE, CLOUD_PDF)]
    server, fixtures = cloud_scenario(tmp, "evil", cloud_listing_output(page_url="https://evil.example/x"), pages=pages)
    try:
        code, out = cloud_main(cloud_read_args(assignment), fixtures)
    finally:
        server.stop()
    record = json.loads((cloud_folder / "c-005-zillow.json").read_text())
    check("cloud read: a page from another host fails with every field rejected host",
          code == 1 and out.startswith("failed c-005-zillow") and record["status"] == "failed"
          and len(record["rejected"]) == 13 and {item["rule"] for item in record["rejected"]} == {"host"}, (out, record))

    # refusals send nothing
    nothing = fakes.write_replies(tmp / "nothing", {})
    for label, argv, wanted in (
            ("an unlisted site", cloud_read_args(assignment, site="Trulia"), "Trulia is not in the source list"),
            ("a relative assignment", cloud_read_args("assignments/a1"), "absolute"),
            ("a job with no field list", cloud_read_args(assignment, job="sold-comps"), "--schema")):
        code, out = cloud_main(argv, nothing)
        check(f"cloud read: {label} exits 2 with {wanted}", code == 2 and wanted in out and "Traceback" not in out, out)
    check("cloud read: the refusals sent nothing", not (nothing / "sent.jsonl").exists())
    code, out = run(CLOUD_READ, cwd=tmp)
    check("cloud read: no arguments prints usage, exit 2, no traceback", code == 2 and "usage" in out.lower()
          and "Traceback" not in out, out)

    # build_task
    with_match = cloud_read.build_task("https://x.example", "q", {"sale_date": "2026-03-14", "apt": "4C"}, "")
    check("cloud read: build_task puts Match third", with_match.splitlines()[2] == "Match: sale_date=2026-03-14; apt=4C", with_match)
    check("cloud read: build_task without notes has no About line", "About this site:" not in with_match)
    with_notes = cloud_read.build_task("https://x.example", "q", {}, "Look for Sold.")
    check("cloud read: build_task with notes has the About block", "About this site:\nLook for Sold.\n" in with_notes)


def cloud_inputs_refused(tmp, nothing, label, argv, wanted):
    """A refused read: exit 2, the wanted words, no traceback, and nothing sent."""
    code, out = cloud_main(argv, nothing)
    check(f"cloud inputs: {label} is refused with {wanted}",
          code == 2 and wanted in out and "Traceback" not in out and not (nothing / "sent.jsonl").exists(), (code, out))


def test_cloud_inputs(tmp):
    cloud_read = tool_module("cloud_read")
    fakes = cloud_fakes_module()
    workspace, assignment = make_cloud_assignment(tmp)
    nothing = fakes.write_replies(tmp / "nothing", {})
    refuse = lambda label, argv, wanted: cloud_inputs_refused(tmp, nothing, label, argv, wanted)

    refuse("a Chrome-only site", cloud_read_args(assignment, site="OneKey MLS"), "read in his Chrome")
    padded = "123-45 Example Ave " + "x" * 120 + ", Flushing, NY 11354"
    for query in ("Maria Lopez", "refinance for Chase Bank, loan LN-99812", "a@b.com",
                  "12 Oak Ln, Yonkers, NY 10701 for Maria Lopez", padded):
        refuse(f"query {query[:30]!r}", cloud_read_args(assignment, query=query), "--query")
    for position, query in enumerate(("25-47 77th St, East Elmhurst, NY 11370", "245 E 72nd St, Apt 4C, New York, NY 10021",
                                      "4012340056", "3541278", "LP1436116", "551800 3.120-4-12")):
        server, fixtures = cloud_scenario(tmp, f"query-{position}", cloud_listing_output())
        try:
            code, out = cloud_main(cloud_read_args(assignment, query=query), fixtures)
        finally:
            server.stop()
        task = [entry["body"]["task"] for entry in fakes.sent_requests(fixtures) if entry["path"] == "/runs"][0]
        check(f"cloud inputs: query {query!r} is accepted and sent", code == 0 and f"Find the page for this property: {query}" in task, out)
    refuse("match mls_number with a long prefix", cloud_read_args(assignment) + ["--match", "mls_number=LOAN1234567"], "--match")
    refuse("match owner", cloud_read_args(assignment) + ["--match", "owner=Lopez"], "--match")
    refuse("match apt too long", cloud_read_args(assignment) + ["--match", "apt=penthouse-suite-9"], "--match")
    server, fixtures = cloud_scenario(tmp, "match", cloud_listing_output())
    try:
        code, out = cloud_main(cloud_read_args(assignment) + ["--match", "sale_date=2026-03-14", "--match", "apt=4C"], fixtures)
    finally:
        server.stop()
    task = [entry["body"]["task"] for entry in fakes.sent_requests(fixtures) if entry["path"] == "/runs"][0]
    check("cloud inputs: matches are accepted and in the task", code == 0
          and task.splitlines()[2] == "Match: sale_date=2026-03-14; apt=4C", out)

    for bad_url in ("http://www.zillow.com/x", "https://www.zillow.com/x?note=LN-99812", "https://user@www.zillow.com/x",
                    "https://www.zillow.com/x#frag", "https://unlisted.example/x", "https://www.onekeymls.com/x",
                    "https://www.zillow.com/" + "a" * 290):
        refuse(f"url {bad_url[:40]}", cloud_read_args(assignment) + ["--url", bad_url], "--url")
    good_url = "https://www.zillow.com/homedetails/1_zpid/"
    server, fixtures = cloud_scenario(tmp, "url", cloud_listing_output())
    try:
        code, out = cloud_main(cloud_read_args(assignment) + ["--url", good_url], fixtures)
    finally:
        server.stop()
    task = [entry["body"]["task"] for entry in fakes.sent_requests(fixtures) if entry["path"] == "/runs"][0]
    check("cloud inputs: a listed --url is the start", code == 0 and task.splitlines()[0] == f"Start at: {good_url}", out)

    refuse("a relative schema", cloud_read_args(assignment, job="sold-comps") + ["--schema", "fields.json"], "--schema")
    unknown_key = write(tmp / "unknown.json", json.dumps({"fields": [{"name": "beds", "type": "integer", "colour": "red"}]}))
    refuse("a schema with an unknown key", cloud_read_args(assignment, job="sold-comps") + ["--schema", str(unknown_key)], "field list")
    two_fields = write(tmp / "two.json", json.dumps({"job": "sold-comps", "fields": [
        {"name": "sale_price", "type": "number", "unit": "usd", "min": 1000, "max": 100000000},
        {"name": "beds", "type": "integer", "min": 0, "max": 20}]}))
    output = cloud_listing_output()
    output["fields"] = {"sale_price": output["fields"]["sale_price"], "beds": output["fields"]["beds"]}
    server, fixtures = cloud_scenario(tmp, "schema", output)
    try:
        code, out = cloud_main(cloud_read_args(assignment, job="sold-comps") + ["--schema", str(two_fields)], fixtures)
    finally:
        server.stop()
    body = [entry["body"] for entry in fakes.sent_requests(fixtures) if entry["path"] == "/runs"][0]
    check("cloud inputs: a custom schema is the output schema", code == 0 and sorted(
          body["outputSchema"]["properties"]["fields"]["required"]) == ["beds", "sale_price"], out)

    write(workspace / "shared/sources.csv",
          "job,area,site,url,rank,gives,status,added_by,checked,reader\n"
          f"listing-details,Queens,Zillow,https://www.zillow.com,1,avoid in Queens,avoid,appraiser,{date.today().isoformat()},cloud\n")
    refuse("an avoided site", cloud_read_args(assignment), "not in the source list")


def csv_rows(path):
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def sent_keys(fixtures):
    return [f"{entry['method']} {entry['path']}" for entry in cloud_fakes_module().sent_requests(fixtures)]


def cloud_case(tmp, name, output, args=None, **scenario_options):
    """One read in a fresh workspace. Returns a dict with the exit code, output, assignment and fixtures."""
    workspace, assignment = make_cloud_assignment(tmp / name)
    server, fixtures = cloud_scenario(tmp, f"{name}-fixtures", output, **scenario_options)
    try:
        code, out = cloud_main(args(assignment) if args else cloud_read_args(assignment), fixtures)
    finally:
        server.stop()
    return {"code": code, "out": out, "assignment": assignment, "fixtures": fixtures, "keys": sent_keys(fixtures),
            "errors": csv_rows(assignment / "errors.csv"), "runs": csv_rows(assignment / "data/cloud/runs.csv"),
            "calls": server.calls}


def open_is_empty(assignment):
    open_path = assignment / "data/cloud/open.json"
    return not open_path.exists() or json.loads(open_path.read_text()) == []


def test_cloud_failures(tmp):
    fakes = cloud_fakes_module()
    blocked_output = cloud_listing_output(status="blocked_challenge")

    case = cloud_case(tmp, "challenge", blocked_output)
    check("cloud failures: a challenge twice is blocked after two runs",
          case["code"] == 1 and case["out"].splitlines()[0] == "blocked c-001-zillow: human check not cleared after 2 tries; "
          "$0.08 → pass Zillow for this lookup" and case["keys"].count("POST /runs") == 2
          and [row["status"] for row in case["runs"]] == ["retried", "blocked"]
          and case["keys"].count("PATCH /browsers/br-run-1") == 2
          and [(row["code"], row["detail"]) for row in case["errors"]] == [("CLOUD_BLOCKED", "Zillow")], case["out"])
    check("cloud failures: open.json is empty after a blocked read", open_is_empty(case["assignment"]))

    done_body = fakes.happy_run("run-1", cloud_listing_output(), "")["GET /runs/run-1"][0]
    blocked_body = fakes.happy_run("run-1", blocked_output, "")["GET /runs/run-1"][0]
    case = cloud_case(tmp, "challenge-then-done", blocked_output,
                      extra_replies={"GET /runs/run-1": [blocked_body, done_body]})
    check("cloud failures: a challenge then a read is done", case["code"] == 0 and case["out"].startswith("done c-001-zillow")
          and [row["status"] for row in case["runs"]] == ["retried", "done"], case["out"])

    denied_page = fakes.FakePage("t1", CLOUD_ZILLOW_URL, "Access to this page has been denied\nPress & Hold", CLOUD_PDF)
    case = cloud_case(tmp, "denied-text", cloud_listing_output(), pages=[denied_page])
    check("cloud failures: a human-check page text is a block and is retried",
          case["code"] == 1 and case["out"].startswith("blocked c-001-zillow: human check not cleared")
          and case["keys"].count("POST /runs") == 2, case["out"])

    case = cloud_case(tmp, "login", cloud_listing_output(status="blocked_login"))
    check("cloud failures: a login wall is blocked at once", case["code"] == 1 and case["keys"].count("POST /runs") == 1
          and case["out"].splitlines()[0] == "blocked c-001-zillow: login; $0.04 → offer to mark Zillow as read in his Chrome "
          "(sources.py add --reader chrome)" and [(row["code"], row["detail"]) for row in case["errors"]] == [("CLOUD_BLOCKED", "Zillow: login")],
          case["out"])

    failed_status = [{"status": 200, "body": {"status": "failed"}}]
    failed_body = dict(done_body["body"], status="failed")
    case = cloud_case(tmp, "run-failed", cloud_listing_output(), extra_replies={
        "GET /runs/run-1/status": failed_status, "GET /runs/run-1": [{"status": 200, "body": failed_body}]})
    check("cloud failures: a failed run twice is failed", case["code"] == 1
          and case["out"].splitlines()[0] == "failed c-001-zillow: run failed; $0.08 → retry once, then next site"
          and [row["status"] for row in case["runs"]] == ["retried", "failed"]
          and [row["code"] for row in case["errors"]] == ["CLOUD_FAILED"], case["out"])

    case = cloud_case(tmp, "timeout", cloud_listing_output(), timing={
        "poll_seconds": 0.01, "run_timeout_s": 0.05, "retry_after_cap_s": 0.05, "capture_timeout_s": 2},
        extra_replies={"GET /runs/run-1/status": [{"status": 200, "body": {"status": "running"}}]})
    check("cloud failures: a run that never ends times out, is cancelled and its browser stopped",
          case["code"] == 1 and case["out"].splitlines()[0] == "failed c-001-zillow: run timed out at 0.05 s; $0.08 → retry once, then next site"
          and "POST /runs/run-1/cancel" in case["keys"] and "PATCH /browsers/br-run-1" in case["keys"], case["out"])

    case = cloud_case(tmp, "shape", "not json")
    check("cloud failures: output that is not JSON twice is failed shape", case["code"] == 1
          and case["out"].splitlines()[0] == "failed c-001-zillow: shape; $0.08 → retry once, then next site"
          and case["keys"].count("POST /runs") == 2, case["out"])
    check("cloud failures: open.json is empty after a failed read", open_is_empty(case["assignment"]))

    cap_reply = {"status": 402, "body": {"detail": {"code": "api_key_monthly_spend_limit_reached", "message": "m",
                                                     "cap": 25, "spent": 25.4}}}
    case = cloud_case(tmp, "budget", cloud_listing_output(), extra_replies={"POST /runs": [cap_reply]})
    check("cloud failures: a 402 is the budget line, one request, CLOUD_BUDGET logged", case["code"] == 1
          and case["out"].splitlines()[0] == "budget: browser-use has no credit, or the key's monthly cap is reached "
          "(cap $25, spent $25.4) → tell him; public reads are off until it is topped up"
          and case["keys"] == ["POST /runs"] and [row["code"] for row in case["errors"]] == ["CLOUD_BUDGET"], case["out"])
    case = cloud_case(tmp, "rate-limited", cloud_listing_output(), extra_replies={
        "POST /runs": [{"status": 429, "headers": {"Retry-After": "0.01"}}]})
    check("cloud failures: a repeating 429 is failed rate_limited", case["code"] == 1
          and case["out"].splitlines()[0] == "failed c-001-zillow: rate_limited; $0.00 → retry once, then next site", case["out"])

    workspace, assignment = make_cloud_assignment(tmp / "no-key")
    code, out = cloud_main(cloud_read_args(assignment), None)
    check("cloud failures: no key is an input error pointing at setup", code == 2
          and "ERROR: no browser-use key → setup, step 5" in out and "Traceback" not in out, out)

    page_not_saved = "failed c-001-zillow: page not saved; $0.04 → next site"
    for label, items, expect_patch, expected_note in (
            ("a null cdpUrl", [{"id": "br-run-1", "cdpUrl": None}], True, "empty cdpUrl"),
            ("a non-wss cdpUrl", [{"id": "br-run-1", "cdpUrl": "http://cdp.example/x"}], True,
             "cdp-url-scheme (scheme http, 1 browser(s))"),
            ("no browser at all", [], False, "no browser listed")):
        browsers_key = "GET /browsers?agentSessionId=sess-run-1&filterBy=active"
        workspace, assignment = make_cloud_assignment(tmp / f"nocdp-{label.replace(' ', '-')}")
        server, fixtures = cloud_scenario(tmp, f"nocdp-{label.replace(' ', '-')}-fx", cloud_listing_output(), extra_replies={
            browsers_key: [{"status": 200, "body": {"items": items}}]})
        try:
            code, out = cloud_main(cloud_read_args(assignment), fixtures)
        finally:
            server.stop()
        record = json.loads((assignment / "data/cloud/c-001-zillow.json").read_text())
        check(f"cloud failures: {label} keeps the record with capture none", code == 1 and out.splitlines()[0] == page_not_saved
              and record["capture"] == "none" and len(record["rejected"]) == 13
              and {item["rule"] for item in record["rejected"]} == {"no-saved-page"}
              and not (assignment / "data/cloud/c-001-zillow.txt").exists()
              and not (assignment / "data/cloud/c-001-zillow.pdf").exists()
              and record["capture_note"] == expected_note
              and "cdp.example" not in record["capture_note"]
              and ("PATCH /browsers/br-run-1" in sent_keys(fixtures)) == expect_patch, (out, record))
        check(f"cloud failures: open.json is empty after {label}", open_is_empty(assignment))
    check("cloud failures: an https cdpUrl is opened as wss; other schemes are left alone",
          tool_module("cloud_read").websocket_address("https://cdp.example") == "wss://cdp.example"
          and tool_module("cloud_read").websocket_address("wss://cdp.example/x") == "wss://cdp.example/x"
          and tool_module("cloud_read").websocket_address("http://cdp.example") == "http://cdp.example")
    case = cloud_case(tmp, "crash-mid-capture", cloud_listing_output(), close_on_method="Page.printToPDF")
    check("cloud failures: a crash mid-capture is page not saved and the browser is stopped",
          case["code"] == 1 and case["out"].splitlines()[0] == page_not_saved and "PATCH /browsers/br-run-1" in case["keys"], case["out"])

    # left-over browsers
    dead_process = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_process.wait()
    old_replies = {"GET /browsers?agentSessionId=sess-old&filterBy=active": [{"status": 200, "body": {"items": [{"id": "br-old", "cdpUrl": None}]}}],
                   "PATCH /browsers/br-old": [{"status": 200, "body": {}}],
                   "DELETE /sessions/sess-old": [{"status": 204}],
                   "POST /runs/old/cancel": [{"status": 200, "body": {}}]}
    hostname = tool_module("cloud_read").socket.gethostname()

    def old_entry(pid, seconds_old):
        started = (datetime.now() - timedelta(seconds=seconds_old)).isoformat(timespec="seconds")
        return {"run_id": "old", "session_id": "sess-old", "pid": pid, "host": hostname, "started_at": started}

    for label, pid, seconds_old, swept in (("a dead process", dead_process.pid, 0, True),
                                           ("a live process", os.getpid(), 0, False),
                                           ("a live process, too old", os.getpid(), 481, True)):
        workspace, assignment = make_cloud_assignment(tmp / f"left-{label.replace(' ', '-').replace(',', '')}")
        write(assignment / "data/cloud/open.json", json.dumps([old_entry(pid, seconds_old)]))
        server, fixtures = cloud_scenario(tmp, f"left-{label.replace(' ', '-').replace(',', '')}-fx", cloud_listing_output(),
                                          extra_replies=old_replies)
        try:
            code, out = cloud_main(cloud_read_args(assignment), fixtures)
        finally:
            server.stop()
        keys = sent_keys(fixtures)
        remaining = json.loads((assignment / "data/cloud/open.json").read_text())
        if swept:
            ok = "PATCH /browsers/br-old" in keys and keys.index("PATCH /browsers/br-old") < keys.index("POST /runs") and remaining == []
        else:
            ok = "PATCH /browsers/br-old" not in keys and [entry["run_id"] for entry in remaining] == ["old"]
        check(f"cloud failures: an open entry of {label} is {'swept' if swept else 'left alone'}", code == 0 and ok, (keys, remaining))

    # page deletion for a county job
    card_page = fakes.FakePage("t1", "https://beacon.schneidercorp.com/Application.aspx?AppID=1", CLOUD_PAGE, CLOUD_PDF)
    quote_check = tool_module("quote_check")
    card_names = [field.name for field in quote_check.load_schema(PRODUCT / "tools/cloud_schemas/property-card.json")]
    card_found = {"living_sqft": {"value": 1620, "unit": "sqft", "quote": "1,620 sqft"},
                  "year_built": {"value": 1931, "quote": "Built in 1931"}}
    card_output = {"status": "done", "page_url": card_page.url, "injection_seen": False,
                   "fields": {name: card_found.get(name, {"not_found": True}) for name in card_names}}
    case = cloud_case(tmp, "card", card_output, pages=[card_page],
                      args=lambda a: cloud_read_args(a, site="Beacon", job="property-card", area="Westchester/Yonkers"))
    record = json.loads((case["assignment"] / "data/cloud/c-001-beacon.json").read_text())
    check("cloud failures: a property-card read keeps no page, only its address and hash",
          case["code"] == 0 and not (case["assignment"] / "data/cloud/c-001-beacon.txt").exists()
          and not (case["assignment"] / "data/cloud/c-001-beacon.pdf").exists()
          and record["page_sha256"] == hashlib.sha256(CLOUD_PAGE.encode()).hexdigest() and record["page_url"] == card_page.url
          and record["fields"]["living_sqft"]["quote"] == "" and record["fields"]["living_sqft"]["value"] == 1620, (case["out"], record))

    case = cloud_case(tmp, "injection", dict(cloud_listing_output(), injection_seen=True))
    check("cloud failures: injection_seen is logged with the read id",
          [(row["code"], row["detail"]) for row in case["errors"]] == [("INJECTION_SEEN", "c-001-zillow")], case["errors"])


CLOUD_REQUEST_FIELDS = ["sale_price", "sale_date", "beds", "baths", "living_sqft", "year_built"]


def write_cloud_request(assignment, request_id, **overrides):
    document = {"id": request_id, "kind": "cloud", "job": "listing-details", "area": "Queens", "site": "Zillow",
                "query": CLOUD_QUERY, "match": {}, "fields": CLOUD_REQUEST_FIELDS}
    document.update(overrides)
    write(assignment / f"data/extract/requests/{request_id}.json", json.dumps(document))


def test_cloud_run(tmp):
    fakes = cloud_fakes_module()
    workspace, assignment = make_cloud_assignment(tmp)
    ids = [f"x-00{number}-c0{number}-zillow" for number in range(1, 6)]
    for request_id in ids:
        write_cloud_request(assignment, request_id)
    pages = [fakes.FakePage(f"t{number}", f"https://www.zillow.com/homedetails/p{number}/{number}_zpid/", CLOUD_PAGE, CLOUD_PDF)
             for number in range(1, 6)]
    server = fakes.FakeCdpServer(pages)
    server.start()
    replies = {"POST /runs": []}
    for number, page in enumerate(pages, start=1):
        replies.update(fakes.happy_run(f"run-{number}", cloud_listing_output(page_url=page.url), server.url))
        replies["POST /runs"].append({"status": 200, "body": {"id": f"run-{number}", "sessionId": f"sess-run-{number}",
                                                                "workspaceId": f"ws-run-{number}"}})
    fixtures = fakes.write_replies(tmp / "five", replies)
    argv = ["run", "--assignment", str(assignment)] + [part for request_id in ids for part in ("--request", request_id)]
    try:
        code, out = cloud_main(argv, fixtures)
    finally:
        server.stop()
    keys = sent_keys(fixtures)
    lines = out.splitlines()
    check("cloud run: five requests give five done lines in request order", code == 0 and len(lines) == 5
          and all(line.startswith(f"done {request_id}:") for line, request_id in zip(lines, ids)), out)
    check("cloud run: five runs, five stopped browsers",
          keys.count("POST /runs") == 5 and len([key for key in keys if key.startswith("PATCH /browsers/")]) == 5, keys)
    check("cloud run: five rows, five records, open.json empty", len(csv_rows(assignment / "data/cloud/runs.csv")) == 5
          and all(json.loads((assignment / f"data/cloud/{request_id}.json").read_text())["status"] == "done" for request_id in ids)
          and open_is_empty(assignment))

    nothing = fakes.write_replies(tmp / "nothing", {})
    too_many = ["run", "--assignment", str(assignment)] + [part for number in range(6) for part in ("--request", f"x-00{number}-c0{number}-zillow")]
    code, out = cloud_main(too_many, nothing)
    check("cloud run: six requests are refused", code == 2 and "at most 5" in out and not (nothing / "sent.jsonl").exists(), out)

    write_cloud_request(assignment, "x-010-c10-zillow")
    bad_requests = [
        ("a browser request", "x-011-c11-zillow", {"kind": "browser"}, "is a browser request"),
        ("a client name as the query", "x-012-c12-zillow", {"query": "Maria Lopez"}, "--query"),
        ("a field the job does not have", "x-013-c13-zillow", {"fields": ["owner_name"]}, "owner_name"),
        ("an id that does not match its file", "x-014-c14-zillow", {"id": "x-099-c99-zillow"}, "does not hold id"),
        ("a Chrome-only site", "x-015-c15-zillow", {"site": "OneKey MLS"}, "read in his Chrome"),
    ]
    for label, request_id, overrides, wanted in bad_requests:
        write_cloud_request(assignment, request_id, **overrides)
        code, out = cloud_main(["run", "--assignment", str(assignment), "--request", "x-010-c10-zillow",
                                "--request", request_id], nothing)
        check(f"cloud run: {label} refuses the whole call with {wanted}",
              code == 2 and wanted in out and not (nothing / "sent.jsonl").exists(), out)

    # one done and one blocked
    write_cloud_request(assignment, "x-020-c20-zillow")
    write_cloud_request(assignment, "x-021-c21-zillow")
    page_one = fakes.FakePage("t1", CLOUD_ZILLOW_URL, CLOUD_PAGE, CLOUD_PDF)
    server = fakes.FakeCdpServer([page_one])
    server.start()
    replies = {"POST /runs": [{"status": 200, "body": {"id": "run-1", "sessionId": "sess-run-1", "workspaceId": "ws-run-1"}},
                              {"status": 200, "body": {"id": "run-2", "sessionId": "sess-run-2", "workspaceId": "ws-run-2"}}]}
    replies.update(fakes.happy_run("run-1", cloud_listing_output(), server.url))
    replies.update(fakes.happy_run("run-2", cloud_listing_output(status="blocked_login"), server.url))
    fixtures = fakes.write_replies(tmp / "mixed", replies)
    try:
        code, out = cloud_main(["run", "--assignment", str(assignment), "--request", "x-020-c20-zillow",
                                "--request", "x-021-c21-zillow"], fixtures)
    finally:
        server.stop()
    check("cloud run: a done and a blocked request exit 1 and print both lines",
          code == 1 and sorted(line.split()[0] for line in out.splitlines()) == ["blocked", "done"], out)

    code, out = cloud_main(["run", "--assignment", str(assignment), "--request", ids[0]], nothing)
    check("cloud run: a finished request is not read again", code == 2 and "was already read" in out, out)

    code, out = cloud_main(["show", "--assignment", str(assignment)])
    shown = out.splitlines()
    check("cloud run: show prints the header and one line per row", code == 0
          and shown[0] == "id  site  status  ok  rejected  cost  seconds" and len(shown) == 1 + 7, out)
    fresh_workspace, fresh_assignment = make_cloud_assignment(tmp / "fresh")
    code, out = cloud_main(["show", "--assignment", str(fresh_assignment)])
    check("cloud run: show on a new assignment says no cloud reads yet", code == 0 and out.strip() == "no cloud reads yet", out)

    key_workspace, key_assignment = make_cloud_assignment(tmp / "keyed")
    write(key_workspace / "settings/browser-use.key", "bu_test_KEY123\n")
    account_fixtures = fakes.write_replies(tmp / "account", {"GET /billing/account": [
        {"status": 200, "body": {"totalCreditsBalanceUsd": 12.5, "concurrentSessionLimit": 10}}]})
    previous_folder = Path.cwd()
    os.chdir(key_workspace)
    try:
        code, out = cloud_main(["check-key"], account_fixtures)
    finally:
        os.chdir(previous_folder)
    check("cloud run: check-key prints the credit and limit", code == 0
          and out.strip() == "browser-use key OK: $12.50 credit left; up to 10 browsers at once", out)
    key_server, key_fixtures = cloud_scenario(tmp, "keyed-read", cloud_listing_output())
    try:
        read_code, read_out = cloud_main(cloud_read_args(key_assignment), key_fixtures)
    finally:
        key_server.stop()
    check("cloud run: the key appears in no output", "KEY123" not in out + read_out + (key_fixtures / "sent.jsonl").read_text())
    code, out = run(CLOUD_READ, "show", "--assignment", assignment, cwd=tmp)
    check("cloud run: the script works under -I from any folder", code == 0 and out.startswith("id  site"), out)


def test_cloud_guard_and_export(tmp):
    ws = make_workspace(tmp / "guard-ws")
    key_path = ws / "settings/browser-use.key"
    for tool, path in (("Write", key_path), ("Edit", key_path), ("Write", ws / "Settings/Browser-Use.KEY")):
        reason = guard_reason(ws, tool, {"file_path": str(path)})
        check(f"cloud guard: {tool} to {path.name} is denied and names check-key",
              guard_action(ws, tool, {"file_path": str(path)}) == "deny" and "check-key" in reason, reason)
    for command in ("cat settings/browser-use.key", "cp settings/browser-use.key /tmp/k",
                    "python3 -c \"open('settings/browser-use.key')\""):
        check(f"cloud guard: bash {command[:30]!r} is denied",
              guard_action(ws, "Bash", {"command": command}) == "deny")
    check("cloud guard: bash cloud_read check-key is allowed",
          guard_action(ws, "Bash", {"command": "python3 -I /x/tools/cloud_read.py check-key"}) == "allow")
    check("cloud guard: a write to conventions.md is allowed",
          guard_action(ws, "Write", {"file_path": str(ws / "settings/conventions.md")}) == "allow")

    root = make_workspace(tmp / "export-ws")
    write(root / "settings/export.json", json.dumps({"name": "dcohen"}))
    write(root / "settings/browser-use.key", "bu_test_KEY123\n")
    (root / "exports").mkdir()
    asg = "2026-01-05_1-test-st_2a"
    assignment = root / "assignments" / asg
    copy_writable(PRODUCT / "templates/assignment", assignment)
    write(assignment / "subject.json", json.dumps({"address": "1 TEST ST", "unit": "2A", "bbl": "1000010001",
                                                   "billing_bbl": "1000017501", "effective_date": "2026-01-05",
                                                   "inspection_notes": "", "search": {"since": "2025-01-01"}, "pluto": {}}))
    run(LEDGER, "event", "--assignment", asg, "--stage", "6", "--event", "done", cwd=root)
    ledger = tool_module("ledger")
    cloud_files = {"data/cloud/c-001-zillow.json": '{"status": "done"}', "data/cloud/c-001-zillow.txt": "page text",
                   "data/cloud/c-001-zillow.pdf": "pdf", "data/cloud/open.json": "[]",
                   "data/cloud/runs.csv": ",".join(ledger.CLOUD_RUNS) + "\n"}
    for relative_path, content in cloud_files.items():
        write(assignment / relative_path, content)
    code, out = run(PRODUCT / "tools/export_learning.py", "build", cwd=root)
    pending = root / "exports/_pending" / date.today().isoformat()
    raw_cloud = pending / "raw/assignments" / asg / "data/cloud"
    check("cloud export: build passes", code == 0, out[-400:])
    check("cloud export: only runs.csv is kept from data/cloud",
          sorted(path.name for path in raw_cloud.iterdir()) == ["runs.csv"] if raw_cloud.is_dir() else False)
    check("cloud export: the key appears nowhere in the export",
          not any(b"KEY123" in path.read_bytes() for path in pending.rglob("*") if path.is_file()))
    check("cloud export: raw/settings holds only conventions.md",
          sorted(path.name for path in (pending / "raw/settings").iterdir()) == ["conventions.md"])
    previous_folder = Path.cwd()
    os.chdir(root)
    try:
        export_module = tool_module("export_learning")
    finally:
        os.chdir(previous_folder)
    made_paths = [Path(relative_path) for relative_path in cloud_files] + [Path("settings/browser-use.key")]
    for rule in export_module.EXCLUDED:
        matching = [path for path in made_paths if fnmatch.fnmatchcase(path.as_posix(), rule.pattern)]
        check(f"cloud export: the rule {rule.pattern} matches a path the test made and excludes it",
              bool(matching) and all(export_module.is_excluded(path) for path in matching))
    check("cloud export: runs.csv is not excluded", not export_module.is_excluded(Path("data/cloud/runs.csv")))
    check("cloud export: the README names data/cloud/runs.csv", "data/cloud/runs.csv" in (pending / "README.md").read_text())


def test_cloud_docs():
    browser_read = (PRODUCT / "skills/browser-read/SKILL.md").read_text()
    public_position = browser_read.find("## Public sites")
    public_section = browser_read[public_position:browser_read.find("\n## ", public_position + 1)]
    check("cloud docs: browser-read has Public sites before Sites", 0 <= public_position < browser_read.find("## Sites"))
    check("cloud docs: Public sites names cloud_read.py read and sources.py lookup",
          "cloud_read.py read" in public_section and "sources.py lookup" in public_section)
    for site_skill in ("zillow", "streeteasy"):
        skill_text = (PRODUCT / f"skills/{site_skill}/SKILL.md").read_text()
        lines = skill_text.splitlines()
        closing = lines.index("---", 1) if lines and lines[0] == "---" and "---" in lines[1:] else -1
        frontmatter = "\n".join(lines[1:closing]) if closing > 0 else ""
        body = "\n".join(lines[closing + 1:]) if closing > 0 else skill_text
        check(f"cloud docs: {site_skill} has frontmatter with name and description",
              f"name: {site_skill}" in frontmatter and "description:" in frontmatter, frontmatter)
        check(f"cloud docs: {site_skill} is under 80 lines", len(lines) < 80, len(lines))
        check(f"cloud docs: {site_skill} body names no browser tool, Chrome or script",
              not any(word in body for word in ("browser_", "mcp__", "Chrome", "cloud_read")), body)
    zillow_body = tool_module("cloud_read").skill_body("Zillow")
    check("cloud docs: skill_body finds the Zillow text", zillow_body != "" and not zillow_body.startswith("---"))
    setup_text = (PRODUCT / "skills/setup/SKILL.md").read_text()
    step_five = [line for line in setup_text.splitlines() if line.startswith("5.")]
    check("cloud docs: setup step 5 names check-key", bool(step_five) and "cloud_read.py check-key" in step_five[0], step_five)
    rules_text = (PRODUCT / "rules.md").read_text()
    check("cloud docs: rules name cloud_read.py and what is never sent",
          "cloud_read.py" in rules_text and "never client, borrower, lender or order details" in rules_text)
    check("cloud docs: the workspace README names browser-use",
          "browser-use" in (PRODUCT / "templates/workspace/README.md").read_text())
    check("cloud docs: Design Principles has the public-sites sentence",
          "Public sites are read in a cloud browser that is sent only an address and public identifiers."
          in (PRODUCT / "docs/Design Principles.md").read_text())
    check("cloud docs: Agent Workflow Plan has a cloud_read.py row",
          any(line.startswith("| `cloud_read.py` ") for line in (PRODUCT / "docs/Agent Workflow Plan.md").read_text().splitlines()))


def test_plan_area(tmp):
    rooms = write(tmp / "rooms.csv", "room,dims,subtract,flag,overlaps,px_w,px_d\n"
                  "Bedroom,10-6 x 16-3,,,,105,162.5\nLiving,12-0 x 20-0,4-0 x 6-0,,,120,200\nKitchen,8-0 x 10-0,,,,80,100\n")
    code, out = run(PLAN_AREA, rooms, "--listed", "420")
    exp = 10.5 * 16.25 + (12 * 20 - 4 * 6) + 80          # 466.625
    check("plan_area: labeled total", f"**{exp:.1f}**" in out, out[-300:])
    check("plan_area: scale check passes on a to-scale plan", "Scale check: 6 walls, PASS" in out, out[-300:])
    check("plan_area: no checks raised on a clean plan", "Checks that need attention" not in out and code == 0, out[-300:])

    bad = write(tmp / "bad.csv", "room,dims,subtract,flag,overlaps,px_w,px_d\n"
                "Bedroom,10-6 x 16-3,,,,105,162.5\nBedroom,10-6 x 16-3,,,,105,162.5\nKitchen,8-0 x 10-0,,,,95,100\n")
    code, out = run(PLAN_AREA, bad, "--listed", "900")
    check("plan_area: duplicate room flagged", "listed twice" in out)
    check("plan_area: scale mismatch flagged", "scale check: Kitchen" in out)
    check("plan_area: listed off >10% flagged", "more than 10%" in out)
    check("plan_area: confidence ceiling LOW when checks fail", "ceiling from checks: LOW" in out)

    inch = write(tmp / "inch.csv", "room,dims,subtract,flag,overlaps\nBath,5-14 x 8-0,,,\n")
    code, out = run(PLAN_AREA, inch)
    check("plan_area: 14 inches rejected with exit 1", code == 1 and ">= 12" in out, out[-200:])

    nopx = write(tmp / "nopx.csv", "room,dims,subtract,flag,overlaps\nBath,5-0 x 8-0,,,\n")
    code, out = run(PLAN_AREA, nopx)
    check("plan_area: missing pixels says 'not run'", "Scale check: not run" in out)

    flag = write(tmp / "flag.csv", "room,dims,subtract,flag,overlaps\nCloset,6-3 x 7-8,,confirm 7-8,\n")
    code, out = run(PLAN_AREA, flag)
    check("plan_area: one flagged digit caps confidence at MEDIUM", "ceiling from checks: MEDIUM" in out)

    empty = write(tmp / "empty.csv", "room,dims\n")
    code, out = run(PLAN_AREA, empty, "--listed", "700")
    check("plan_area: empty CSV reported, no crash", "no readable rooms" in out and "Traceback" not in out, out[-200:])


def test_plan_outline(tmp):
    rooms = write(tmp / "o_rooms.csv", "room,dims,subtract,flag,overlaps,px_w,px_d\n"
                  "Bedroom,10-6 x 16-3,,,,105,162.5\nLiving,12-0 x 20-0,4-0 x 6-0,,,120,200\nKitchen,8-0 x 10-0,,,,80,100\n")
    corners = lambda name, pts: write(tmp / name, "x,y\n" + "".join(f"{x},{y}\n" for x, y in pts))
    ok = corners("ok.csv", [(0, 0), (280, 0), (280, 200), (0, 200)])          # 28 x 20 ft = 560
    code, out = run(PLAN_AREA, rooms, "--outline", ok, "--convention", "outline")
    check("plan_outline: outline total", "(d) Outline total: 560 sq ft" in out, out[-400:])
    check("plan_outline: (c) is the outline", "(c) Total under convention 'outline': 560 sq ft" in out)
    check("plan_outline: unlabeled share shown", "Unlabeled space: 93 sq ft (16.7% of outline)" in out)
    check("plan_outline: clean outline raises nothing", "Checks that need attention" not in out)

    code, out = run(PLAN_AREA, rooms, "--outline", corners("big.csv", [(0, 0), (300, 0), (300, 200), (0, 200)]))
    check("plan_outline: unlabeled over 20% flagged", "more than 20%" in out)
    code, out = run(PLAN_AREA, rooms, "--outline", corners("small.csv", [(0, 0), (200, 0), (200, 200), (0, 200)]))
    check("plan_outline: outline smaller than rooms flagged", "smaller than the labeled room total" in out)
    code, out = run(PLAN_AREA, rooms, "--outline", tmp / "small.csv", "--convention", "outline")
    check("plan_outline: outline smaller than rooms is not measured",
          "(d) Outline: not measured" in out and "(labeled only, outline not measured)" in out, out[-400:])
    code, out = run(PLAN_AREA, rooms, "--outline", corners("bow.csv", [(0, 0), (300, 200), (300, 0), (0, 200)]))
    check("plan_outline: self-crossing flagged", "crosses itself" in out)
    code, out = run(PLAN_AREA, rooms, "--outline", corners("tri.csv", [(0, 0), (300, 0), (0, 200)]))
    check("plan_outline: fewer than 4 corners flagged", "at least 4 corners" in out)
    bad = write(tmp / "badxy.csv", "x,y\n0,0\n12O,0\n120,200\n0,200\n")
    code, out = run(PLAN_AREA, rooms, "--outline", bad)
    check("plan_outline: non-numeric corner reported", "line 3" in out and "Traceback" not in out, out[-300:])
    closed_loop = corners("closed.csv", [(0, 0), (280, 0), (280, 200), (0, 200), (0, 0)])
    code, out = run(PLAN_AREA, rooms, "--outline", closed_loop)
    check("plan_outline: repeated first corner at the end is accepted", "(d) Outline total: 560 sq ft" in out
          and "crosses itself" not in out, out[-400:])

    nopx = write(tmp / "o_nopx.csv", "room,dims,subtract,flag,overlaps\nBath,5-0 x 8-0,,,\n")
    code, out = run(PLAN_AREA, nopx, "--outline", ok, "--convention", "outline")
    check("plan_outline: no scale means not measured", "(d) Outline: not measured" in out)
    check("plan_outline: falls back to labeled, LOW",
          "(c) Total under convention 'outline': 40 sq ft (labeled only, outline not measured)" in out
          and "ceiling from checks: LOW" in out, out[-400:])


def test_plan_sketch(tmp):
    rows = ("floor,area_type,side,length,direction,flag\n"
            "1st,finished,1,30-0,R,\n1st,finished,2,20-0,D,\n1st,finished,3,30-0,L,\n1st,finished,4,20-0,U,\n"
            "2nd,finished,1,20-0,R,\n2nd,finished,2,10-0,D,\n2nd,finished,3,10-0,L,\n2nd,finished,4,10-0,D,\n"
            "2nd,finished,5,10-0,L,\n2nd,finished,6,20-0,U,\n"
            "Garage,garage,1,10-0,R,\nGarage,garage,2,20-0,D,\nGarage,garage,3,10-0,L,\nGarage,garage,4,20-0,U,\n")
    sketch = write(tmp / "sketch.csv", rows)
    attic = write(tmp / "attic.csv", "floor,dims,pct_7ft_plus,dims_5ft_plus\n"
                  "Attic A,20-0 x 30-0,60,12-0 x 30-0\nAttic B,10-0 x 10-0,40,6-0 x 10-0\n")
    code, out = run(PLAN_AREA, "--sketch", sketch, "--attic", attic, "--convention", "outline")
    check("plan_sketch: rectangle floor", "Floor 1st (finished): 600 sq ft" in out, out[-500:])
    check("plan_sketch: L-shaped floor", "Floor 2nd (finished): 300 sq ft" in out)
    check("plan_sketch: garage excluded and listed", "Excluded: Garage (garage) 200 sq ft" in out)
    check("plan_sketch: attic over 50% counts the 5 ft area", "Attic Attic A: 360 sq ft counted" in out)
    check("plan_sketch: attic at 40% excluded", "Attic Attic B: excluded (40% at 7 ft or more" in out)
    check("plan_sketch: total = finished floors + attic", "(c) Total under convention 'outline': 1260 sq ft" in out)
    check("plan_sketch: no rooms CSV needed", "no readable rooms" not in out and code == 0)

    bad_attic = write(tmp / "attic_bad.csv", "floor,dims,pct_7ft_plus,dims_5ft_plus\n"
                      "Attic C,10-0 x 10-0,600,6-0 x 10-0\nAttic D,10-0 x 10-0,60,40-0 x 40-0\n")
    code, out = run(PLAN_AREA, "--sketch", sketch, "--attic", bad_attic, "--convention", "outline")
    check("plan_sketch: attic percent over 100 not measured", "Attic Attic C: not measured" in out, out[-500:])
    check("plan_sketch: attic 5 ft area larger than the attic not measured", "Attic Attic D: not measured" in out
          and "(d) Outline: not measured" in out, out[-500:])

    gap = write(tmp / "gap.csv", "floor,area_type,side,length,direction,flag\n"
                "1st,finished,1,30-0,R,\n1st,finished,2,20-0,D,\n1st,finished,3,29-0,L,\n1st,finished,4,20-0,U,\n")
    code, out = run(PLAN_AREA, "--sketch", gap)
    check("plan_sketch: open walk is not measured", "Floor 1st: not measured (does not close: 1.0 ft gap)" in out, out[-300:])
    angled = write(tmp / "angled.csv", "floor,area_type,side,length,direction,flag\n"
                   "1st,finished,1,30-0,R,\n1st,finished,2,20-0,NE,\n1st,finished,3,30-0,L,\n1st,finished,4,20-0,U,\n")
    code, out = run(PLAN_AREA, "--sketch", angled)
    check("plan_sketch: angled side flagged", "side 2" in out and "angled" in out and "not measured" in out)
    zero = write(tmp / "zero.csv", "floor,area_type,side,length,direction,flag\n"
                 "1st,finished,1,0-0,R,\n1st,finished,2,20-0,D,\n1st,finished,3,0-0,L,\n1st,finished,4,20-0,U,\n")
    code, out = run(PLAN_AREA, "--sketch", zero)
    check("plan_sketch: zero-length side rejected", "side 1" in out and "length" in out and "not measured" in out)


def make_bracket_case(root, name, subject, rows):
    a = root / name
    header = ("candidate_id,rank,exclusion_reason,listed_or_measured_sqft,size_basis,beds,baths,floor,"
              "lot_sqft,year_built,units,uad_condition,uad_quality,proposed\n")
    write(a / "subject.json", json.dumps(subject))
    write(a / "data/ranking.csv", header + "".join(r + "\n" for r in rows))
    return a


def test_bracket(tmp):
    condo = make_bracket_case(tmp, "condo", {
        "building_type": "condo", "interior_sqft_stated": 900, "sqft_source": "measured",
        "beds": 2, "baths": 2, "floor": 5, "uad_condition": "C3", "uad_quality": "", "pluto": {}}, [
        "c01,1,,850,measured,2,2,3,,,,C3,,", "c02,2,,880,measured,2,1,4,,,,C4,,",
        "c03,3,,870,measured,2,2,2,,,,C3,,", "c04,4,package_sale,1000,measured,2,2,9,,,,C3,,",
        "c05,5,,950,measured,2,2,8,,,,C3,,", "c06,6,,920,measured,3,2.5,6,,,,C2,,"])
    code, out = run(BRACKET, "propose", "--assignment", condo)
    proposed = {r["candidate_id"]: r["proposed"] for r in csv.DictReader(open(condo / "data/ranking.csv"))}
    check("bracket: top 3 plus the gap closer", proposed == {"c01": "Y", "c02": "Y", "c03": "Y", "c04": "N",
                                                              "c05": "Y", "c06": "N"}, f"{proposed}\n{out[-400:]}")
    check("bracket: size bracketed", "| size | 900 | 850 | 950 | measured | bracketed |" in out, out[-600:])
    check("bracket: unknown subject quality can't check", "| quality |" in out and "can't check" in out)
    check("bracket: bracket.md written", (condo / "data/bracket.md").exists())
    code, out = run(BRACKET, "check", "--assignment", condo, "--comps", "c01,c02")
    check("bracket: check mode reports a gap without proposing",
          "not bracketed: nothing above in pool" in out
          and proposed == {r["candidate_id"]: r["proposed"] for r in csv.DictReader(open(condo / "data/ranking.csv"))})

    house = make_bracket_case(tmp, "house", {
        "building_type": "single-family", "gla_sqft": 1500, "gla_source": "sketch", "lot_sqft": 4000,
        "beds": 3, "baths": 2, "uad_condition": "", "uad_quality": "", "pluto": {"yearbuilt": "1930"}}, [
        "c01,1,,1400,public record,,,,3000,1925,,,,", "c02,2,,1600,public record,,,,3500,1935,,,,",
        "c03,3,,1550,public record,,,,2500,1940,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", house)
    check("bracket: public-record size is unverified, mixed basis",
          "mixed basis" in out and "bracketed (unverified)" in out, out[-600:])
    check("bracket: lot gap named", "| lot |" in out and "nothing above in pool" in out)
    check("bracket: unknown comp beds can't check", "| beds |" in out and "can't check" in out)

    short = make_bracket_case(tmp, "short", {"building_type": "condo", "interior_sqft_stated": 900,
                                              "sqft_source": "measured", "pluto": {}},
                              ["c01,1,,850,measured,,,,,,,,,", "c02,2,,950,measured,,,,,,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", short)
    check("bracket: short pool proposes all and says so", "only 2 eligible candidates" in out and code == 0, out)
    halfs = make_bracket_case(tmp, "halfs", {"building_type": "condo", "baths": 2, "pluto": {}},
                              ["c01,1,,850,measured,2,2.1,,,,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", halfs)
    check("bracket: bath 2.1 refused with convention", code == 2 and "half-bath convention" in out, out)
    extra = make_bracket_case(tmp, "extra", {"building_type": "condo", "pluto": {}},
                              ["c01,1,,850,measured,,,,,,,,,", "c02,2,,950,measured,,,,,,,,,,EXTRA"])
    code, out = run(BRACKET, "propose", "--assignment", extra)
    kept = list(csv.DictReader(open(extra / "data/ranking.csv")))
    check("bracket: row with extra cells refused, ranking.csv kept",
          code == 2 and "line 3" in out and "Traceback" not in out and len(kept) == 2, out)
    year0 = make_bracket_case(tmp, "year0", {"building_type": "single-family", "pluto": {"yearbuilt": "1930"}},
                              ["c01,1,,,,,,,,1940,,,,", "c02,2,,,,,,,,1950,,,,", "c03,3,,,,,,,,1945,,,,",
                               "c04,4,,,,,,,,0,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", year0)
    check("bracket: year built 0 is unknown, not a bracket", "| year_built | 1930 | 1940 | 1950 | - | not bracketed" in out,
          out[-600:])
    notype = make_bracket_case(tmp, "notype", {"pluto": {}}, ["c01,1,,850,measured,,,,,,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", notype)
    check("bracket: missing building_type named", code == 2 and "building_type" in out and "2-4 family" in out, out)

    nogla = make_bracket_case(tmp, "nogla", {
        "building_type": "single-family", "gla_sqft": None, "interior_sqft_stated": 1450, "sqft_source": "listing",
        "pluto": {}}, ["c01,1,,1400,MLS,,,,,,,,,", "c02,2,,1600,MLS,,,,,,,,,", "c03,3,,1500,MLS,,,,,,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", nogla)
    check("bracket: house without gla_sqft shows size can't check", "| size | not in source |" in out
          and "can't check" in out.split("| size |")[1].split("\n")[0], out[-600:])
    same_basis = make_bracket_case(tmp, "same_basis", {
        "building_type": "single-family", "gla_sqft": 1500, "gla_source": "sketch (plans/subject_result.md)",
        "pluto": {}}, ["c01,1,,1400,sketch,,,,,,,,,", "c02,2,,1600,sketch,,,,,,,,,", "c03,3,,1550,sketch,,,,,,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", same_basis)
    check("bracket: subject source text reduced to its basis",
          "| size | 1500 | 1400 | 1600 | sketch | bracketed |" in out, out[-600:])
    listing = make_bracket_case(tmp, "listing", {
        "building_type": "condo", "interior_sqft_stated": 900, "sqft_source": "listing 11/15", "pluto": {}},
        ["c01,1,,850,listed,,,,,,,,,", "c02,2,,950,listed,,,,,,,,,", "c03,3,,900,listed,,,,,,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", listing)
    check("bracket: 'listing' source counts as the listed basis",
          "| size | 900 | 850 | 950 | listed | bracketed |" in out, out[-600:])
    inside = make_bracket_case(tmp, "inside", {
        "building_type": "single-family", "gla_sqft": 1500, "gla_source": "sketch", "pluto": {}},
        ["c01,1,,1400,plan inside face,,,,,,,,,", "c02,2,,1600,plan inside face,,,,,,,,,",
         "c03,3,,1550,plan inside face,,,,,,,,,"])
    code, out = run(BRACKET, "propose", "--assignment", inside)
    check("bracket: house plan traced to the inside face is unverified", "bracketed (unverified)" in out, out[-600:])
    added = make_bracket_case(tmp, "added", {"building_type": "condo", "interior_sqft_stated": 900,
                                              "sqft_source": "measured", "pluto": {}},
                              ["c01,1,,850,measured,,,,,,,,,", "c02,2,,880,measured,,,,,,,,,",
                               "c03,,,950,measured,,,,,,,,,"])
    code, out = run(BRACKET, "check", "--assignment", added, "--comps", "c01,c02,c03")
    check("bracket: check accepts an appraiser-added comp with no rank",
          code == 0 and "| size | 900 | 850 | 950 | measured | bracketed |" in out, out[-600:])


def test_nyc_sales():
    sys.dont_write_bytecode = True        # keep __pycache__ out of product/
    spec = importlib.util.spec_from_file_location("nyc_sales", NYC)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    check("nyc_sales: BBL parsed", m.parse_bbl("1-00441-0012") == ("1", 441, 12))
    check("nyc_sales: addresses normalised", m.norm_street("425 East 13th Street, C") == "425 E 13 ST")
    deed = lambda amt, d, pct="100": {"document_amt": str(amt), "document_date": d + "T00:00:00", "percent_trans": pct}
    cases = [
        ("MATCH", [deed(1692000, "2025-03-10")]),
        ("MISMATCH", [deed(1600000, "2025-03-14")]),
        ("DATE_DIFF", [deed(1692000, "2024-06-01")]),
        ("ZERO_DEED", [deed(0, "2025-03-14")]),
        ("PARTIAL", [deed(1692000, "2025-03-14", "50")]),
        ("MISMATCH", []),
        ("MATCH", [deed(10, "2025-03-14"), deed(1692000, "2025-03-12")]),   # picks the matching deed
    ]
    for want, docs in cases:
        got, detail = m.deed_check(docs, 1692000, "2025-03-14")
        check(f"nyc_sales: deed check {want} ({len(docs)} deed(s))", got == want, f"got {got}: {detail}")
    code, out = run(NYC, "acris", "--bbl", "1004411203", "--expect-price", "5")
    check("nyc_sales: --expect-price without --expect-date refused", code == 2 and "both" in out, out)
    cls = "building_class_at_time_of"
    sale = lambda cat, bc="": {"building_class_category": cat, cls: bc}
    check("nyc_sales: one-family kept for houses", m.keep_sale(sale("01 ONE FAMILY DWELLINGS"), "house"))
    check("nyc_sales: four-family C3 kept for houses", m.keep_sale(sale("07 RENTALS - WALKUP APARTMENTS", "C3"), "house"))
    check("nyc_sales: other walk-up refused for houses", not m.keep_sale(sale("07 RENTALS - WALKUP APARTMENTS", "C1"), "house"))
    check("nyc_sales: co-op refused for houses", not m.keep_sale(sale("09 COOPS - WALKUP APARTMENTS"), "house"))
    dof = m.dof_fields({"land_square_feet": "2,021", "gross_square_feet": "0", "residential_units": "1",
                        "year_built": "0", cls: "A5"})
    check("nyc_sales: DOF zeros blank, commas dropped",
          dof == {"building_class": "A5", "land_sqft": "2021", "gross_sqft": "", "residential_units": "1",
                  "year_built": ""}, dof)
    check("nyc_sales: co-op kept for apartments", m.keep_sale(sale("10 COOPS - ELEVATOR APARTMENTS"), "apartment"))
    code, out = run(NYC, "sales", "--bbl", "1004410012", "--since", "2025-01-01", "--out", "/tmp/x")
    check("nyc_sales: sales without --type refused", code == 2 and "--type" in out, out)


def test_scorer(tmp):
    cal = write(tmp / "cal.csv", "plan_id,plan_file,plan_type,manual_sqft,notes\np1,a.png,full,1000,\np2,b.png,schematic,800,\n")
    runs = write(tmp / "runs.csv", "plan_id,run,model,measured_sqft,confidence,date\n"
                 "p1,1,m,1010,HIGH,2026-11-16\np1,2,m,995,HIGH,2026-11-16\np1,3,m,1020,HIGH,2026-11-16\n"
                 "p2,1,m,800,MEDIUM,2026-11-16\np2,2,m,900,HIGH,2026-11-16\np2,3,m,805,LOW,2026-11-16\n")
    code, out = run(SCORER, cal, runs, "--tol-pct", "3")
    check("scorer: pass^k counts only plans right every run", "pass^k 1/2" in out, out[-300:])
    check("scorer: pass@k counts plans right at least once", "pass@k 2/2" in out)
    check("scorer: wrong-but-HIGH runs listed", "p2 run 2" in out)


def make_workspace(folder):
    write(folder / "settings/conventions.md", "# Conventions\n")
    (folder / "shared/buildings").mkdir(parents=True, exist_ok=True)
    return folder


def copy_writable(source, target):
    """Copy a folder with contents only, so the copy is writable even when the
    source is a read-only install (as the desktop app's plugin cache is)."""
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
                    copy_function=shutil.copyfile)
    set_tree_writable(target, True)


def copy_plugin(into):
    """Copy product/ to `into`, as a standalone plugin install, and return ledger.py's path."""
    copy_writable(PRODUCT, into)
    return into / "tools/ledger.py"


def test_workspace(tmp):
    plugin = tmp / "cache/appraisal-comps"
    led = copy_plugin(plugin)
    ws = make_workspace(tmp / "Appraisal Comps")
    code, out = run(led, "check", cwd=ws / "shared/buildings")
    check("workspace: found from a subfolder", code == 0, out)
    code, out = run(led, "check", cwd=tmp)
    check("workspace: outside the workspace refused", code == 2 and "no Appraisal Comps folder here" in out, out)
    version = json.loads((PRODUCT / ".claude-plugin/plugin.json").read_text())["version"]
    asg = ws / "assignments/2026-01-05_1-test-st_2a"
    copy_writable(PRODUCT / "templates/assignment", asg)
    run(led, "event", "--assignment", asg, "--stage", "1", "--event", "start", cwd=ws)
    check("workspace: version comes from plugin.json", f",{version}," in (asg / "events.csv").read_text())
    check("workspace: nothing written inside the plugin", not (plugin / "assignments").exists()
          and not (plugin / "shared").exists())


def test_workspace_host_paths(tmp):
    led = copy_plugin(tmp / "cache/appraisal-comps")
    hostws = make_workspace(tmp / "host-mapped-workspace")
    code, out = run(led, "check", cwd=tmp, env={"CLAUDE_CODE_WORKSPACE_HOST_PATHS": str(hostws)})
    check("workspace: found via CLAUDE_CODE_WORKSPACE_HOST_PATHS", code == 0, out)


def test_workspace_mnt(tmp):
    led = copy_plugin(tmp / "cache/appraisal-comps")
    fake_home = tmp / "fake-home"
    make_workspace(fake_home / "mnt" / "selected-folder")
    (fake_home / "mnt" / "not-a-workspace").mkdir(parents=True, exist_ok=True)
    code, out = run(led, "check", cwd=tmp, env={"HOME": str(fake_home)})
    check("workspace: found via ~/mnt/*", code == 0, out)


def test_workspace_several(tmp):
    led = copy_plugin(tmp / "cache/appraisal-comps")
    ws1 = make_workspace(tmp / "ws-one")
    ws2 = make_workspace(tmp / "ws-two")
    host_paths_value = os.pathsep.join([str(ws1), str(ws2)])
    code, out = run(led, "check", cwd=tmp, env={"CLAUDE_CODE_WORKSPACE_HOST_PATHS": host_paths_value})
    check("workspace: several workspaces named and refused", code == 2 and str(ws1) in out and str(ws2) in out, out)


def test_setup(tmp):
    target = tmp / "fresh"
    code, out = run(PRODUCT / "tools/setup_workspace.py", "--into", target)
    check("setup: creates the workspace", code == 0 and (target / "settings/conventions.md").exists(), out)
    # No site list lives in his folder (any site is open to the browser).
    check("setup: no settings/sites.json", not (target / "settings/sites.json").exists(), out)
    write(target / "settings/conventions.md", "his edits\n")
    code, out = run(PRODUCT / "tools/setup_workspace.py", "--into", target)
    check("setup: never overwrites", (target / "settings/conventions.md").read_text() == "his edits\n"
          and "kept" in out, out)


def unwritable_paths(folder):
    """Every file and folder under folder (and folder itself) that can't be written."""
    paths = [folder, *folder.rglob("*")]
    return [str(path.relative_to(folder)) for path in paths if not os.access(path, os.W_OK)]


def set_tree_writable(folder, writable):
    """Add or remove write permission on folder and everything in it, like an app's read-only install."""
    for path in [folder, *folder.rglob("*")]:
        mode = path.stat().st_mode
        if writable:
            path.chmod(mode | stat.S_IWUSR)
        else:
            path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def test_read_only_install(tmp):
    """The desktop app installs the plugin read-only. Files copied out of it
    into the workspace must still be writable."""
    plugin = tmp / "cache/appraisal-comps"
    copy_plugin(plugin)
    set_tree_writable(plugin, False)
    try:
        ws = tmp / "Appraisal Comps"
        code, out = run(plugin / "tools/setup_workspace.py", "--into", ws)
        check("read-only install: setup succeeds", code == 0, out)
        check("read-only install: workspace files writable", unwritable_paths(ws) == [], unwritable_paths(ws))

        asg = "2026-01-05_1-test-st_2a"
        code, out = run(plugin / "tools/new_assignment.py", "--name", asg, cwd=ws)
        check("read-only install: new_assignment succeeds", code == 0 and asg in out, out)
        check("read-only install: assignment files writable",
              (ws / "assignments" / asg / "progress.md").is_file()
              and unwritable_paths(ws / "assignments" / asg) == [], unwritable_paths(ws / "assignments" / asg))
        code, out = run(plugin / "tools/ledger.py", "event", "--assignment", asg, "--stage", "1",
                        "--event", "start", cwd=ws)
        check("read-only install: ledger writes the new assignment's events.csv",
              code == 0 and (ws / "assignments" / asg / "events.csv").is_file(), out)

        code, out = run(plugin / "tools/new_assignment.py", "--name", asg, cwd=ws)
        check("new_assignment: existing folder refused", code == 2 and "already exists" in out, out)
        code, out = run(plugin / "tools/new_assignment.py", "--name", "../escape", cwd=ws)
        check("new_assignment: name with a path refused", code == 2 and not (tmp / "escape").exists(), out)
        code, out = run(plugin / "tools/new_assignment.py", "--name", "1 Test St", cwd=ws)
        check("new_assignment: name without a date refused", code == 2 and "YYYY-MM-DD" in out, out)
    finally:
        set_tree_writable(plugin, True)


def test_session_start(tmp):
    plugin = tmp / "sp/appraisal-comps"
    copy_writable(PRODUCT, plugin)
    hook = plugin / "hooks/session_start.py"
    env = {"APPRAISAL_COMPS_TEST_LATEST": str(tmp / "missing.json")}
    ws = make_workspace(tmp / "sp/ws")

    code, out = run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(tmp / "sp")}, cwd=tmp / "sp", env=env)
    check("session: outside workspace says NO_WORKSPACE, writes nothing",
          "NO_WORKSPACE" in session_context(out) and not (tmp / "sp/settings").exists(), out[-300:])

    code, out = run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(ws)}, cwd=ws, env=env)
    context = session_context(out)
    check("session: rules loaded with the real plugin root",
          "${CLAUDE_PLUGIN_ROOT}" not in context and str(plugin) in context, context[:300])
    check("session: first run says NEW_VERSION", f"NEW_VERSION {CURRENT_VERSION}" in context)
    state = json.loads((ws / "settings/plugin-state.json").read_text())
    check("session: last_seen_version recorded", state["last_seen_version"] == CURRENT_VERSION)

    write(plugin / "migrations/0_5_0_boom.py", "def migrate(root):\n    raise RuntimeError('boom')\n")
    write(ws / "settings/plugin-state.json", json.dumps({"last_seen_version": "0.4.0"}))
    code, out = run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(ws)}, cwd=ws, env=env)
    check("session: failed migration reported", "MIGRATION_FAILED 0_5_0_boom" in session_context(out))
    code, out = run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(ws)}, cwd=ws, env=env)
    check("session: failed migration retries next session", "MIGRATION_FAILED 0_5_0_boom" in session_context(out)
          and json.loads((ws / "settings/plugin-state.json").read_text())["last_seen_version"] == "0.4.0")

    latest = write(tmp / "latest.json", json.dumps({"version": NEWER_VERSION}))
    fresh = make_workspace(tmp / "sp/ws2")
    env_ready = {"APPRAISAL_COMPS_TEST_LATEST": str(latest)}
    code, out = run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(fresh)}, cwd=fresh, env=env_ready)
    check("update: newer version offered", f"UPDATE_READY {NEWER_VERSION}" in session_context(out), out[-300:])
    asg = fresh / "assignments/2026-01-05_1-test-st_2a"
    copy_writable(PRODUCT / "templates/assignment", asg)
    write(asg / "events.csv", f"timestamp,stage,event,version,note\n2026-01-05T10:00:00,1,start,{CURRENT_VERSION},\n")
    latest.write_text(json.dumps({"version": NEXT_NEWER_VERSION}))
    write(fresh / "settings/plugin-state.json", json.dumps({"last_seen_version": CURRENT_VERSION}))
    code, out = run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(fresh)}, cwd=fresh, env=env_ready)
    check("update: open job warned, not blocked",
          f"UPDATE_READY {NEXT_NEWER_VERSION}" in session_context(out) and "finish it first" in session_context(out))
    fails = make_workspace(tmp / "sp/ws3")
    first = session_context(run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(fails)}, cwd=fails, env=env)[1])
    second = session_context(run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(fails)}, cwd=fails, env=env)[1])
    # "CHECK_FAILED " (with the trailing space before the reason) rather than
    # the bare code name, so this doesn't match rules.md's own table row
    # documenting the code (`CHECK_FAILED` with no reason after it).
    check("update: CHECK_FAILED once a day", "CHECK_FAILED " in first and "CHECK_FAILED " not in second)


def test_session_start_host_paths(tmp):
    """A hook whose input cwd has no workspace (the desktop app's own cwd,
    /private/var/empty) still finds the workspace through
    CLAUDE_CODE_WORKSPACE_HOST_PATHS, so it must not say NO_WORKSPACE."""
    plugin = tmp / "sp/appraisal-comps"
    copy_writable(PRODUCT, plugin)
    hook = plugin / "hooks/session_start.py"
    empty = tmp / "sp/empty"
    empty.mkdir(parents=True, exist_ok=True)
    ws = make_workspace(tmp / "sp/ws")
    env = {"APPRAISAL_COMPS_TEST_LATEST": str(tmp / "missing.json"),
           "CLAUDE_CODE_WORKSPACE_HOST_PATHS": str(ws)}

    code, out = run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(empty)}, cwd=empty, env=env)
    context = session_context(out)
    check("session: host-path env finds the workspace when hook cwd has none",
          f"NEW_VERSION {CURRENT_VERSION}" in context and "no Appraisal Comps folder found" not in context, context[:300])


RULES_MARKER = "Assistant to a licensed NYC residential appraiser"  # rules.md's first line of text


def session_plugin(tmp):
    """A copy of product/ installed as a plugin under tmp, and its SessionStart hook."""
    plugin = tmp / "sp/appraisal-comps"
    copy_writable(PRODUCT, plugin)
    return plugin / "hooks/session_start.py"


def run_session(hook, ws, env):
    return session_context(run_hook(hook, {"hook_event_name": "SessionStart", "cwd": str(ws)}, cwd=ws, env=env)[1])


def test_session_start_resilience(tmp):
    """Final review I3: the rules always come first; a bad plugin-state.json
    or a failure in the status work only adds a line after them."""
    from datetime import date
    hook = session_plugin(tmp)
    env = {"APPRAISAL_COMPS_TEST_LATEST": str(tmp / "missing.json")}

    ws = make_workspace(tmp / "sp/ws-bad-seen")
    write(ws / "settings/plugin-state.json", json.dumps({"last_seen_version": 5}))
    context = run_session(hook, ws, env)
    check("session: non-string last_seen_version still gives the rules",
          RULES_MARKER in context and f"NEW_VERSION {CURRENT_VERSION}" in context and "ERROR: session_start.py failed" not in context, context[-400:])

    ws = make_workspace(tmp / "sp/ws-empty-latest")
    write(ws / "settings/plugin-state.json", json.dumps({
        "last_seen_version": CURRENT_VERSION, "last_check_date": date.today().isoformat(),
        "latest_version": "", "last_check_result": "ok"}))
    context = run_session(hook, ws, env)
    check("session: empty cached latest_version still gives the rules",
          RULES_MARKER in context and "ERROR: session_start.py failed" not in context and "CHECK_FAILED " in context, context[-400:])

    ws = make_workspace(tmp / "sp/ws-error")
    (ws / "settings/plugin-state.json").mkdir()   # unwritable: writing the state fails
    context = run_session(hook, ws, env)
    check("session: rules text survives a session_start error",
          context.startswith("## Role") and RULES_MARKER in context and "\nERROR: session_start.py failed:" in context,
          context[-400:])


def test_session_ignores_old_sites_json(tmp):
    """There is no site list, so the session start has nothing to report
    about one: a settings/sites.json left in an older
    folder, even a broken one, is ignored, and no handoff file is written."""
    hook = session_plugin(tmp)
    env = {"APPRAISAL_COMPS_TEST_LATEST": str(tmp / "missing.json")}
    ws = make_workspace(tmp / "sp/ws")
    write(ws / "settings/sites.json", '{"sites": [\n  {"name": "MLS",}\n]}')
    context = run_session(hook, ws, env)
    check("session: no BROWSER_OFF line, even with a broken leftover sites.json",
          "BROWSER_OFF" not in context, context[-400:])
    plugin_data = tmp / "sp/plugin-data"
    run_session(hook, ws, {**env, "CLAUDE_PLUGIN_DATA": str(plugin_data)})
    check("session: no workspace handoff file is written", not (plugin_data / "workspace-path.txt").exists(),
          sorted(plugin_data.glob("*")) if plugin_data.exists() else [])


def test_scorer_workspace_defaults(tmp):
    """Final review I4: with no file arguments the scorer reads the
    workspace's shared/evals/ files, so the command never names calibration.csv."""
    ws = make_workspace(tmp / "ws")
    write(ws / "shared/evals/calibration.csv",
          "plan_id,plan_file,plan_type,manual_sqft,notes\np1,a.png,full,1000,\np2,b.png,schematic,800,\n")
    write(ws / "shared/evals/runs.csv", "plan_id,run,model,measured_sqft,confidence,date\n"
          "p1,1,m,1010,HIGH,2026-11-16\np2,1,m,900,HIGH,2026-11-16\n")
    code, out = run(SCORER, "--tol-pct", "3", cwd=ws / "shared")
    check("scorer: no file args scores the workspace's eval files",
          code == 0 and "pass^k 1/2" in out and "p2 run 1" in out, out[-300:])
    code, out = run(SCORER, "--tol-pct", "3", cwd=tmp)
    check("scorer: no file args outside a workspace refused",
          code == 2 and "no Appraisal Comps folder here" in out, out[-300:])


def test_guard_final_fixes(tmp):
    """The matcher has no web tools (open network)."""
    ws = make_workspace(tmp / "ws")
    hooks = json.loads((PRODUCT / "hooks/hooks.json").read_text())
    matcher = hooks["hooks"]["PreToolUse"][0]["matcher"]
    check("guard: hooks.json matcher has no WebSearch", "WebSearch" not in matcher.split("|"), matcher)
    check("guard: hooks.json matcher has no WebFetch", "WebFetch" not in matcher.split("|"), matcher)


def test_skill_paths():
    import re
    pattern = re.compile(r"\$\{(CLAUDE_SKILL_DIR|CLAUDE_PLUGIN_ROOT)\}/([\w./-]+)")
    for skill_md in sorted(PRODUCT.glob("skills/*/SKILL.md")):
        text = skill_md.read_text()
        check(f"skill paths: {skill_md.parent.name} has no product/ path", "product/" not in text)
        for variable, relative in pattern.findall(text):
            base = skill_md.parent if variable == "CLAUDE_SKILL_DIR" else PRODUCT
            check(f"skill paths: {skill_md.parent.name} -> {relative} exists", (base / relative).exists())
def test_scorer_close(tmp):
    cal = write(tmp / "cal_c.csv", "plan_id,plan_file,plan_type,manual_sqft,notes\np3,c.png,full,1000,\n")
    runs = write(tmp / "runs_c.csv", "plan_id,run,model,measured_sqft,confidence,date\n"
                 "p3,1,m,1040,HIGH,2026-11-17\np3,2,m,1010,HIGH,2026-11-17\np3,3,m,1070,HIGH,2026-11-17\n")
    code, out = run(SCORER, cal, runs, "--tol-pct", "3")
    close_line = ""
    for line in out.splitlines():
        if line.startswith("close (within 5%)"):
            close_line = line
    check("scorer: 3-5% runs listed as close", "p3 run 1" in close_line, out[-300:])
    check("scorer: over 5% not called close", "p3 run 3" not in close_line, close_line)


def test_ledger_and_export(tmp):
    plugin = tmp / "plugin/appraisal-comps"
    copy_writable(PRODUCT, plugin)
    root = tmp / "Appraisal Comps"
    write(root / "settings/conventions.md", "# Conventions\n")
    for d in ("shared/buildings", "shared/evals", "exports"):
        (root / d).mkdir(parents=True, exist_ok=True)
    copy_writable(PRODUCT / "templates/assignment", root / "assignments/2026-01-05_1-test-st_2a")
    write(root / "shared/evals/calibration.csv", "plan_id,plan_file,plan_type,manual_sqft,notes\n")
    write(root / "shared/evals/runs.csv", "plan_id,run,model,measured_sqft,confidence,date\n")
    write(root / "shared/lessons.md", "# Lessons\n")
    asg = "2026-01-05_1-test-st_2a"
    a = root / "assignments" / asg
    write(a / "subject.json", json.dumps({"address": "1 TEST ST", "unit": "2A", "bbl": "1000010001",
                                          "billing_bbl": "1000017501", "effective_date": "2026-01-05",
                                          "inspection_notes": "", "search": {"since": "2025-01-01"}, "pluto": {}}))
    led, exp = plugin / "tools/ledger.py", plugin / "tools/export_learning.py"

    for st in ("1", "2-3", "4", "5", "6"):
        for ev in ("start", "done"):
            code, out = run(led, "event", "--assignment", asg, "--stage", st, "--event", ev, cwd=root)
    check("ledger: events written", code == 0 and (a / "events.csv").exists(), out)
    code, out = run(led, "error", "--assignment", asg, "--stage", "4", "--code", "NOT_A_CODE", cwd=root)
    check("ledger: unknown error code refused with the list", code == 2 and "DIGIT_FLAGGED" in out)
    code, out = run(led, "error", "--assignment", asg, "--stage", "1", "--code", "BROWSER_CHALLENGE",
                    "--detail", "Zillow", cwd=root)
    check("ledger: BROWSER_CHALLENGE accepted", code == 0, out)
    code, out = run(led, "correction", "--assignment", asg, "--stage", "4", "--found-by", "reviewer",
                    "--category", "digit", "--field", "Bed width", "--ai-value", "7-8", "--corrected-value", "7-3",
                    "--note", "lender asked", cwd=root)
    check("ledger: any note accepted", code == 0, out)
    code, out = run(led, "check", cwd=root)
    check("ledger: check passes on a clean folder", code == 0, out)
    ledger_spec = importlib.util.spec_from_file_location("ledger", led)
    ledger_module = importlib.util.module_from_spec(ledger_spec)
    # ledger.py finds the workspace from the current folder when it loads
    previous_cwd = Path.cwd()
    os.chdir(root)
    try:
        ledger_spec.loader.exec_module(ledger_module)
    finally:
        os.chdir(previous_cwd)
    ledger_ranking_header = ledger_module.RANKING
    phase_1_columns = ["property_type", "floor", "lot_sqft", "year_built", "units", "size_basis",
                       "listed_sqft", "listed_basis"]
    check("ledger: RANKING ends with the phase 1 columns",
          ledger_ranking_header[-8:] == phase_1_columns, ledger_ranking_header[-8:])
    write(a / "data/ranking.csv", ",".join(ledger_ranking_header) + "\n")
    code, out = run(led, "check", cwd=root)
    check("ledger: ranking.csv with phase 1 columns passes check", code == 0, out)
    code, out = run(led, "error", "--assignment", asg, "--stage", "4", "--code", "OUTLINE_NOT_MEASURED", cwd=root)
    check("ledger: OUTLINE_NOT_MEASURED accepted", code == 0, out)

    # a closed single-family assignment measured from a sketch only (no rooms CSV)
    house_asg = "2026-01-06_2-test-pl"
    house = root / "assignments" / house_asg
    copy_writable(PRODUCT / "templates/assignment", house)
    write(house / "subject.json", json.dumps({"address": "2 TEST PL", "unit": "", "bbl": "4000020002",
                                              "building_type": "single-family", "gla_sqft": 1260,
                                              "gla_source": "sketch", "interior_sqft_stated": None,
                                              "effective_date": "2026-01-06", "inspection_notes": "",
                                              "search": {"since": "2024-01-06"}, "pluto": {}}))
    write(house / "plans/subject_sketch.csv", "floor,area_type,side,length,direction,flag\n"
          "1st,finished,1,30-0,R,\n1st,finished,2,20-0,D,\n1st,finished,3,30-0,L,\n1st,finished,4,20-0,U,\n")
    write(house / "plans/subject_result.md", "Floor 1st (finished): 600 sq ft\n"
          "(d) Outline total: 1260 sq ft (sketch: finished floors + attic)\n"
          "(c) Total under convention 'outline': 1260 sq ft\nConfidence ceiling from checks: HIGH\n")
    # Stage 6 done is what closes it for the export; the event command itself
    # is checked on the first assignment above.
    run(led, "event", "--assignment", house_asg, "--stage", "6", "--event", "done", cwd=root)

    # an open assignment (stage 1 started only) must stay out of the export
    open_asg = "2026-01-07_3-open-st"
    copy_writable(PRODUCT / "templates/assignment", root / "assignments" / open_asg)
    run(led, "event", "--assignment", open_asg, "--stage", "1", "--event", "start", cwd=root)
    code, out = run(led, "correction", "--assignment", open_asg, "--stage", "1", "--found-by", "appraiser",
                    "--category", "other", "--field", "Open job field", "--ai-value", "a", "--corrected-value", "b",
                    cwd=root)
    check("ledger: correction on an open assignment written", code == 0, out)
    plan_image_bytes = b"\x89PNG\r\n\x1a\nfake"
    (house / "plans/subject.png").write_bytes(plan_image_bytes)
    write(root / "shared/lessons.md", "# Lessons\n- Lender asked for a 4th comp, jdoe@example.com\n")
    setup = plugin / "tools/setup_workspace.py"

    empty_folder = tmp / "empty-for-bad-name"
    empty_folder.mkdir()
    code, out = run(setup, "--into", empty_folder, "--name", "D Cohen")
    check("setup: refuses a name with a space", code == 2 and not any(empty_folder.iterdir()), out)
    fresh_folder = tmp / "fresh-without-name"
    code, out = run(setup, "--into", fresh_folder)
    check("setup: without --name writes no export.json",
          code == 0 and not (fresh_folder / "settings/export.json").exists(), out)

    # a copy of the workspace before his name is saved, for the 0.7.0 settings check
    old_root = tmp / "Appraisal Comps 0.7.0"
    shutil.copytree(root, old_root)

    code, out = run(exp, "build", cwd=root)
    check("export: build without a name names setup", code == 2 and "setup_workspace.py" in out, out)
    code, out = run(setup, "--into", root, "--name", "dcohen", cwd=root)
    saved_settings = root / "settings/export.json"
    check("setup: saves his name", code == 0 and saved_settings.exists()
          and json.loads(saved_settings.read_text()) == {"name": "dcohen"}, out)

    pending = root / "exports/_pending" / date.today().isoformat()
    code, out = run(exp, "build", cwd=root)
    pending_lessons = pending / "raw/shared/lessons.md"
    check("export: build passes with an email and a lender word", code == 0 and pending_lessons.exists()
          and "jdoe@example.com" in pending_lessons.read_text(), out[-400:])

    def table_rows(file_name):
        table = pending / "tables" / file_name
        if not table.exists():
            return []
        with open(table, newline="") as f:
            return list(csv.DictReader(f))

    assignment_rows = table_rows("assignments.csv")
    subject_rows = [row for row in assignment_rows if row.get("assignment") == asg]
    subject_row = subject_rows[0] if subject_rows else {}
    check("export: subject identity exported", subject_row.get("bbl") == "1000010001"
          and subject_row.get("address") == "1 TEST ST" and subject_row.get("effective_date") == "2026-01-05",
          assignment_rows)
    house_rows = [row for row in assignment_rows if row.get("assignment") == house_asg]
    house_row = house_rows[0] if house_rows else {}
    check("export: house exact size", house_row.get("size_sqft") == "1260"
          and house_row.get("plans_measured") == "1", house_row)
    result_rows = table_rows("plan_results.csv")
    check("export: sketch result exported",
          any(row.get("plan") == f"{house_asg}/subject" and row.get("outline_sqft") == "1260" for row in result_rows),
          result_rows)
    copied_image = pending / "raw/assignments" / house_asg / "plans/subject.png"
    check("export: plan image copied byte for byte",
          copied_image.exists() and copied_image.read_bytes() == plan_image_bytes)
    check("export: settings copied without export.json", (pending / "raw/settings/conventions.md").exists()
          and not any(path.name == "export.json" for path in pending.rglob("*")))
    open_in_raw = any(open_asg in str(path) for path in (pending / "raw").rglob("*"))
    open_in_tables = any(open_asg in table.read_text() for table in (pending / "tables").glob("*.csv"))
    check("export: open assignment left out", (pending / "raw").exists() and not open_in_raw and not open_in_tables)
    raw_corrections = pending / "raw/shared/corrections.csv"
    check("export: open assignment's correction kept in raw/shared only",
          raw_corrections.exists() and open_asg in raw_corrections.read_text()
          and open_asg not in (pending / "tables/corrections.csv").read_text())
    adjustments_table = pending / "tables/adjustments.csv"
    check("export: empty table keeps its header", adjustments_table.exists()
          and adjustments_table.read_text().startswith("assignment,"))
    readme = pending / "README.md"
    table_names = [table.name for table in (pending / "tables").glob("*.csv")]
    check("export: README describes every table", readme.exists() and table_names
          and all(name in readme.read_text() for name in table_names), table_names)
    write(pending / "raw/stale.txt", "left over from an earlier build\n")
    code, out = run(exp, "build", cwd=root)
    check("export: rebuild clears stale files", code == 0 and not (pending / "raw/stale.txt").exists(), out[-400:])

    code, out = run(exp, "approve", cwd=root)
    export_folder = f"{date.today().isoformat()}_learning_dcohen"
    zip_path = root / "exports" / f"{export_folder}.zip"
    zip_names = zipfile.ZipFile(zip_path).namelist() if zip_path.exists() else []
    expected_names = [f"{export_folder}/README.md", f"{export_folder}/tables/assignments.csv",
                      f"{export_folder}/raw/assignments/{house_asg}/plans/subject.png"]
    check("export: approve makes the named zip",
          code == 0 and all(name in zip_names for name in expected_names), out + str(zip_names[:10]))

    write(old_root / "settings/export.json", json.dumps({"contributor": "A-001", "mode": "external", "salt": "x"}))
    code, out = run(exp, "build", cwd=old_root)
    old_manifest = old_root / "exports/_pending" / date.today().isoformat() / "MANIFEST.md"
    check("export: 0.7.0 settings still build", code == 0 and old_manifest.exists()
          and "A-001" in old_manifest.read_text(), out[-400:])

    # a hand-edited name that isn't safe in a file name is refused before anything is written
    old_settings = old_root / "settings/export.json"
    old_settings_text = old_settings.read_text()
    files_before = sorted(str(path) for path in tmp.rglob("*"))
    write(old_settings, json.dumps({"name": "../x"}))
    build_code, build_out = run(exp, "build", cwd=old_root)
    approve_code, approve_out = run(exp, "approve", cwd=old_root)
    old_settings.write_text(old_settings_text)
    files_after = sorted(str(path) for path in tmp.rglob("*"))
    check("export: hand-edited name ../x refused",
          build_code == 2 and approve_code == 2 and "setup_workspace.py" in build_out
          and "setup_workspace.py" in approve_out and files_after == files_before, build_out + approve_out)
    code, out = run(exp, "approve", cwd=old_root)
    old_zip = old_root / "exports" / f"{date.today().isoformat()}_learning_A-001.zip"
    check("export: 0.7.0 contributor code still approves", code == 0 and old_zip.exists(), out)

@functools.cache
def guard_module():
    """guard.py, loaded once into this process, so a decision check doesn't
    start a new Python. test_guard still runs the real script for what only
    a real run shows: its printed output and failing closed on bad input."""
    spec = importlib.util.spec_from_file_location("guard", GUARD)
    module = importlib.util.module_from_spec(spec)
    sys.modules["guard"] = module  # its dataclass looks itself up here while loading
    # An earlier test may have imported workspace or sites from a copied
    # plugin; guard.py must get its own, next to it in PRODUCT.
    sys.modules.pop("workspace", None)
    sys.modules.pop("sites", None)
    sys.modules.pop("browser_scripts", None)
    try:
        spec.loader.exec_module(module)
    except SystemExit as error:
        # guard.py exits (with a deny) when it can't load its own modules;
        # here that must fail the tests, not end the run with exit 0.
        raise RuntimeError(f"guard.py failed to load (exit {error.code})") from error
    if module.plugin_root() != PRODUCT:
        raise RuntimeError(f"guard.py loaded workspace.py from {module.plugin_root()}, not {PRODUCT}")
    return module


def guard_decision(ws, tool, tool_input):
    """The guard's Decision for one tool call, from the same hook payload the
    app sends (the guard reads only tool_name and tool_input)."""
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(ws)}
    return guard_module().decide_from_raw(json.dumps(payload))


def guard_action(ws, tool, tool_input):
    return guard_decision(ws, tool, tool_input).action


def guard_reason(ws, tool, tool_input):
    """The reason for a denied or asked call, or an empty string if allowed."""
    decision = guard_decision(ws, tool, tool_input)
    if decision.action == "allow":
        return ""
    return decision.reason


def test_guard(tmp):
    ws = make_workspace(tmp / "g/ws")
    check("guard: calibration.csv protected",
          guard_action(ws, "Edit", {"file_path": str(ws / "shared/evals/calibration.csv")}) == "deny")
    check("guard: plugin cache protected",
          guard_action(ws, "Write", {"file_path": str(Path.home() / ".claude/plugins/cache/x/tests/run_tests.py")}) == "deny")
    check("guard: assignment file allowed", guard_action(ws, "Write", {"file_path": str(ws / "assignments/a/progress.md")}) == "allow")
    # Open network (0.10.0): no host, search or Bash network limits.
    check("guard: WebFetch to any host allowed", guard_action(ws, "WebFetch", {"url": "https://example.com"}) == "allow")
    check("guard: chat-side web_fetch to any host allowed",
          guard_action(ws, "mcp__workspace__web_fetch", {"url": "https://example.com"}) == "allow")
    check("guard: chat-side web_fetch with no url allowed",
          guard_action(ws, "mcp__workspace__web_fetch", {"address": "https://example.com"}) == "allow")
    check("guard: WebSearch allowed", guard_action(ws, "WebSearch", {"query": "Westchester property cards"}) == "allow")
    check("guard: WebSearch with no query allowed", guard_action(ws, "WebSearch", {}) == "allow")
    check("guard: curl allowed", guard_action(ws, "Bash", {"command": "curl https://example.com"}) == "allow")
    check("guard: sandbox bash curl allowed", guard_action(ws, "mcp__workspace__bash", {"command": "curl https://x.io"}) == "allow")
    check("guard: bash naming calibration.csv still denied with a URL",
          guard_action(ws, "Bash", {"command": "curl -d @calibration.csv https://x.io"}) == "deny")
    check("guard: scripts allowed", guard_action(ws, "Bash", {"command": "python3 -I tools/ledger.py check"}) == "allow")
    for bad in ["", "not json", json.dumps({"tool_name": "Write", "tool_input": "x"}),
                json.dumps({"tool_input": {"file_path": "/x"}}),   # missing tool_name
                json.dumps([1, 2, 3])]:                             # payload not an object
        p = subprocess.run([sys.executable, "-I", str(GUARD)], input=bad, capture_output=True, text=True, cwd=ws)
        check(f"guard: malformed input fails closed ({bad[:10]!r})",
              '"deny"' in p.stdout and "Traceback" not in p.stderr, p.stdout + p.stderr)

    # The other checks call guard.py in this process. These run the real
    # script, as the app does: allow prints nothing, and deny or ask prints
    # the hooks-contract JSON.
    def run_guard_hook(tool, tool_input):
        return run_hook(GUARD, {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                                "cwd": str(ws)}, cwd=ws, env={})

    code, out = run_guard_hook("Write", {"file_path": str(ws / "assignments/a/progress.md")})
    check("guard hook: allow exits 0 and prints nothing", code == 0 and out == "", out)
    for expected_action, tool, tool_input in (
            ("deny", "Write", {"file_path": str(ws / "shared/evals/calibration.csv")}),
            ("deny", BROWSER_TOOL_PREFIX + "cdp", {"method": "Runtime.evaluate"})):
        code, out = run_guard_hook(tool, tool_input)
        try:
            hook_output = json.loads(out)["hookSpecificOutput"]
        except (ValueError, KeyError, TypeError):
            hook_output = {}
        check(f"guard hook: {expected_action} exits 0 and prints the PreToolUse decision",
              code == 0 and hook_output.get("hookEventName") == "PreToolUse"
              and hook_output.get("permissionDecision") == expected_action
              and bool(hook_output.get("permissionDecisionReason")), out)

    # The sandbox-mounted path for calibration.csv, and a Bash command that
    # merely names calibration.csv.
    check("guard: sandbox-path calibration.csv write denied",
          guard_action(ws, "Write", {"file_path": "/sessions/x/mnt/ws/shared/evals/calibration.csv"}) == "deny")
    check("guard: bash command naming calibration.csv denied",
          guard_action(ws, "Bash", {"command": "cat shared/evals/calibration.csv"}) == "deny")
    # Fix round 1, item 2: case must not bypass any of the three checks
    # (macOS/APFS is case-insensitive).
    check("guard: calibration.csv protected regardless of case",
          guard_action(ws, "Edit", {"file_path": str(ws / "shared/evals/Calibration.CSV")}) == "deny")
    check("guard: plugin segment protected regardless of case",
          guard_action(ws, "Write", {"file_path": "/Users/x/.Claude/Plugins/cache/x/a.py"}) == "deny")
    check("guard: bash calibration.csv protected regardless of case",
          guard_action(ws, "Bash", {"command": "cp x shared/evals/CALIBRATION.csv"}) == "deny")

    # Fix round 1, item 4: a Bash call with no string command, or an Edit
    # call with a non-string path, must fail closed, not be let through.
    check("guard: bash with no command fails closed",
          guard_action(ws, "Bash", {}) == "deny")
    check("guard: sandbox bash with no command fails closed",
          guard_action(ws, "mcp__workspace__bash", {}) == "deny")
    check("guard: Edit with non-string file_path fails closed",
          guard_action(ws, "Edit", {"file_path": ["not", "a", "string"]}) == "deny")

    # Fix round 1, item 5 (ruling R11a): calibration.csv is protected by its
    # basename alone, wherever it is - not only under shared/evals/.
    check("guard: calibration.csv protected by basename anywhere",
          guard_action(ws, "Write", {"file_path": str(ws / "assignments/a/calibration.csv")}) == "deny")

    # Review-requested: a path that is literally inside this guard's own
    # plugin_root(), not merely carrying a cache-folder segment.
    check("guard: plugin_root() descendant denied",
          guard_action(ws, "Write", {"file_path": str(PRODUCT / "skills/comp-pool/scripts/nyc_sales.py")}) == "deny")

    # Review-requested: the deny reason names the file and tells the agent
    # to report the failing check instead of working around it.
    cal_path = str(ws / "shared/evals/calibration.csv")
    reason = guard_reason(ws, "Edit", {"file_path": cal_path})
    check("guard: deny reason names the file and says report the failing check instead",
          cal_path in reason and "report the failing check instead" in reason, reason)


def test_browser_launch(tmp):
    sys.path.insert(0, str(PRODUCT / "browser"))
    import launch
    pin = f"browser-harness[mcp]=={launch.BROWSER_HARNESS_VERSION}"
    check("launch: runs the pinned Browser Harness server through uvx",
          launch.harness_command() == ["uvx", "--from", "browser-harness[mcp]==0.1.14", "browser-harness-mcp"]
          and launch.BROWSER_HARNESS_VERSION == "0.1.14", launch.harness_command())
    fixture = json.loads((PRODUCT / "tests/fixtures/browser/harness-tools.json").read_text())
    check("launch: the pin and the tool-list fixture are the same version", pin in fixture["command"],
          fixture["command"])
    existing_environment = {"PATH": "/x", "BROWSER_USE_API_KEY": "bu_secret", "KEEP": "1"}
    check("launch: telemetry and the CAPTCHA solver are off and the browser-use key is removed",
          launch.harness_environment(existing_environment) == {
              "PATH": "/x", "KEEP": "1", "BROWSER_USE_OFFLINE": "1", "BU_CAPTCHA_SOLVER": "off"})
    check("launch: the environment given is left as it was",
          existing_environment == {"PATH": "/x", "BROWSER_USE_API_KEY": "bu_secret", "KEEP": "1"})

    uvx_name = "uvx"
    on_path = tmp / "b/on-path"
    write(on_path / uvx_name, "").chmod(0o755)
    check("launch: finds uvx on PATH", launch.find_uvx(str(on_path), ()) == on_path / uvx_name)
    spaced = tmp / "b/my tools"
    write(spaced / uvx_name, "").chmod(0o755)
    check("launch: finds uvx in a fallback folder with a space in its name",
          launch.find_uvx(str(tmp / "b/no-such-folder"), (tmp / "b/nope", spaced)) == spaced / uvx_name)
    plain_file = tmp / "b/plain"
    write(plain_file / uvx_name, "")
    check("launch: skips a uvx that is not executable",
          launch.find_uvx(str(tmp / "b/no-such-folder"), (plain_file,)) is None)
    check("launch: no uvx anywhere gives None", launch.find_uvx(str(tmp / "b/none"), (tmp / "b/none2",)) is None)

    present = tmp / "b/chrome-present"
    write(present, "")
    check("launch: finds Chrome at the first candidate that exists",
          launch.find_chrome((tmp / "b/nope", present)) == present)
    check("launch: no Chrome found gives None", launch.find_chrome((tmp / "b/nope",)) is None)
    check("launch: looks in both Applications folders",
          [str(path) for path in launch.CHROME_CANDIDATES] == [
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")])
    check("launch: .mcp.json starts launch.py as the browser server",
          json.loads((PRODUCT / ".mcp.json").read_text()) == {"mcpServers": {"browser": {
              "command": "python3", "args": ["-I", "${CLAUDE_PLUGIN_ROOT}/browser/launch.py"]}}})

    # It needs no Appraisal Comps folder (nothing to read from it), so it never
    # waits for one. With no uvx anywhere it stops at once, on stderr only.
    empty = tmp / "b/empty"
    empty.mkdir(parents=True)
    started = time.monotonic()
    code, out_stdout, out_stderr = run_streams(
        PRODUCT / "browser/launch.py", cwd=empty,
        env={"PATH": str(empty), "APPRAISAL_COMPS_TEST_UVX_DIRS": str(empty)})
    check("launch: starts without a workspace, and says when uvx is missing",
          code == 1 and "uvx not found" in out_stderr and "workspace" not in out_stderr
          and time.monotonic() - started < 5, out_stderr)
    # stdout is the MCP wire protocol - nothing may ever print there.
    check("launch: prints nothing to stdout", out_stdout == "", repr(out_stdout))

    # With a stand-in uvx on PATH, launch.py must exec it with the server's
    # arguments and the changed environment. It needs Google Chrome installed,
    # as on the maintainer's Mac.
    if launch.find_chrome(launch.CHROME_CANDIDATES) is None:
        check("launch: execs uvx with the pinned server (needs Google Chrome installed)", False,
              "Google Chrome not found")
    else:
        fake_bin = tmp / "b/fake bin"
        fake_bin.mkdir(parents=True)
        exec_record = tmp / "b/uvx-exec.json"
        fake_uvx = write(fake_bin / "uvx", f"""#!{sys.executable}
import json, os, sys
names = ["BROWSER_USE_OFFLINE", "BU_CAPTCHA_SOLVER", "BROWSER_USE_API_KEY"]
with open({json.dumps(str(exec_record))}, "w") as record:
    json.dump({{"argv": sys.argv[1:], "environment": {{name: os.environ.get(name) for name in names}}}}, record)
""")
        fake_uvx.chmod(0o755)
        code, out_stdout, out_stderr = run_streams(PRODUCT / "browser/launch.py", cwd=empty,
                                                   env={"PATH": str(fake_bin), "BROWSER_USE_API_KEY": "bu_secret"})
        exec_details = json.loads(exec_record.read_text()) if exec_record.exists() else {}
        check("launch: execs uvx with the server's arguments, telemetry off, the key removed",
              code == 0 and out_stdout == ""
              and exec_details.get("argv") == launch.harness_command()[1:]
              and exec_details.get("environment") == {
                  "BROWSER_USE_OFFLINE": "1", "BU_CAPTCHA_SOLVER": "off", "BROWSER_USE_API_KEY": None},
              {"exit": code, "stderr": out_stderr, "exec": exec_details})

    guard = guard_module()
    missing_from_fixture = [tool for tool in guard.BROWSER_ALLOWED_TOOLS
                            if "browser_" + tool not in [listed["name"] for listed in fixture["tools"]]]
    check("launch: every allowed tool is one the pinned server lists", missing_from_fixture == [],
          missing_from_fixture)
    # Built from pieces so the docs test's search for the removed names doesn't match this file.
    old_module_one = "runtime" + "-shim.cjs"
    old_module_two = "human" + "ize"
    removed_paths = (f"browser/{old_module_one}", f"browser/{old_module_two}.cjs", f"browser/{old_module_two}-plan.cjs",
                     "browser/vendor", "tests/browser")
    still_there = [path for path in removed_paths if (PRODUCT / path).exists()]
    check("launch: the old browser modules, the vendored library and the node tests are gone",
          still_there == [], still_there)
    needle = 'which' + '("node")'
    check("launch: the tests don't look for node", needle not in Path(__file__).read_text())


def browser_scripts_module():
    """hooks/browser_scripts.py, loaded into this process from PRODUCT."""
    spec = importlib.util.spec_from_file_location("browser_scripts", PRODUCT / "hooks/browser_scripts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("workspace", None)  # it must get its own, next to it in PRODUCT
    spec.loader.exec_module(module)
    return module


def test_browser_scripts(tmp):
    scripts_dir = PRODUCT / "browser/scripts"
    read_path = scripts_dir / "read.js"
    elements_path = scripts_dir / "elements.js"
    check("browser scripts: both files exist", read_path.exists() and elements_path.exists())
    module = browser_scripts_module()
    read_text = read_path.read_text()
    elements_text = elements_path.read_text()
    for path, text in ((read_path, read_text), (elements_path, elements_text)):
        check(f"browser scripts: {path.name} is under 6,000 characters", len(text) < 6000, str(len(text)))
    check("browser scripts: known scripts are exactly read.js and elements.js",
          sorted(module.known_scripts().values()) == ["elements.js", "read.js"])
    check("browser scripts: read.js is named", module.script_name(read_text) == "read.js")
    check("browser scripts: elements.js is named", module.script_name(elements_text) == "elements.js")
    check("browser scripts: CRLF line endings still match", module.script_name(read_text.replace("\n", "\r\n")) == "read.js")
    check("browser scripts: trailing newlines still match", module.script_name(read_text + "\n\n") == "read.js")
    doubled_indent = "\n".join("  " + line for line in read_text.split("\n"))
    check("browser scripts: doubled indent still matches", module.script_name(doubled_indent) == "read.js")
    check("browser scripts: one changed character is refused",
          module.script_name(read_text.replace("100000", "100001")) is None)
    check("browser scripts: a code fence is refused", module.script_name("```js\n" + read_text + "\n```") is None)
    check("browser scripts: None, a number and an empty string are refused",
          module.script_name(None) is None and module.script_name(42) is None and module.script_name("") is None)
    string_pattern = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
    block_comment = re.compile(r"/\*.*?\*/", re.DOTALL)
    forbidden_words = ("fetch(", "XMLHttpRequest", ".click(", ".submit(", "localStorage", "document.cookie")
    for path, text in ((read_path, read_text), (elements_path, elements_text)):
        code = block_comment.sub("", text)
        check(f"browser scripts: {path.name} has no backtick", "`" not in text)
        bad_strings = [string for string in string_pattern.findall(code)
                       if "\\n" in string or re.search(r"\s\s", string)]
        check(f"browser scripts: {path.name} has no string with a newline or two spaces", not bad_strings,
              str(bad_strings))
        found_words = [word for word in forbidden_words if word in text]
        check(f"browser scripts: {path.name} calls nothing that sends or acts", not found_words, str(found_words))


def post_tool_module():
    """hooks/post_tool.py, loaded into this process from PRODUCT."""
    spec = importlib.util.spec_from_file_location("post_tool", POST_TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("workspace", None)
    sys.modules.pop("browser_scripts", None)
    spec.loader.exec_module(module)
    return module


def test_browser_post_tool(tmp):
    data_dir = tmp / "data"
    data_dir.mkdir()
    elements_file = data_dir / "browser-elements.json"
    page_file = data_dir / "browser-page.json"
    elements_js = (PRODUCT / "browser/scripts/elements.js").read_text()
    read_js = (PRODUCT / "browser/scripts/read.js").read_text()
    js_tool = BROWSER_TOOL_PREFIX + "js"
    reply = {"origin": "https://app.reggora.com", "url": "https://app.reggora.com/orders",
             "items": [{"n": 1, "kind": "button", "name": "Send | Next", "value": "client: Maria Lopez",
                        "selector": "#send", "x": 100, "y": 200, "w": 80, "h": 30}]}

    post_tool = post_tool_module()

    def payload_for(tool_name, tool_input, tool_response):
        return {"hook_event_name": "PostToolUse", "tool_name": tool_name, "tool_input": tool_input,
                "tool_response": tool_response}

    def post(tool_name, tool_input, tool_response):
        """The hook's handler, in this process (a Python start per case would be slow)."""
        post_tool.handle(payload_for(tool_name, tool_input, tool_response), data_dir, time.time())

    def run_main(raw_stdin, plugin_data):
        """post_tool.main() in this process with the given stdin and CLAUDE_PLUGIN_DATA (None: unset).
        Returns (exit code, what it printed)."""
        saved_stdin, saved_environment = sys.stdin, os.environ.get("CLAUDE_PLUGIN_DATA")
        captured = io.StringIO()
        sys.stdin = io.StringIO(raw_stdin)
        if plugin_data is None:
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        else:
            os.environ["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
        try:
            with contextlib.redirect_stdout(captured):
                post_tool.main()
            code = 0
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 1
        finally:
            sys.stdin = saved_stdin
            os.environ.pop("CLAUDE_PLUGIN_DATA", None)
            if saved_environment is not None:
                os.environ["CLAUDE_PLUGIN_DATA"] = saved_environment
        return code, captured.getvalue()

    def clear_state():
        for state_file in (elements_file, page_file):
            if state_file.exists():
                state_file.unlink()

    def saved_items():
        return json.loads(elements_file.read_text())["items"]

    code, out = run_hook(POST_TOOL, payload_for(js_tool, {"expression": elements_js}, reply),
                         env={"CLAUDE_PLUGIN_DATA": str(data_dir)})
    check("post_tool: a real run on an elements.js reply exits 0 and prints nothing", code == 0 and out == "", out)
    saved = json.loads(elements_file.read_text())
    check("post_tool: saved list has origin, url and a fresh saved_at",
          saved["origin"] == "https://app.reggora.com" and saved["url"] == reply["url"]
          and abs(saved["saved_at"] - time.time()) < 5)
    check("post_tool: saved item has the name and the box",
          saved["items"] == [{"n": 1, "kind": "button", "name": "Send | Next", "selector": "#send",
                              "x": 100, "y": 200, "w": 80, "h": 30}])
    check("post_tool: a field's value is never saved", "Maria Lopez" not in elements_file.read_text())
    check("post_tool: the page origin is saved too",
          json.loads(page_file.read_text())["origin"] == "https://app.reggora.com")

    wrapped_replies = {
        "a JSON string": json.dumps(reply),
        "a list of text blocks": [{"type": "text", "text": json.dumps(reply)}],
        "an object wrapping content": {"content": [{"type": "text", "text": json.dumps(reply)}]},
    }
    for shape, wrapped in wrapped_replies.items():
        clear_state()
        post(js_tool, {"expression": elements_js}, wrapped)
        check(f"post_tool: reply as {shape} is read",
              elements_file.exists() and saved_items()[0]["name"] == "Send | Next")

    post(js_tool, {"expression": elements_js}, reply)
    post(js_tool, {"expression": elements_js}, {"error": "Runtime.evaluate failed"})
    check("post_tool: an error reply leaves no list behind", not elements_file.exists())

    clear_state()
    post(js_tool, {"expression": elements_js}, reply)
    page_reply = {"origin": "https://app.reggora.com", "url": "https://app.reggora.com/o/1", "title": "t",
                  "text": "page text"}
    post(js_tool, {"expression": read_js}, page_reply)
    check("post_tool: read.js saves the page origin and leaves the list",
          json.loads(page_file.read_text())["url"].endswith("/o/1") and elements_file.exists())
    check("post_tool: page text is never saved",
          "page text" not in page_file.read_text() and "page text" not in elements_file.read_text())

    clear_state()
    post(BROWSER_TOOL_PREFIX + "page_info",
         {}, {"url": "https://www.onekeymls.com/search?x=1", "title": "t", "w": 1200, "h": 800})
    check("post_tool: page_info origin is derived from the url",
          json.loads(page_file.read_text())["origin"] == "https://www.onekeymls.com")

    for acting_tool in ("goto", "new_tab", "click", "type", "fill", "press", "scroll", "close_tab",
                        "ensure_real_tab"):
        post(js_tool, {"expression": elements_js}, reply)
        post(BROWSER_TOOL_PREFIX + acting_tool, {}, {"ok": True})
        check(f"post_tool: {acting_tool} clears the list", not elements_file.exists())
    for quiet_tool in ("screenshot", "wait", "current_tab"):
        post(js_tool, {"expression": elements_js}, reply)
        post(BROWSER_TOOL_PREFIX + quiet_tool, {}, {"ok": True})
        check(f"post_tool: {quiet_tool} leaves the list", elements_file.exists())

    clear_state()
    post(js_tool, {"expression": "1+1"}, {"url": "https://x.test/", "items": []})
    check("post_tool: another script writes nothing", not elements_file.exists() and not page_file.exists())

    many_items = [dict(reply["items"][0], n=number) for number in range(300)]
    post(js_tool, {"expression": elements_js}, dict(reply, items=many_items))
    check("post_tool: at most 200 items kept", len(saved_items()) == 200)
    bad_item = dict(reply["items"][0], x="left")
    post(js_tool, {"expression": elements_js}, dict(reply, items=[bad_item, reply["items"][0]]))
    check("post_tool: an item with a non-number box is dropped", len(saved_items()) == 1)

    clear_state()
    elements_payload = json.dumps(payload_for(js_tool, {"expression": elements_js}, reply))
    quiet_cases = {
        "another tool": lambda: run_main(json.dumps(payload_for("Edit", {"expression": elements_js}, reply)), data_dir),
        "empty stdin": lambda: run_main("", data_dir),
        "bad JSON": lambda: run_main("not json", data_dir),
        "no plugin data": lambda: run_main(elements_payload, None),
        "missing plugin data folder": lambda: run_main(elements_payload, tmp / "nowhere"),
    }
    for case, run_case in quiet_cases.items():
        code, out = run_case()
        check(f"post_tool: {case} exits 0, prints nothing, writes nothing",
              code == 0 and out == "" and not elements_file.exists() and not (tmp / "nowhere").exists(), out)
    code, out = run_hook_raw(POST_TOOL, "not json", {"CLAUDE_PLUGIN_DATA": str(data_dir)})
    check("post_tool: a real run on bad JSON exits 0 and prints nothing", code == 0 and out == "", out)

    hooks_config = json.loads((PRODUCT / "hooks/hooks.json").read_text())["hooks"]
    check("post_tool: hooks.json has the PostToolUse entry",
          hooks_config["PostToolUse"] == [{
              "matcher": "mcp__plugin_appraisal-comps_browser__.*",
              "hooks": [{"type": "command", "command": 'python3 -I "${CLAUDE_PLUGIN_ROOT}/hooks/post_tool.py"'}]}])
    check("post_tool: the PreToolUse matcher is unchanged",
          hooks_config["PreToolUse"][0]["matcher"] ==
          "Edit|Write|MultiEdit|NotebookEdit|Bash|mcp__workspace__bash|mcp__plugin_appraisal-comps_browser__.*")
    check("post_tool: tool prefix matches the guard's",
          BROWSER_TOOL_PREFIX == guard_module().BROWSER_TOOL_PREFIX)


def run_hook_raw(script, raw_stdin, env):
    """Like run_hook, with the stdin text given as it is (not JSON-encoded)."""
    child_env = dict(os.environ)
    child_env["HOME"] = str(SAFE_HOME)
    child_env.pop("CLAUDE_PLUGIN_DATA", None)
    child_env.update(env)
    p = subprocess.run([sys.executable, "-I", str(script)], input=raw_stdin, capture_output=True,
                       text=True, env=child_env)
    return p.returncode, p.stdout + p.stderr


def test_browser_guard(tmp):
    guard = guard_module()
    data_dir = tmp / "data"
    data_dir.mkdir()
    ws = make_workspace(tmp / "ws")
    prefix = BROWSER_TOOL_PREFIX
    read_js = (PRODUCT / "browser/scripts/read.js").read_text()
    elements_js = (PRODUCT / "browser/scripts/elements.js").read_text()
    send = {"n": 1, "kind": "button", "name": "Send | Next", "selector": "#send", "x": 100, "y": 200, "w": 80, "h": 30}
    search = {"n": 2, "kind": "input", "name": "Search", "selector": "#q", "x": 300, "y": 50, "w": 200, "h": 30}

    def with_list(items, age_s=0, raw_text=None):
        """Write browser-elements.json into data_dir and point CLAUDE_PLUGIN_DATA at it."""
        os.environ["CLAUDE_PLUGIN_DATA"] = str(data_dir)
        if raw_text is not None:
            (data_dir / "browser-elements.json").write_text(raw_text)
            return
        state = {"origin": "https://app.reggora.com", "url": "https://app.reggora.com/orders",
                 "saved_at": time.time() - age_s, "items": items}
        (data_dir / "browser-elements.json").write_text(json.dumps(state))

    def act(tool, tool_input):
        return guard_action(ws, prefix + tool, tool_input)

    saved_environment = os.environ.pop("CLAUDE_PLUGIN_DATA", None)
    try:
        fixture = json.loads((PRODUCT / "tests/fixtures/browser/harness-tools.json").read_text())
        tools = {tool["name"]: tool["inputSchema"].get("properties", {}) for tool in fixture["tools"]}
        check("guard: the guard knows exactly the pinned server's tools",
              set(tools) == {"browser_" + name for name in guard.HARNESS_TOOLS}, sorted(tools))
        check("guard: every allowed tool is a server tool", set(guard.BROWSER_ALLOWED_TOOLS) <= set(guard.HARNESS_TOOLS))
        check("guard: the parameter names match the server's",
              guard.JS_EXPRESSION_KEY in tools["browser_js"]
              and guard.URL_KEY in tools["browser_goto"] and guard.URL_KEY in tools["browser_new_tab"]
              and guard.CLICK_X_KEY in tools["browser_click"] and guard.CLICK_Y_KEY in tools["browser_click"]
              and guard.FILL_SELECTOR_KEY in tools["browser_fill"])

        with_list([send, search])
        harmless_input = {
            "goto": {"url": "https://www.onekeymls.com/"}, "new_tab": {"url": "https://www.onekeymls.com/"},
            "click": {"x": 900, "y": 700}, "type": {"text": "11370"}, "fill": {"selector": "#q", "text": "1"},
            "press": {"key": "Enter"}, "scroll": {"x": 10, "y": 10, "dy": 300}, "screenshot": {},
            "close_tab": {}, "wait": {"seconds": 1}, "js": {"expression": read_js},
        }
        not_allowed = [tool for tool in guard.BROWSER_ALLOWED_TOOLS
                       if act(tool, harmless_input.get(tool, {})) != "allow"]
        check("guard: every allowed tool with harmless input is allowed", not_allowed == [], not_allowed)

        denied_hints = {"cdp": "read.js", "http_get": "browser_goto", "upload_file": "uploaded",
                        "list_tabs": "browser_current_tab", "switch_tab": "browser_current_tab",
                        "solve_captcha": "Chrome", "start_recording": "browser_screenshot",
                        "stop_recording": "browser_screenshot", "feedback": "vendor", "report_run": "vendor",
                        "mark_tab": "", "name_session": "", "release_tabs": "", "some_future_tool": ""}
        for tool, hint_word in denied_hints.items():
            reason = guard_reason(ws, prefix + tool, {})
            check(f"guard: browser_{tool} denied, reason says what to use", act(tool, {}) == "deny" and hint_word in reason,
                  reason)
        check("guard: a tool on the browser server without the browser_ prefix is denied",
              guard_action(ws, "mcp__plugin_appraisal-comps_browser__evaluate", {}) == "deny")

        for label, expression in (("read.js", read_js), ("elements.js", elements_js),
                                  ("read.js with CRLF and a trailing newline", read_js.replace("\n", "\r\n") + "\n")):
            check(f"guard: js with {label} allowed", act("js", {"expression": expression}) == "allow")
        wrong_scripts = {
            "one character changed": read_js.replace("100000", "100001"),
            "another script": "document.title",
            "a code fence": "```js\n" + read_js + "\n```",
            "a number": 5,
        }
        for label, expression in wrong_scripts.items():
            reason = guard_reason(ws, prefix + "js", {"expression": expression})
            check(f"guard: js with {label} denied, reason names both scripts",
                  act("js", {"expression": expression}) == "deny" and "read.js" in reason and "elements.js" in reason,
                  reason)
        reason = guard_reason(ws, prefix + "js", {})
        check("guard: js with no expression denied", act("js", {}) == "deny" and "elements.js" in reason, reason)

        for bad_url in ("file:///etc/passwd", "javascript:alert(1)", "chrome://settings", "", 5):
            check(f"guard: goto {bad_url!r} denied", act("goto", {"url": bad_url}) == "deny")
        check("guard: goto with no url denied", act("goto", {}) == "deny")
        check("guard: goto HTTPS in capitals allowed", act("goto", {"url": "HTTPS://Example.com"}) == "allow")
        check("guard: new_tab with no url allowed", act("new_tab", {}) == "allow")
        check("guard: new_tab about:blank allowed", act("new_tab", {"url": "about:blank"}) == "allow")
        check("guard: new_tab file: denied", act("new_tab", {"url": "file:///etc/passwd"}) == "deny")

        os.environ.pop("CLAUDE_PLUGIN_DATA")
        reason = guard_reason(ws, prefix + "click", {"x": 140, "y": 215})
        check("guard: click with no element list denied, says run elements.js first",
              act("click", {"x": 140, "y": 215}) == "deny" and "run elements.js first" in reason, reason)

        with_list([send, search])
        check("guard: click inside a Send element asks", act("click", {"x": 140, "y": 215}) == "ask")
        check("guard: click on its corner asks", act("click", {"x": 100, "y": 200}) == "ask")
        check("guard: click inside Search allowed", act("click", {"x": 400, "y": 65}) == "allow")
        check("guard: click outside every box allowed", act("click", {"x": 900, "y": 700}) == "allow")
        check("guard: a fractional click inside Send asks", act("click", {"x": 140.5, "y": 215.2}) == "ask")

        email = dict(send, name="Email listing", x=0, y=0)
        share = dict(search, name="Share", x=500, y=0)
        sender = dict(send, name="Sender details", x=0, y=500)
        with_list([email, share, sender])
        check("guard: click on Email listing asks", act("click", {"x": 10, "y": 10}) == "ask")
        check("guard: click on Share asks", act("click", {"x": 510, "y": 10}) == "ask")
        check("guard: click on Sender details allowed", act("click", {"x": 10, "y": 510}) == "allow")

        with_list([send], age_s=121)
        check("guard: an element list older than 120 seconds denies a click", act("click", {"x": 140, "y": 215}) == "deny")
        with_list([send], age_s=-60)
        check("guard: an element list from the future denies a click", act("click", {"x": 140, "y": 215}) == "deny")
        for label, raw_text in (("not JSON", "not json"), ("a list", "[]"),
                                ("an item with a text coordinate",
                                 json.dumps({"origin": "o", "url": "u", "saved_at": time.time(),
                                             "items": [dict(send, x="left")]}))):
            with_list([], raw_text=raw_text)
            check(f"guard: an element list that is {label} denies a click", act("click", {"x": 140, "y": 215}) == "deny")

        with_list([send, search])
        for label, click_input in (("text x", {"x": "140", "y": 215}), ("true x", {"x": True, "y": 215}),
                                   ("null x", {"x": None, "y": 215}), ("negative x", {"x": -1, "y": 215}),
                                   ("missing x", {"y": 215})):
            check(f"guard: click with {label} denied", act("click", click_input) == "deny")

        os.environ.pop("CLAUDE_PLUGIN_DATA")
        saved_setting = guard.CLICK_WITHOUT_LIST
        guard.CLICK_WITHOUT_LIST = "ask"
        try:
            check("guard: with the fallback set to ask, a click with no list asks",
                  act("click", {"x": 140, "y": 215}) == "ask")
        finally:
            guard.CLICK_WITHOUT_LIST = saved_setting

        with_list([send, search])
        check("guard: fill on the Send element asks", act("fill", {"selector": "#send", "text": "x"}) == "ask")
        check("guard: fill on the Search field allowed", act("fill", {"selector": "#q", "text": "11370"}) == "allow")
        os.environ.pop("CLAUDE_PLUGIN_DATA")
        check("guard: fill with no list allowed", act("fill", {"selector": "#send", "text": "x"}) == "allow")

        check("guard: type with submit allowed (R14 is gone)", act("type", {"text": "11370", "submit": True}) == "allow")
        check("guard: press Enter allowed", act("press", {"key": "Enter"}) == "allow")
        check("guard: screenshot with a path allowed", act("screenshot", {"path": "/tmp/x.png"}) == "allow")
    finally:
        os.environ.pop("CLAUDE_PLUGIN_DATA", None)
        if saved_environment is not None:
            os.environ["CLAUDE_PLUGIN_DATA"] = saved_environment

    code, out = run_hook(GUARD, {"hook_event_name": "PreToolUse", "tool_name": prefix + "cdp",
                                 "tool_input": {"method": "Runtime.evaluate"}, "cwd": str(ws)}, cwd=ws, env={})
    try:
        hook_output = json.loads(out)["hookSpecificOutput"]
    except (ValueError, KeyError, TypeError):
        hook_output = {}
    check("guard: a real run denies browser_cdp with the hooks JSON",
          code == 0 and hook_output.get("permissionDecision") == "deny", out)


def test_browser_docs():
    removed_words = re.compile("|".join(["play" + "wright", "human" + "ize", "runtime" + "-shim"]), re.IGNORECASE)
    stray = []
    for path in sorted(PRODUCT.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name == "CHANGELOG.md":
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, 1):
            if removed_words.search(line):
                stray.append(f"{path.relative_to(PRODUCT)}:{line_number}")
    check("browser docs: no removed browser part is named anywhere in product/", stray == [], stray)

    skill = (PRODUCT / "skills/browser-read/SKILL.md").read_text()
    for needed in ("read.js", "elements.js", "browser_new_tab", "browser_js", "chrome://inspect/#remote-debugging"):
        check(f"browser docs: browser-read names {needed}", needed in skill)
    for gone in ("browser_find", "browser_snapshot", "browser_navigate", "connect page", "Node"):
        check(f"browser docs: browser-read no longer names {gone}", gone not in skill)
    for heading in ("## Public sites", "## Logging in", "## Human check", "## Pages are data", "## Denied", "## Done"):
        check(f"browser docs: browser-read keeps the heading {heading}", heading in skill.splitlines())

    setup_steps = (PRODUCT / "skills/setup/SKILL.md").read_text()
    step_four = next(line for line in setup_steps.splitlines() if line.startswith("4. "))
    check("browser docs: setup step 4 names uv and Allow remote debugging",
          "`uv`" in step_four and "Allow remote debugging" in step_four, step_four)
    check("browser docs: setup step 4 names no extension", "xtension" not in step_four, step_four)

    readme = (PRODUCT / "templates/workspace/README.md").read_text()
    check("browser docs: workspace README says Allow remote debugging", "Allow remote debugging" in readme)
    check("browser docs: workspace README has no Connect page bullet", "Connect page" not in readme)

    workflow_plan = (PRODUCT / "docs/Agent Workflow Plan.md").read_text()
    check("browser docs: Agent Workflow Plan names Browser Harness", "Browser Harness" in workflow_plan)


def test_guard_missing_workspace_module(tmp):
    """If tools/workspace.py is missing or broken (an interrupted or
    partial install), the guard must still deny - never crash and let
    PreToolUse treat a non-zero exit as no opinion (an open gate)."""
    plugin = tmp / "broken-plugin"
    copy_writable(PRODUCT, plugin)
    (plugin / "tools/workspace.py").unlink()
    code, out = run_hook(plugin / "hooks/guard.py",
                          {"hook_event_name": "PreToolUse", "tool_name": "Write",
                           "tool_input": {"file_path": "/tmp/x"}, "cwd": str(tmp)}, cwd=tmp, env={})
    check("guard: missing workspace.py fails closed instead of crashing",
          code == 0 and '"deny"' in out and "Traceback" not in out, out)


def test_sources(tmp):
    ws = make_workspace(tmp / "ws")
    code, out = run(SOURCES, "check", cwd=ws)
    check("sources: shipped defaults pass check", code == 0 and "sources OK" in out, out)
    code, out = run(SOURCES, "lookup", "--job", "sold-comps", "--area", "Queens", cwd=ws)
    check("sources: NYC row and everywhere rows, OneKey MLS then Zillow then StreetEasy",
          out.index("OneKey MLS") < out.index("Zillow") < out.index("StreetEasy"), out)
    code, out = run(SOURCES, "lookup", "--job", "tax-map-lot", "--area", "westchester / yonkers", cwd=ws)
    check("sources: town rows before county rows, any case and spacing",
          out.index("Beacon") < out.index("imo.yonkersny.gov") < out.index("arcgis"), out)
    run(SOURCES, "add", "--job", "tax-map-lot", "--area", "westchester/yonkers", "--site", "Beacon",
        "--url", "https://beacon.schneidercorp.com", "--rank", "1", "--gives", "property card and tax map lookup",
        cwd=ws)
    code, out = run(SOURCES, "lookup", "--job", "tax-map-lot", "--area", "Westchester/Yonkers", cwd=ws)
    check("sources: lowercase-town add replaces the default, not an extra row",
          out.count("Beacon") == 1, out)
    write(ws / "shared/sources.csv",
          "job,area,site,url,rank,gives,status,added_by,checked\n"
          f'tax-map-lot,"Westchester / Yonkers",HandEdited,https://hand-edited.example,1,hand-edited tax lookup,'
          f'use,appraiser,{date.today().isoformat()}\n')
    code, out = run(SOURCES, "check", cwd=ws)
    check("sources: hand-edited 'Westchester / Yonkers' row passes check", code == 0 and "sources OK" in out, out)
    code, out = run(SOURCES, "lookup", "--job", "tax-map-lot", "--area", "Westchester/Yonkers", cwd=ws)
    check("sources: hand-edited 'Westchester / Yonkers' row shows in lookup", "HandEdited" in out, out)
    write(ws / "shared/sources.csv", "job,area,site,url,rank,gives,status,added_by,checked\n")
    code, out = run(SOURCES, "add", "--job", "sold-comps", "--area", "everywhere", "--site", "Zillow",
                    "--url", "https://www.zillow.com", "--rank", "1", "--gives", "sold, \"pending\" and listed",
                    "--status", "avoid", cwd=ws)
    code, out = run(SOURCES, "lookup", "--job", "sold-comps", "--area", "Queens", cwd=ws)
    check("sources: his avoid row overrides the default", "Avoid:" in out and out.index("Avoid:") < out.index("Zillow"), out)
    check("sources: defaults file untouched by add", "appraiser" not in (PRODUCT / "sources/defaults.csv").read_text())
    rows = list(csv.DictReader((ws / "shared/sources.csv").open()))
    check("sources: gives with commas and quotes round-trips", rows[0]["gives"] == 'sold, "pending" and listed', rows)
    check("sources: add stamps appraiser and today", rows[0]["added_by"] == "appraiser"
          and rows[0]["checked"] == date.today().isoformat(), rows)
    run(SOURCES, "add", "--job", "sold-comps", "--area", "Everywhere", "--site", "zillow", "--url", "https://www.zillow.com",
        "--rank", "2", "--gives", "listed", cwd=ws)
    check("sources: add replaces his own row (same job, area, site)",
          len(list(csv.DictReader((ws / "shared/sources.csv").open()))) == 1)
    run(SOURCES, "add", "--job", "listing-details", "--area", "Queens", "--site", "Zillow",
        "--url", "https://www.zillow.com", "--rank", "1", "--gives", "avoid in Queens", "--status", "avoid",
        cwd=ws)
    code, out = run(SOURCES, "lookup", "--job", "listing-details", "--area", "Queens", cwd=ws)
    check("sources: a site avoided at one applicable area is not also listed as use from another",
          "Avoid:" in out and out.count("Zillow") == 1, out)
    run(SOURCES, "add", "--job", "sold-comps", "--area", "Queens", "--site", "OneKey MLS",
        "--url", "https://www.onekeymls.com", "--rank", "1", "--gives", "Queens-specific access", cwd=ws)
    code, out = run(SOURCES, "lookup", "--job", "sold-comps", "--area", "Queens", cwd=ws)
    check("sources: a site matching at two applicable areas prints once, the more specific row",
          out.count("OneKey MLS") == 1 and "Queens-specific access" in out, out)
    for flag, value in [("--job", "comps"), ("--area", "Ulster"), ("--rank", "0"), ("--rank", "x"),
                        ("--url", "ftp://x"), ("--status", "maybe")]:
        args = {"--job": "orders", "--area": "everywhere", "--site": "X", "--url": "https://x.example",
                "--rank": "1", "--gives": "x"}
        args[flag] = value
        code, out = run(SOURCES, "add", *[part for pair in args.items() for part in pair], cwd=ws)
        check(f"sources: add refuses {flag} {value}", code == 2 and flag in out and "Traceback" not in out, out)
    his_file_before = (ws / "shared/sources.csv").read_text()
    for flag, value in [("--site", ""), ("--site", "  "), ("--gives", "")]:
        args = {"--job": "orders", "--area": "everywhere", "--site": "X", "--url": "https://x.example",
                "--rank": "1", "--gives": "x"}
        args[flag] = value
        code, out = run(SOURCES, "add", *[part for pair in args.items() for part in pair], cwd=ws)
        check(f"sources: add refuses {flag} {value!r}",
              code == 2 and flag in out and "Traceback" not in out
              and (ws / "shared/sources.csv").read_text() == his_file_before, out)
    code, out = run(SOURCES, "lookup", "--job", "orders", "--area", "everywhere", cwd=ws)
    rank_one_lines = [line for line in out.splitlines() if line.startswith("1. ")]
    check("sources: equal-rank sites print the rank, not their position",
          len(rank_one_lines) == 4, out)
    code, out = run(SOURCES, "lookup", "--job", "building-footprint", "--area", "Suffolk", cwd=ws)
    check("sources: no rows says search, then ask before adding", code == 0 and "sources.py add" in out, out)
    code, out = run(SOURCES, "lookup", "--job", "tax-map-lot", "--area", "Brooklyn", cwd=ws)
    check("sources: NYC tax map is the Property Information Portal", "propertyinformationportal.nyc.gov" in out, out)
    write(ws / "shared/sources.csv",
          "﻿job,area,site,url,rank,gives,status,added_by,checked\r\n"
          f"report,everywhere,BOMSite,https://bom.example,1,BOM test,use,appraiser,{date.today().isoformat()}\r\n")
    code, out = run(SOURCES, "check", cwd=ws)
    check("sources: his file with an Excel BOM and CRLFs passes check", code == 0 and "sources OK" in out, out)
    code, out = run(SOURCES, "lookup", "--job", "report", "--area", "everywhere", cwd=ws)
    check("sources: his file with an Excel BOM and CRLFs works for lookup", "BOMSite" in out, out)
    write(ws / "shared/sources.csv",
          "job,area,site,url,rank,gives,status,added_by,checked\n"
          f"orders,everywhere,BlankRowSite,https://blank-row.example,1,blank row test,use,appraiser,"
          f"{date.today().isoformat()}\n"
          ",,,,,,,,\n")
    code, out = run(SOURCES, "check", cwd=ws)
    check("sources: a trailing all-blank row passes check", code == 0 and "sources OK" in out, out)
    code, out = run(SOURCES, "lookup", "--job", "orders", "--area", "everywhere", cwd=ws)
    check("sources: a trailing all-blank row does not block lookup", "BlankRowSite" in out, out)
    write(ws / "shared/sources.csv", "job,area\nx,y\n")
    code, out = run(SOURCES, "lookup", "--job", "orders", "--area", "everywhere", cwd=ws)
    check("sources: broken his file stops with a fix, no traceback",
          code == 2 and "shared/sources.csv" in out and "Traceback" not in out, out)
    code, out = run(SOURCES, "check", cwd=ws)
    check("sources: check fails on a broken his file", code == 1 and "sources.csv" in out, out)
    code, out = run(SOURCES, "lookup", "--job", "orders", "--area", "everywhere", cwd=tmp)
    check("sources: outside a folder exits 2", code == 2 and "Traceback" not in out, out)
    # the reader column
    ws = make_workspace(tmp / "ws_reader")
    code, out = run(SOURCES, "check", cwd=ws)
    check("sources: shipped defaults with a reader column pass check", code == 0 and "sources OK" in out, out)
    code, out = run(SOURCES, "lookup", "--job", "sold-comps", "--area", "Queens", cwd=ws)
    onekey_line = [line for line in out.splitlines() if "OneKey MLS" in line][0]
    zillow_line = [line for line in out.splitlines() if "Zillow" in line][0]
    check("sources: lookup shows the reader at the end of each line",
          onekey_line.endswith("(chrome)") and zillow_line.endswith("(cloud)"), out)
    run(SOURCES, "add", "--job", "orders", "--area", "everywhere", "--site", "X", "--url", "https://x.example",
        "--rank", "1", "--gives", "x", cwd=ws)
    his_rows = list(csv.DictReader((ws / "shared/sources.csv").open()))
    check("sources: add writes reader cloud by default", his_rows[0]["reader"] == "cloud", his_rows)
    code, out = run(SOURCES, "add", "--job", "orders", "--area", "everywhere", "--site", "Y",
                    "--url", "https://www.onekeymls.com", "--rank", "1", "--gives", "y", "--reader", "cloud", cwd=ws)
    check("sources: add refuses reader cloud on a local-only host", code == 2 and "--reader chrome" in out, out)
    run(SOURCES, "add", "--job", "orders", "--area", "everywhere", "--site", "Y",
        "--url", "https://matrix.onekeymls.com/x", "--rank", "2", "--gives", "y", cwd=ws)
    his_rows = list(csv.DictReader((ws / "shared/sources.csv").open()))
    check("sources: add defaults a local-only host to chrome", [row["reader"] for row in his_rows] == ["cloud", "chrome"], his_rows)
    write(ws / "shared/sources.csv",
          "job,area,site,url,rank,gives,status,added_by,checked\n"
          f"orders,everywhere,OldRow,https://old.example,1,old header,use,appraiser,{date.today().isoformat()}\n")
    code, out = run(SOURCES, "check", cwd=ws)
    check("sources: his file with the old nine-column header passes check", code == 0 and "sources OK" in out, out)
    code, out = run(SOURCES, "lookup", "--job", "orders", "--area", "everywhere", cwd=ws)
    check("sources: his old-header row works in lookup", "OldRow" in out and "(cloud)" in out, out)
    run(SOURCES, "add", "--job", "orders", "--area", "everywhere", "--site", "NewRow", "--url", "https://new.example",
        "--rank", "2", "--gives", "new", cwd=ws)
    header_line = (ws / "shared/sources.csv").read_text().splitlines()[0]
    check("sources: the next add rewrites his file with ten columns",
          header_line == "job,area,site,url,rank,gives,status,added_by,checked,reader", header_line)
    write(ws / "shared/sources.csv",
          "job,area,site,url,rank,gives,status,added_by,checked,reader\n"
          f"orders,everywhere,Bad,https://bad.example,1,bad reader,use,appraiser,{date.today().isoformat()},phone\n")
    code, out = run(SOURCES, "check", cwd=ws)
    check("sources: a his row with reader phone fails check", code == 1 and "reader" in out, out)


def main():
    global SAFE_HOME
    parser = argparse.ArgumentParser(description="Run the regression tests.")
    parser.add_argument("--only", help="run only the tests whose function name contains this text")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        SAFE_HOME = tmp / "_safe_home"
        SAFE_HOME.mkdir()
        for fn in (test_plan_area, test_plan_outline, test_plan_sketch, test_bracket, test_nyc_sales,
                   test_scorer, test_scorer_close, test_workspace, test_workspace_host_paths,
                   test_workspace_mnt, test_workspace_several, test_setup, test_read_only_install,
                   test_session_start,
                   test_session_start_host_paths, test_session_start_resilience,
                   test_session_ignores_old_sites_json,
                   test_scorer_workspace_defaults, test_skill_paths, test_ledger_and_export, test_browser_launch, test_browser_scripts, test_browser_post_tool, test_browser_guard, test_browser_docs,
                   test_guard, test_guard_final_fixes,
                   test_guard_missing_workspace_module, test_sources, test_cloud_ledger, test_cloud_api, test_cloud_quote_check, test_cloud_cdp, test_cloud_read, test_cloud_inputs, test_cloud_failures, test_cloud_run, test_cloud_guard_and_export, test_cloud_docs):
            if options.only and options.only not in fn.__name__:
                continue
            try:
                if fn in (test_nyc_sales, test_skill_paths, test_cloud_docs, test_browser_docs):
                    fn()
                else:
                    fn_tmp = tmp / fn.__name__
                    fn_tmp.mkdir()
                    fn(fn_tmp)
            except Exception as e:  # a crash is a failure, not a skipped test
                check(f"{fn.__name__} crashed", False, repr(e))
    if options.only and not results:
        print(f"no test matches {options.only}")
        sys.exit(1)
    fails = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n      {detail}"))
    print(f"\n{len(results) - len(fails)}/{len(results)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
