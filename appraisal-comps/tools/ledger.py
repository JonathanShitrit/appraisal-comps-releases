#!/usr/bin/env python3
"""
ledger.py - write the structured records the workflow learns from.

Every write goes through this script so the columns never drift and the
timestamps come from the clock, not from the model.

Commands (run from the Appraisal Comps folder)

  event       --assignment <folder> --stage <1|2-3|4|5|6|export> --event <start|waiting|approved|done> [--note "12 candidates"]
  line        --billing-bbl 1012345678 --address "245 E 72 ST" --line C [--floors 4-12]
              --labeled 901 [--convention-sqft 945 --convention labeled|outline] [--listed 1000]
              --confidence HIGH|MEDIUM|LOW [--plan-source-type "broker plan"] [--plan-date 2024]
              [--subject] [--reused] --assignment <folder>
  correction  --assignment <folder> --stage 4 --found-by appraiser|reviewer|script
              --category digit|room_missing|room_extra|overlap|notch|convention|unlabeled_estimate|outline|sketch|comp_rank|comp_excluded|bed_bath|sale_data|source|other
              [--subject] [--billing-bbl ..] [--line ..] --field "Bedroom 2 width" --ai-value "7-8" --corrected-value "7-3" [--note ".."]
  error       --assignment <folder> --stage <stage> --code <ERROR_CODE> [--detail "short fact"]
              structured error codes instead of free log text (see ERROR_CODES below)
  adjustment  --assignment <folder> --candidate c01 --feature "Size" --amount=-15000 [--note ".."]
              (write negative amounts as --amount=-15000, with the equals sign)
              one row per adjustment the APPRAISER made (never suggested by the agent)
  value       --assignment <folder> --final-value 1250000 [--approach sales_comparison] [--note ".."]
              the APPRAISER's final value conclusion, recorded only after he states it
  check       validate every ledger file's columns

Files
  assignments/<folder>/events.csv    timestamp,stage,event,version,note
  shared/buildings/lines.csv         one row per line measurement
  shared/corrections.csv             one row per correction (appraiser, reviewer or script)
  assignments/<folder>/errors.csv   timestamp,stage,code,detail,version
  assignments/<folder>/data/adjustments.csv   candidate_id,feature,amount,note,recorded_on,version
  assignments/<folder>/data/conclusion.csv    final_value,approach,note,recorded_on,version
  assignments/<folder>/data/ranking.csv  written by the comp-pool skill; `check` validates it

Notes are short facts (counts, what was misread). Never client, borrower,
lender, AMC or order details: the export scanner blocks them.
"""
import argparse, csv, os, re, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import plugin_version, require_workspace

ROOT = require_workspace(Path.cwd())
VERSION = plugin_version()

EVENTS = ["timestamp", "stage", "event", "version", "note"]
LINES = ["billing_bbl", "building_address", "line", "floors", "labeled_sqft", "convention_sqft", "convention",
         "listed_sqft", "confidence", "plan_source_type", "plan_date", "measured_on", "is_subject", "reused",
         "version", "assignment_ref"]
CORR = ["date", "assignment_ref", "stage", "found_by", "category", "is_subject", "billing_bbl", "line",
        "field", "ai_value", "corrected_value", "note", "version"]
RANKING = ["candidate_id", "bbl", "address", "apt", "same_building", "distance_mi", "sale_date", "sale_price",
           "source", "acris_link", "months_before_effective", "listed_or_measured_sqft", "size_diff_pct", "beds",
           "bed_diff", "baths", "bath_diff", "uad_condition", "uad_quality", "score", "rank", "proposed",
           "appraiser_decision", "exclusion_reason", "used_in_grid",
           "property_type", "floor", "lot_sqft", "year_built", "units", "size_basis",
           "listed_sqft", "listed_basis"]
ERRORS = ["timestamp", "stage", "code", "detail", "version"]
ADJ = ["candidate_id", "feature", "amount", "note", "recorded_on", "version"]
CONCL = ["final_value", "approach", "note", "recorded_on", "version"]
ERROR_CODES = {
    "DATA_QUERY_FAILED": "an Open Data / ACRIS query errored or timed out",
    "BBL_NOT_FOUND": "PLUTO/ACRIS had no record for the BBL given",
    "BBL_AMBIGUOUS": "condo unit lot matched several or no billing lots",
    "THIN_POOL": "too few same-building sales; radius widened",
    "NO_SALES_FOUND": "no qualifying sales in the search window",
    "ACRIS_MISMATCH": "ACRIS deed amount/date differs from the DOF row",
    "PACKAGE_SALE": "multi-unit same date and price sale flagged",
    "PLAN_UNREADABLE": "plan has no dimensions, is schematic or too blurry",
    "PLAN_NOT_TO_SCALE": "scale check failed by more than 5%",
    "DIGIT_FLAGGED": "a printed digit could not be read with confidence",
    "SCRIPT_CHECK_FAILED": "plan_area.py raised a check",
    "LISTED_OFF_10PCT": "listed size differs from measured by more than 10%",
    "REVIEW_DISAGREE": "blind review disagreed with the first reading",
    "OUTLINE_NOT_MEASURED": "outline or sketch not measured: scale failed, walk not closed, or angled side",
    "BRACKET_GAP": "a subject feature could not be bracketed from the pool",
    "CONVENTION_MISSING": "settings/conventions.md still has a placeholder",
    "SOURCE_MISSING": "a value had no source (written as 'not in source')",
    "INJECTION_SEEN": "listing/plan/email text contained instructions; ignored and reported",
    "BROWSER_BLOCKED": "a browser call was denied by the guard or the origin allowlist",
    "APPRAISER_REDO": "the appraiser asked to redo a stage",
    "TOOL_ERROR": "a script or tool failed for another reason",
    "UPDATE_FAILED": "a migration failed at the first session on a new version",
    "OTHER": "anything else (say what in --detail)",
}
UAD_CONDITION = {"C1", "C2", "C3", "C4", "C5", "C6", ""}
UAD_QUALITY = {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", ""}
STAGES = {"1", "2-3", "4", "5", "6", "export"}
EVENT_TYPES = {"start", "waiting", "approved", "done"}
CATEGORIES = {"digit", "room_missing", "room_extra", "overlap", "notch", "convention", "unlabeled_estimate",
              "outline", "sketch", "comp_rank", "comp_excluded", "bed_bath", "sale_data", "source", "other"}
BLOCK_WORDS = re.compile(r"\b(borrower|lender|amc|client|loan|order\s*(no|#|number)|mortgage|@)\b", re.I)


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def assignment_dir(name):
    p = Path(name)
    if not p.is_absolute():
        p = ROOT / "assignments" / Path(name).name
    if not p.is_dir():
        die(f"assignment folder not found: {p}")
    if p.name.startswith("_"):
        die("that is a template, not an assignment")
    return p


def append(path, header, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    if not new:
        with open(path, newline="") as f:
            have = next(csv.reader(f), [])
        if have != header:
            die(f"{path} has columns {have}, expected {header}. Do not edit the header by hand")
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if new:
            w.writeheader()
        w.writerow(row)


def clean_note(s):
    s = (s or "").strip().replace("\n", " ")
    if BLOCK_WORDS.search(s):
        die("note looks like it contains client/lender/order details. Keep notes to property facts")
    return s[:200]


def bbl_ok(s):
    if s and not re.fullmatch(r"[1-5]\d{9}", s):
        die(f"BBL '{s}' must be 10 digits")
    return s or ""


def cmd_event(a):
    d = assignment_dir(a.assignment)
    if a.stage not in STAGES or a.event not in EVENT_TYPES:
        die(f"stage must be one of {sorted(STAGES)}, event one of {sorted(EVENT_TYPES)}")
    row = {"timestamp": datetime.now().isoformat(timespec="minutes"), "stage": a.stage, "event": a.event,
           "version": VERSION, "note": clean_note(a.note)}
    append(d / "events.csv", EVENTS, row)
    print(f"logged {a.stage} {a.event} at {row['timestamp']}")


def cmd_line(a):
    d = assignment_dir(a.assignment)
    if a.confidence not in ("HIGH", "MEDIUM", "LOW"):
        die("confidence must be HIGH, MEDIUM or LOW")
    if not re.fullmatch(r"[A-Za-z0-9]{1,4}", a.line):
        die("line is a letter or short number only, e.g. C or 04")
    row = {"billing_bbl": bbl_ok(a.billing_bbl), "building_address": a.address.upper().strip(),
           "line": a.line.upper(), "floors": a.floors or "", "labeled_sqft": round(a.labeled),
           "convention_sqft": round(a.convention_sqft) if a.convention_sqft else "",
           "convention": a.convention or "", "listed_sqft": round(a.listed) if a.listed else "",
           "confidence": a.confidence, "plan_source_type": clean_note(a.plan_source_type),
           "plan_date": a.plan_date or "", "measured_on": datetime.now().date().isoformat(),
           "is_subject": "Y" if a.subject else "N", "reused": "Y" if a.reused else "N",
           "version": VERSION, "assignment_ref": d.name}
    if re.search(r"https?://|www\.", row["plan_source_type"]):
        die("plan_source_type is a type (e.g. 'broker plan'), not a link")
    append(ROOT / "shared" / "buildings" / "lines.csv", LINES, row)
    print(f"line {row['line']} at {row['billing_bbl']} saved ({row['labeled_sqft']} labeled)")


def cmd_correction(a):
    d = assignment_dir(a.assignment)
    if a.category not in CATEGORIES:
        die(f"category must be one of {sorted(CATEGORIES)}")
    if a.found_by not in ("appraiser", "reviewer", "script"):
        die("found-by must be appraiser, reviewer or script")
    row = {"date": datetime.now().date().isoformat(), "assignment_ref": d.name, "stage": a.stage,
           "found_by": a.found_by, "category": a.category, "is_subject": "Y" if a.subject else "N",
           "billing_bbl": "" if a.subject else bbl_ok(a.billing_bbl), "line": "" if a.subject else (a.line or ""),
           "field": clean_note(a.field), "ai_value": clean_note(a.ai_value),
           "corrected_value": clean_note(a.corrected_value), "note": clean_note(a.note), "version": VERSION}
    append(ROOT / "shared" / "corrections.csv", CORR, row)
    print(f"correction saved: {row['category']} {row['field']} {row['ai_value']} -> {row['corrected_value']}")


def money(s):
    try:
        return round(float(str(s).replace(",", "").replace("$", "")))
    except ValueError:
        die(f"'{s}' is not a dollar amount")


def cmd_error(a):
    d = assignment_dir(a.assignment)
    if a.code not in ERROR_CODES:
        die("code must be one of:\n" + "\n".join(f"  {k}: {v}" for k, v in ERROR_CODES.items()))
    row = {"timestamp": datetime.now().isoformat(timespec="minutes"), "stage": a.stage, "code": a.code,
           "detail": clean_note(a.detail), "version": VERSION}
    append(d / "errors.csv", ERRORS, row)
    print(f"error logged: {a.code}")


def cmd_adjustment(a):
    d = assignment_dir(a.assignment)
    if not re.fullmatch(r"c\d{2,3}", a.candidate):
        die("candidate is the ranking.csv candidate_id, e.g. c01")
    row = {"candidate_id": a.candidate, "feature": clean_note(a.feature), "amount": money(a.amount),
           "note": clean_note(a.note), "recorded_on": datetime.now().date().isoformat(), "version": VERSION}
    append(d / "data" / "adjustments.csv", ADJ, row)
    print(f"adjustment saved: {a.candidate} {row['feature']} {row['amount']:+,}")


def cmd_value(a):
    d = assignment_dir(a.assignment)
    row = {"final_value": money(a.final_value), "approach": a.approach, "note": clean_note(a.note),
           "recorded_on": datetime.now().date().isoformat(), "version": VERSION}
    append(d / "data" / "conclusion.csv", CONCL, row)
    print(f"appraiser's value recorded: {row['final_value']:,}")


def cmd_check(a):
    bad = 0
    targets = [(ROOT / "shared" / "buildings" / "lines.csv", LINES), (ROOT / "shared" / "corrections.csv", CORR)]
    for d in sorted((ROOT / "assignments").glob("*")):
        if d.is_dir() and not d.name.startswith("_"):
            targets += [(d / "events.csv", EVENTS), (d / "data" / "ranking.csv", RANKING),
                        (d / "errors.csv", ERRORS), (d / "data" / "adjustments.csv", ADJ),
                        (d / "data" / "conclusion.csv", CONCL)]
    for p, header in targets:
        if not p.exists():
            continue
        with open(p, newline="") as f:
            have = next(csv.reader(f), [])
        mismatched = have != header
        if mismatched:
            bad += 1
            print(f"PROBLEM {p.relative_to(ROOT)}: columns {have}, expected {header}")
        if p.name == "ranking.csv" and not mismatched:
            for i, r in enumerate(csv.DictReader(open(p, newline="")), start=2):
                if r["uad_condition"] not in UAD_CONDITION or r["uad_quality"] not in UAD_QUALITY:
                    bad += 1
                    print(f"PROBLEM {p.relative_to(ROOT)} line {i}: UAD codes must be C1-C6 / Q1-Q6 or blank")
    print("all ledger files OK" if not bad else f"{bad} file(s) need fixing")
    sys.exit(1 if bad else 0)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("event"); s.add_argument("--assignment", required=True); s.add_argument("--stage", required=True)
    s.add_argument("--event", required=True); s.add_argument("--note", default=""); s.set_defaults(f=cmd_event)
    s = sp.add_parser("line")
    for k in ("--billing-bbl", "--address", "--line", "--confidence", "--assignment"):
        s.add_argument(k, required=True)
    s.add_argument("--labeled", type=float, required=True); s.add_argument("--convention-sqft", type=float)
    s.add_argument("--convention"); s.add_argument("--listed", type=float); s.add_argument("--floors")
    s.add_argument("--plan-source-type", default=""); s.add_argument("--plan-date")
    s.add_argument("--subject", action="store_true"); s.add_argument("--reused", action="store_true")
    s.set_defaults(f=cmd_line)
    s = sp.add_parser("correction")
    for k in ("--assignment", "--stage", "--found-by", "--category", "--field", "--ai-value", "--corrected-value"):
        s.add_argument(k, required=True)
    s.add_argument("--subject", action="store_true"); s.add_argument("--billing-bbl"); s.add_argument("--line")
    s.add_argument("--note", default=""); s.set_defaults(f=cmd_correction)
    s = sp.add_parser("error"); s.add_argument("--assignment", required=True); s.add_argument("--stage", required=True)
    s.add_argument("--code", required=True); s.add_argument("--detail", default=""); s.set_defaults(f=cmd_error)
    s = sp.add_parser("adjustment")
    for k in ("--assignment", "--candidate", "--feature", "--amount"):
        s.add_argument(k, required=True)
    s.add_argument("--note", default=""); s.set_defaults(f=cmd_adjustment)
    s = sp.add_parser("value"); s.add_argument("--assignment", required=True); s.add_argument("--final-value", required=True)
    s.add_argument("--approach", default="sales_comparison", choices=["sales_comparison", "reconciled", "other"])
    s.add_argument("--note", default=""); s.set_defaults(f=cmd_value)
    s = sp.add_parser("check"); s.set_defaults(f=cmd_check)
    a = p.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
