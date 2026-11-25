#!/usr/bin/env python3
"""
export_learning.py - build the learning export for the product maintainer.

The appraiser runs this, reviews what it wrote, and approves it. Nothing is sent
anywhere: the approved zip sits in exports/ and the appraiser sends it to the
maintainer himself.

Commands (run from the Appraisal Comps folder)
  build     writes exports/_pending/<date>/ (README.md, MANIFEST.md, tables/, raw/)
            and prints the manifest. Running it again the same day starts over.
  approve   only after the appraiser has reviewed the pending export: zips it to
            exports/<date>_learning_<name>.zip and logs it in exports/export_log.csv.
            One approved export per day.

His name comes from settings/export.json, saved by setup_workspace.py --name.

The zip holds one folder, <date>_learning_<name>/:
  README.md    what every table and column means, how the tables join, what raw/ holds
               (generated from TABLES below, so it always matches the tables)
  MANIFEST.md  date, plugin version, his name, row counts, raw file counts, open assignments left out
  tables/      one CSV per entry in TABLES, built from the closed assignments and shared/
  raw/         assignments/<folder>/ (every file of each closed assignment, plan images included),
               shared/ (all of it) and settings/conventions.md

Only closed assignments (stage 6 done, or ticked in progress.md) go into tables/ and raw/assignments/;
raw/shared/ is shared/ whole, open assignments' rows included. settings/export.json,
settings/plugin-state.json and exports/ never do: they describe the tool, not his work.
"""
from __future__ import annotations

import argparse, csv, fnmatch, json, re, shutil, sys, zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_name import is_valid_saved_name
from template_copy import copy_missing
from workspace import plugin_version, require_workspace

ROOT = require_workspace(Path.cwd())
SETTINGS = ROOT / "settings" / "export.json"
EXPORTS = ROOT / "exports"
PENDING = EXPORTS / "_pending"
TODAY = date.today().isoformat()
VERSION = plugin_version()
PENDING_STATUS = "Status: PENDING appraiser review. Nothing has been sent."
RUN_SETUP_HINT = "Run setup again with your name (setup_workspace.py --into <this folder> --name <name>)"
NO_NAME_MESSAGE = f"no name saved. {RUN_SETUP_HINT}"


@dataclass
class TableSpec:
    file_name: str
    description: str
    source: str
    columns: list[tuple[str, str]]

    def header(self) -> list[str]:
        return [column_name for column_name, _meaning in self.columns]


ASSIGNMENT_COLUMN = ("assignment", "the assignment's folder name; its files are in raw/assignments/<assignment>/")

TABLES: list[TableSpec] = [
    TableSpec("assignments.csv", "One row per closed assignment: the subject, the search, the counts, his value.",
              "<folder>/subject.json, data/ranking.csv, data/conclusion.csv, plans/, errors.csv, shared/corrections.csv",
              [ASSIGNMENT_COLUMN,
               ("address", "subject street address"),
               ("unit", "subject unit (blank for a house)"),
               ("bbl", "subject BBL (the unit lot for a condo, the building lot for a co-op or house)"),
               ("billing_bbl", "the building's billing BBL (condos and co-ops)"),
               ("effective_date", "effective date of the appraisal (YYYY-MM-DD)"),
               ("borough", "MN, BX, BK, QN or SI, from the BBL's first digit"),
               ("building_type", "co-op, condo, single-family or 2-4 family"),
               ("beds", "subject bedrooms"),
               ("baths", "subject baths (half-bath convention, e.g. 1.5)"),
               ("size_sqft", "subject size: gla_sqft for a house, else interior_sqft_stated"),
               ("year_built", "PLUTO year built"),
               ("residential_units", "PLUTO residential units in the building"),
               ("radius_mi", "comp search radius in miles"),
               ("lookback_months", "months from the search start date to the effective date"),
               ("candidates_ranked", "rows in data/ranking.csv"),
               ("same_building_candidates", "candidates in the subject's building"),
               ("proposed", "candidates the bracket script proposed"),
               ("appraiser_removed", "proposed comps he removed"),
               ("appraiser_added", "comps he added"),
               ("used_in_grid", "comps used in the final grid"),
               ("plans_measured", "plans or sketches measured (one per stem in plans/)"),
               ("corrections", "rows for this assignment in corrections.csv"),
               ("subject_uad_condition", "subject UAD condition code (C1-C6), from him"),
               ("subject_uad_quality", "subject UAD quality code (Q1-Q6), from him"),
               ("errors_logged", "rows in the assignment's errors.csv"),
               ("final_value", "his final value (last row of data/conclusion.csv)"),
               ("value_approach", "the approach he named for the final value"),
               ("value_per_subject_sqft", "final_value / size_sqft, rounded"),
               ("inspection_notes", "inspection notes from subject.json, as written"),
               ("version", "plugin version that built this export")]),
    TableSpec("comp_candidates.csv", "One row per comp candidate the ranking scored, included and excluded.",
              "<folder>/data/ranking.csv (written by the comp-pool skill)",
              [ASSIGNMENT_COLUMN,
               ("candidate_id", "c01, c02, ... within the assignment"),
               ("bbl", "comp BBL"),
               ("address", "comp street address"),
               ("apt", "comp unit"),
               ("same_building", "Y when the comp is in the subject's building"),
               ("distance_mi", "distance from the subject in miles"),
               ("sale_date", "sale date (YYYY-MM-DD)"),
               ("sale_price", "sale price from the DOF row"),
               ("source", "where the sale came from"),
               ("acris_link", "ACRIS deed link (condos and houses)"),
               ("months_before_effective", "months from the sale to the effective date"),
               ("listed_or_measured_sqft", "comp size used in the ranking"),
               ("size_diff_pct", "size difference from the subject, percent"),
               ("beds", "comp bedrooms"),
               ("bed_diff", "comp bedrooms minus subject bedrooms"),
               ("baths", "comp baths"),
               ("bath_diff", "comp baths minus subject baths"),
               ("uad_condition", "comp UAD condition code, from him"),
               ("uad_quality", "comp UAD quality code, from him"),
               ("score", "ranking score"),
               ("rank", "rank by score"),
               ("proposed", "Y when the bracket script proposed it"),
               ("appraiser_decision", "approved, removed or added (his choice)"),
               ("exclusion_reason", "short code for why it was excluded"),
               ("used_in_grid", "Y when it is in the final grid"),
               ("property_type", "co-op, condo, single-family or 2-4 family"),
               ("floor", "comp floor, from a source"),
               ("lot_sqft", "lot size"),
               ("year_built", "comp year built"),
               ("units", "residential units in the comp's building"),
               ("size_basis", "where listed_or_measured_sqft came from (measured, listed, MLS, ...)"),
               ("listed_sqft", "the listed size kept when a measured size replaced it"),
               ("listed_basis", "the basis of listed_sqft"),
               ("subject_prior_sale", "Y when the comp is the subject's own earlier sale")]),
    TableSpec("adjustments.csv", "One row per adjustment he made to a comp.",
              "<folder>/data/adjustments.csv (ledger.py adjustment)",
              [ASSIGNMENT_COLUMN,
               ("candidate_id", "the comp, joins to comp_candidates.csv"),
               ("comp_address", "comp street address"),
               ("comp_apt", "comp unit"),
               ("feature", "what he adjusted for (e.g. Size)"),
               ("amount", "adjustment in dollars, negative for down"),
               ("note", "his note, as written"),
               ("recorded_on", "date it was recorded"),
               ("version", "plugin version that recorded it")]),
    TableSpec("adjusted_comps.csv", "One row per adjusted comp: totals computed by this script from adjustments.csv.",
              "computed from <folder>/data/adjustments.csv and data/ranking.csv",
              [ASSIGNMENT_COLUMN,
               ("candidate_id", "the comp, joins to comp_candidates.csv"),
               ("sale_price", "comp sale price"),
               ("net_adjustment", "sum of his adjustments"),
               ("gross_adjustment", "sum of the absolute adjustments"),
               ("net_pct", "net_adjustment / sale_price, percent"),
               ("gross_pct", "gross_adjustment / sale_price, percent"),
               ("adjusted_price", "sale_price + net_adjustment"),
               ("computed_by", "always export_learning.py")]),
    TableSpec("corrections.csv", "One row per correction to the agent's work (by him, the reviewer or a script).",
              "shared/corrections.csv (ledger.py correction)",
              [ASSIGNMENT_COLUMN,
               ("date", "date of the correction"),
               ("stage", "workflow stage (1, 2-3, 4, 5, 6)"),
               ("found_by", "appraiser, reviewer or script"),
               ("category", "what kind of miss (digit, room_missing, comp_rank, ...)"),
               ("is_subject", "Y when it was about the subject"),
               ("billing_bbl", "building, when it was about a line measurement"),
               ("line", "line letter or number"),
               ("field", "what was corrected"),
               ("ai_value", "the agent's value"),
               ("corrected_value", "the corrected value"),
               ("note", "note, as written"),
               ("version", "plugin version")]),
    TableSpec("lines.csv", "One row per line measurement (subject and comps).",
              "shared/buildings/lines.csv (ledger.py line)",
              [ASSIGNMENT_COLUMN,
               ("billing_bbl", "building billing BBL"),
               ("building_address", "building street address"),
               ("line", "line letter or number"),
               ("floors", "floors the line covers"),
               ("labeled_sqft", "labeled rooms total"),
               ("convention_sqft", "total under his convention"),
               ("convention", "labeled or outline"),
               ("listed_sqft", "listed size"),
               ("confidence", "HIGH, MEDIUM or LOW"),
               ("plan_source_type", "kind of plan it was read from"),
               ("plan_date", "date of the plan"),
               ("measured_on", "date measured"),
               ("is_subject", "Y for the subject's line"),
               ("reused", "Y when an earlier measurement was reused"),
               ("version", "plugin version")]),
    TableSpec("errors.csv", "One row per structured error the agent logged.",
              "<folder>/errors.csv (ledger.py error)",
              [ASSIGNMENT_COLUMN,
               ("timestamp", "when it was logged"),
               ("stage", "workflow stage"),
               ("code", "error code (ledger.py ERROR_CODES)"),
               ("detail", "short fact"),
               ("version", "plugin version")]),
    TableSpec("timing.csv", "One row per stage per assignment: minutes working and minutes waiting for him.",
              "<folder>/events.csv, else the timestamps in <folder>/log.md",
              [ASSIGNMENT_COLUMN,
               ("stage", "workflow stage"),
               ("active_min", "minutes from start to waiting (or done)"),
               ("wait_for_appraiser_min", "minutes from waiting to approved"),
               ("version", "plugin version"),
               ("source", "events.csv or log.md timestamps")]),
    TableSpec("plan_readings.csv", "One row per room per reading of each measured plan (first and blind review).",
              "<folder>/plans/<stem>_rooms.csv and <stem>_rooms_review.csv",
              [("plan", "<assignment>/<stem>; the plan's files are raw/assignments/<assignment>/plans/<stem>*"),
               ASSIGNMENT_COLUMN,
               ("is_subject", "Y when the stem starts with subject"),
               ("reading", "first or review"),
               ("room", "room label"),
               ("dims", "dimension string as read"),
               ("subtract", "area subtracted"),
               ("flag", "flag raised on the reading"),
               ("overlaps", "rooms it overlaps")]),
    TableSpec("plan_results.csv", "One row per measured plan: the totals from its result file.",
              "<folder>/plans/<stem>_result.md",
              [("plan", "<assignment>/<stem>, joins to plan_readings.csv"),
               ASSIGNMENT_COLUMN,
               ("is_subject", "Y when the stem starts with subject"),
               ("labeled_sqft", "(a) labeled rooms total"),
               ("convention_sqft", "(c) total under his convention"),
               ("outline_sqft", "(d) outline total"),
               ("listed_diff_pct", "difference from the listed size, percent"),
               ("confidence_ceiling", "confidence ceiling from the checks"),
               ("checks_raised", "number of checks listed")]),
    TableSpec("building_notes.csv", "One row per building page with notes.",
              "shared/buildings/<billing_bbl>.md, the Notes section",
              [("billing_bbl", "building billing BBL, joins to lines.csv"),
               ("notes", "the page's notes, as written")]),
    TableSpec("evals_runs.csv", "One row per eval run of the plan measurement.",
              "shared/evals/runs.csv",
              [("plan_id", "eval plan, joins to evals_calibration.csv"),
               ("run", "run number"),
               ("model", "model that ran it"),
               ("measured_sqft", "size it measured"),
               ("confidence", "confidence it gave"),
               ("date", "date of the run")]),
    TableSpec("evals_calibration.csv", "One row per eval plan, with his manual size.",
              "shared/evals/calibration.csv",
              [("plan_id", "eval plan"),
               ("plan_file", "plan file name, in raw/shared/evals/plans/"),
               ("plan_type", "kind of plan"),
               ("manual_sqft", "his manual measurement"),
               ("notes", "notes, as written")]),
]

JOINS = [
    "`assignment` is the folder name on every table that has it, and the folder under `raw/assignments/`.",
    "`comp_candidates`, `adjustments` and `adjusted_comps` join on `assignment` + `candidate_id`.",
    "`plan_readings` and `plan_results` join on `plan` (`<assignment>/<stem>`).",
    "`lines` and `building_notes` join on `billing_bbl`; `assignments.billing_bbl` links a subject to its building.",
    "`evals_runs` and `evals_calibration` join on `plan_id`.",
]

@dataclass(frozen=True)
class ExcludedPath:
    pattern: str    # fnmatch, against the POSIX path relative to the assignment folder, or to the workspace for settings/
    owner: str      # the backlog item that owns the rule
    reason: str


EXCLUDED = (
    ExcludedPath("data/cloud/*.txt", "B1", "page text from a cloud read"),
    ExcludedPath("data/cloud/*.pdf", "B1", "a printed page from a cloud read"),
    ExcludedPath("data/cloud/*.json", "B1", "records with quotes from a page; open.json"),
    ExcludedPath("settings/browser-use.key", "B1", "the browser-use API key"),
)


def is_excluded(relative_path: Path) -> bool:
    """True when the export must not copy this file."""
    posix_path = relative_path.as_posix()
    return any(fnmatch.fnmatchcase(posix_path, rule.pattern) for rule in EXCLUDED)


RAW_SECTION = [
    "- `raw/assignments/<folder>/`: every file of each closed assignment, as it was: `subject.json`, "
    "`progress.md`, `log.md`, `events.csv`, `errors.csv`, `data/` (ranking, adjustments, conclusion) and "
    "`plans/` (plan images, room readings, results).",
    "- `raw/assignments/<assignment>/data/cloud/runs.csv`: one row per cloud read (site, status, cost, tokens, "
    "seconds). The pages and records themselves stay in his folder.",
    "- `raw/shared/`: all of `shared/`: building pages and `lines.csv`, `corrections.csv`, `lessons.md`, "
    "`evals/` with its plans.",
    "- `raw/settings/conventions.md`: the conventions he worked under.",
    "",
    "Open a raw file when a table row needs checking; read the tables first.",
]


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def export_name(settings: dict[str, object]) -> str:
    """His name: `name`, else a 0.7.0 `contributor` code. `mode` and `salt` are ignored.

    The name goes into the zip's file name, so it must pass setup's name rule (or be a 0.7.0 code like A-001).
    """
    for key in ("name", "contributor"):
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            if not is_valid_saved_name(name):
                die(f"the saved name {name!r} isn't a valid name. {RUN_SETUP_HINT}")
            return name
    die(NO_NAME_MESSAGE)


def load_export_name() -> str:
    if not SETTINGS.exists():
        die(NO_NAME_MESSAGE)
    try:
        settings: object = json.loads(SETTINGS.read_text())
    except json.JSONDecodeError:
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    return export_name(settings)


def write_table(path: Path, table: TableSpec, rows: list[dict[str, object]]) -> int:
    """Write rows with the table's header, even when there are no rows."""
    with open(path, "w", newline="") as table_file:
        writer = csv.DictWriter(table_file, fieldnames=table.header(), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_csv(p):
    if not p.exists() or p.stat().st_size == 0:
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def months_between(a, b):
    try:
        da, db = date.fromisoformat(a[:10]), date.fromisoformat(b[:10])
    except (TypeError, ValueError):
        return ""
    return round((db - da).days / 30.44, 1)


def norm(s):
    return re.sub(r"\s+", " ", (s or "").upper()).strip()


def closed(d):
    ev = read_csv(d / "events.csv")
    if any(e.get("stage") == "6" and e.get("event") == "done" for e in ev):
        return True
    p = d / "progress.md"
    return p.exists() and re.search(r"- \[x\] 6", p.read_text(), re.I) is not None


def timing_rows(d, ref):
    ev = read_csv(d / "events.csv")
    out = []
    if ev:
        by = defaultdict(dict)
        for e in ev:
            try:
                t = datetime.fromisoformat(e["timestamp"])
            except (KeyError, ValueError):
                continue
            by[e["stage"]].setdefault(e["event"], t)
            by[e["stage"]]["version"] = e.get("version", "")
        for st, x in by.items():
            end_work = x.get("waiting") or x.get("done")
            active = round((end_work - x["start"]).total_seconds() / 60) if x.get("start") and end_work else ""
            wait = round((x["approved"] - x["waiting"]).total_seconds() / 60) if x.get("waiting") and x.get("approved") else ""
            out.append({"assignment": ref, "stage": st, "active_min": active, "wait_for_appraiser_min": wait,
                        "version": x.get("version", ""), "source": "events.csv"})
        return out
    # fallback: first and last timestamp per stage in log.md (only the timestamp and stage columns are read)
    log = d / "log.md"
    spans = {}
    if log.exists():
        for line in log.read_text().splitlines():
            m = re.match(r"\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*\|\s*([^|]+?)\s*\|", line)
            if m:
                t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
                st = m.group(2)[:12]
                a, b = spans.get(st, (t, t))
                spans[st] = (min(a, t), max(b, t))
    for st, (a, b) in spans.items():
        out.append({"assignment": ref, "stage": st, "active_min": round((b - a).total_seconds() / 60),
                    "wait_for_appraiser_min": "", "version": "", "source": "log.md timestamps"})
    return out


def parse_result(p):
    t = p.read_text()
    g = lambda rx: (re.search(rx, t).group(1) if re.search(rx, t) else "")
    return {"labeled_sqft": g(r"\(a\) Labeled rooms total: ([\d.]+)"),
            "convention_sqft": g(r"\(c\) Total under convention[^:]*: (\d+)"),
            "outline_sqft": g(r"\(d\) Outline total: (\d+)"),
            "listed_diff_pct": g(r"listed is [+-]?\d+ sq ft \(([+-][\d.]+)%\)"),
            "confidence_ceiling": g(r"Confidence ceiling from checks: (\w+)"),
            "checks_raised": str(t.count("\n- "))}


PLAN_INPUT_SUFFIXES = ("_rooms.csv", "_outline.csv", "_sketch.csv")


def measured_plan_stems(plans_dir):
    """One stem per measured plan or sketch (e.g. 'subject', '12b'), whatever input it was read into."""
    stems = set()
    if not plans_dir.exists():
        return []
    for f in plans_dir.iterdir():
        for suffix in PLAN_INPUT_SUFFIXES:
            if f.name.endswith(suffix):
                stems.add(f.name[:-len(suffix)])
    return sorted(stems)




def is_yes(value: object) -> bool:
    return value in ("Y", "True", "1")


def subject_size(subject: dict) -> object:
    """Houses carry their living area in gla_sqft; apartments in interior_sqft_stated."""
    if num(subject.get("gla_sqft")) is not None:
        return subject.get("gla_sqft")
    if num(subject.get("interior_sqft_stated")) is not None:
        return subject.get("interior_sqft_stated")
    return ""


def is_subject_prior_sale(candidate: dict, subject: dict) -> bool:
    subject_bbl = re.sub(r"\D", "", str(subject.get("bbl") or ""))
    subject_unit_bbl = subject_bbl if len(subject_bbl) == 10 and int(subject_bbl[6:]) >= 1001 else ""
    if subject_unit_bbl and candidate.get("bbl") == subject_unit_bbl:
        return True
    subject_address = norm(subject.get("address"))
    subject_unit = norm(subject.get("unit"))
    if not (subject_address and subject_unit):
        return False
    return norm(candidate.get("address")) == subject_address and norm(candidate.get("apt")) == subject_unit


def assignment_row(folder: Path, subject: dict, ranking: list[dict], plans: list[str],
                   corrections: list[dict]) -> dict[str, object]:
    pluto = subject.get("pluto") or {}
    search = subject.get("search") or {}
    effective_date = subject.get("effective_date") or ""
    since = search.get("since", "")
    size = subject_size(subject)
    row: dict[str, object] = {
        "assignment": folder.name,
        "address": subject.get("address", ""), "unit": subject.get("unit", ""),
        "bbl": subject.get("bbl", ""), "billing_bbl": subject.get("billing_bbl", ""),
        "effective_date": effective_date,
        "borough": {"1": "MN", "2": "BX", "3": "BK", "4": "QN", "5": "SI"}.get(str(subject.get("bbl", " "))[0], ""),
        "building_type": subject.get("building_type", ""), "beds": subject.get("beds", ""),
        "baths": subject.get("baths", ""),
        "size_sqft": size, "year_built": pluto.get("yearbuilt", ""), "residential_units": pluto.get("unitsres", ""),
        "radius_mi": search.get("radius_mi", ""),
        "lookback_months": months_between(since, effective_date) if since and effective_date else "",
        "candidates_ranked": len(ranking),
        "same_building_candidates": sum(is_yes(r.get("same_building")) for r in ranking),
        "proposed": sum(is_yes(r.get("proposed")) for r in ranking),
        "appraiser_removed": sum(r.get("appraiser_decision") == "removed" for r in ranking),
        "appraiser_added": sum(r.get("appraiser_decision") == "added" for r in ranking),
        "used_in_grid": sum(is_yes(r.get("used_in_grid")) for r in ranking),
        "plans_measured": len(plans),
        "corrections": sum(1 for c in corrections if c.get("assignment_ref") == folder.name),
        "subject_uad_condition": subject.get("uad_condition", ""),
        "subject_uad_quality": subject.get("uad_quality", ""),
        "errors_logged": len(read_csv(folder / "errors.csv")),
        "final_value": "", "value_approach": "", "value_per_subject_sqft": "",
        "inspection_notes": subject.get("inspection_notes") or "",
        "version": VERSION}
    conclusion = read_csv(folder / "data" / "conclusion.csv")
    if conclusion:
        final_value = num(conclusion[-1].get("final_value"))
        size_number = num(size)
        row["final_value"] = conclusion[-1].get("final_value", "")
        row["value_approach"] = conclusion[-1].get("approach", "")
        if final_value and size_number:
            row["value_per_subject_sqft"] = round(final_value / size_number)
    return row


def candidate_rows(folder: Path, subject: dict, ranking: list[dict]) -> list[dict[str, object]]:
    effective_date = subject.get("effective_date") or ""
    rows = []
    for candidate in ranking:
        row: dict[str, object] = dict(candidate)
        row["assignment"] = folder.name
        if not candidate.get("months_before_effective"):
            row["months_before_effective"] = months_between(candidate.get("sale_date", ""), effective_date)
        row["subject_prior_sale"] = "Y" if is_subject_prior_sale(candidate, subject) else "N"
        rows.append(row)
    return rows


def adjustment_rows(folder: Path, ranking: list[dict]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """His adjustment lines, and per comp the net, gross and adjusted price computed here."""
    candidates_by_id = {candidate.get("candidate_id", ""): candidate for candidate in ranking}
    lines, totals = [], []
    net_by_id: dict[str, float] = defaultdict(float)
    gross_by_id: dict[str, float] = defaultdict(float)
    for adjustment in read_csv(folder / "data" / "adjustments.csv"):
        candidate_id = adjustment.get("candidate_id", "")
        candidate = candidates_by_id.get(candidate_id, {})
        amount = num(adjustment.get("amount")) or 0
        net_by_id[candidate_id] += amount
        gross_by_id[candidate_id] += abs(amount)
        lines.append({"assignment": folder.name, "candidate_id": candidate_id,
                      "comp_address": candidate.get("address", ""), "comp_apt": candidate.get("apt", ""),
                      "feature": adjustment.get("feature", ""), "amount": adjustment.get("amount", ""),
                      "note": adjustment.get("note", ""), "recorded_on": adjustment.get("recorded_on", ""),
                      "version": adjustment.get("version", "")})
    for candidate_id, net in net_by_id.items():
        gross = gross_by_id[candidate_id]
        price = num(candidates_by_id.get(candidate_id, {}).get("sale_price"))
        totals.append({"assignment": folder.name, "candidate_id": candidate_id,
                       "sale_price": round(price) if price else "",
                       "net_adjustment": round(net), "gross_adjustment": round(gross),
                       "net_pct": round(net / price * 100, 1) if price else "",
                       "gross_pct": round(gross / price * 100, 1) if price else "",
                       "adjusted_price": round(price + net) if price else "",
                       "computed_by": "export_learning.py from the appraiser's entries"})
    return lines, totals


def plan_rows(folder: Path, plans: list[str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    readings, results = [], []
    for stem in plans:
        plan_key = f"{folder.name}/{stem}"
        is_subject = "Y" if stem.lower().startswith("subject") else "N"
        first_reading = folder / "plans" / f"{stem}_rooms.csv"
        review_reading = folder / "plans" / f"{stem}_rooms_review.csv"
        for reading, rooms_file in (("first", first_reading), ("review", review_reading)):
            for room in read_csv(rooms_file):
                readings.append({"plan": plan_key, "assignment": folder.name, "is_subject": is_subject,
                                 "reading": reading, "room": room.get("room", ""), "dims": room.get("dims", ""),
                                 "subtract": room.get("subtract", ""), "flag": room.get("flag", ""),
                                 "overlaps": room.get("overlaps", "")})
        result_file = folder / "plans" / f"{stem}_result.md"
        if result_file.exists():
            results.append({"plan": plan_key, "assignment": folder.name, "is_subject": is_subject,
                            **parse_result(result_file)})
    return readings, results


def with_assignment(rows: list[dict], open_folders: set[str]) -> list[dict[str, object]]:
    """Shared rows keyed by `assignment` (renamed from assignment_ref), minus rows of open assignments."""
    kept = []
    for row in rows:
        new_row: dict[str, object] = dict(row)
        assignment = str(new_row.pop("assignment_ref", "") or "")
        if assignment in open_folders:
            continue
        new_row["assignment"] = assignment
        kept.append(new_row)
    return kept


def building_note_rows() -> list[dict[str, object]]:
    rows = []
    for page in sorted((ROOT / "shared" / "buildings").glob("*.md")):
        match = re.search(r"## Notes\s*\n(.*)", page.read_text(), re.S)
        body = match.group(1) if match else ""
        note_lines = [line for line in body.splitlines() if line.strip() and not line.startswith("(Combinations")]
        notes = "\n".join(note_lines).strip()
        if notes:
            rows.append({"billing_bbl": page.stem, "notes": notes})
    return rows


def copy_raw(out: Path, closed_folders: list[Path]) -> dict[str, int]:
    """Copy each closed assignment, shared/ and conventions.md (contents only). Returns files per raw folder."""
    file_counts: dict[str, int] = {}
    for folder in closed_folders:
        copied = copy_missing(folder, out / "raw" / "assignments" / folder.name, skip=is_excluded)
        file_counts[f"assignments/{folder.name}"] = len(copied)
    shared = ROOT / "shared"
    if shared.is_dir():
        file_counts["shared"] = len(copy_missing(shared, out / "raw" / "shared"))
    conventions = ROOT / "settings" / "conventions.md"
    if conventions.exists() and not is_excluded(Path("settings") / "conventions.md"):
        target = out / "raw" / "settings" / "conventions.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(conventions, target)
        file_counts["settings"] = 1
    return file_counts


def write_readme(out: Path) -> None:
    lines = [f"# Learning export {TODAY}", "",
             "Everything from the appraiser's closed assignments, for reviewing and improving the appraisal-comps "
             "workflow. Read `MANIFEST.md` for what came in, then the tables, then `raw/` when a row needs checking.",
             "", "## tables/", ""]
    for table in TABLES:
        lines += [f"### {table.file_name}", "", table.description, "", f"Source: {table.source}", "",
                  "| Column | Meaning |", "| --- | --- |"]
        lines += [f"| {column_name} | {meaning} |" for column_name, meaning in table.columns]
        lines.append("")
    lines += ["## Joins", ""] + [f"- {join}" for join in JOINS]
    lines += ["", "## raw/", ""] + RAW_SECTION
    (out / "README.md").write_text("\n".join(lines) + "\n")


def cmd_build(args: argparse.Namespace) -> None:
    name = load_export_name()
    out = PENDING / TODAY
    if out.exists():
        shutil.rmtree(out)
    (out / "tables").mkdir(parents=True)

    closed_folders, open_folders = [], set()
    for folder in sorted((ROOT / "assignments").glob("*")):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        if closed(folder):
            closed_folders.append(folder)
        else:
            open_folders.add(folder.name)

    corrections = read_csv(ROOT / "shared" / "corrections.csv")
    rows_by_table: dict[str, list] = {table.file_name: [] for table in TABLES}
    for folder in closed_folders:
        subject_file = folder / "subject.json"
        subject = json.loads(subject_file.read_text() or "{}") if subject_file.exists() else {}
        ranking = read_csv(folder / "data" / "ranking.csv")
        plans = measured_plan_stems(folder / "plans")
        rows_by_table["assignments.csv"].append(assignment_row(folder, subject, ranking, plans, corrections))
        rows_by_table["comp_candidates.csv"] += candidate_rows(folder, subject, ranking)
        adjustment_lines, adjusted_comps = adjustment_rows(folder, ranking)
        rows_by_table["adjustments.csv"] += adjustment_lines
        rows_by_table["adjusted_comps.csv"] += adjusted_comps
        for error in read_csv(folder / "errors.csv"):
            rows_by_table["errors.csv"].append({**error, "assignment": folder.name})
        rows_by_table["timing.csv"] += timing_rows(folder, folder.name)
        readings, results = plan_rows(folder, plans)
        rows_by_table["plan_readings.csv"] += readings
        rows_by_table["plan_results.csv"] += results

    rows_by_table["corrections.csv"] = with_assignment(corrections, open_folders)
    rows_by_table["lines.csv"] = with_assignment(read_csv(ROOT / "shared" / "buildings" / "lines.csv"), open_folders)
    rows_by_table["building_notes.csv"] = building_note_rows()
    rows_by_table["evals_runs.csv"] = read_csv(ROOT / "shared" / "evals" / "runs.csv")
    rows_by_table["evals_calibration.csv"] = read_csv(ROOT / "shared" / "evals" / "calibration.csv")

    row_counts = {table.file_name: write_table(out / "tables" / table.file_name, table,
                                               rows_by_table[table.file_name]) for table in TABLES}
    file_counts = copy_raw(out, closed_folders)
    write_readme(out)

    manifest = [f"# Learning export {TODAY}", "",
                f"Name: {name} | Plugin version: {VERSION} | Built: {datetime.now().isoformat(timespec='minutes')}",
                "", PENDING_STATUS, "", "## tables/", "", "| File | Rows |", "| --- | --- |"]
    manifest += [f"| {file_name} | {count} |" for file_name, count in row_counts.items()]
    manifest += ["", "## raw/", "", "| Folder | Files |", "| --- | --- |"]
    manifest += [f"| {folder_name} | {count} |" for folder_name, count in file_counts.items()]
    manifest += ["", f"Closed assignments in: {len(closed_folders)}", f"Open assignments left out: {len(open_folders)}"]
    (out / "MANIFEST.md").write_text("\n".join(manifest) + "\n")
    print("\n".join(manifest))
    print(f"\nPending export: {out.relative_to(ROOT)}")


def cmd_approve(args: argparse.Namespace) -> None:
    name = load_export_name()
    out = PENDING / TODAY
    manifest = out / "MANIFEST.md"
    if not manifest.exists():
        die("no pending export for today. Run build first")
    final = EXPORTS / f"{TODAY}_learning_{name}"
    if final.exists():
        die(f"{final.name} already exists. One approved export per day")
    approved_status = (f"Status: APPROVED by appraiser {datetime.now().isoformat(timespec='minutes')}. "
                       "Not sent; the appraiser sends it.")
    manifest.write_text(manifest.read_text().replace(PENDING_STATUS, approved_status))
    shutil.move(str(out), str(final))
    files = sorted(path for path in final.rglob("*") if path.is_file())
    zip_path = EXPORTS / f"{final.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for path in files:
            zip_file.write(path, path.relative_to(EXPORTS).as_posix())
    log = EXPORTS / "export_log.csv"
    is_new_log = not log.exists()
    with open(log, "a", newline="") as log_file:
        writer = csv.writer(log_file)
        if is_new_log:
            writer.writerow(["date", "file", "version", "files"])
        writer.writerow([TODAY, zip_path.name, VERSION, len(files)])
    print(f"Approved: {zip_path.relative_to(ROOT)}. Send it to the maintainer yourself; this tool does not send anything.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("build").set_defaults(run=cmd_build)
    commands.add_parser("approve").set_defaults(run=cmd_approve)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
