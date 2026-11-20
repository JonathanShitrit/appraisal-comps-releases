#!/usr/bin/env python3
"""
bracket.py - propose comps by bracketing the subject's features, and show the coverage.

The score in ranking.csv only sets the order candidates are considered in. This
script takes the top 3 eligible candidates by rank, then walks down the ranked
list and adds a candidate only when it closes an open gap. A feature is
bracketed when one proposed comp is at or above the subject and one is at or
below (equal covers both). Unknown values never count. Arithmetic and
comparisons happen here, not in the model.

Commands
  propose --assignment <absolute folder>
          Reads subject.json and data/ranking.csv, rewrites the proposed column
          (Y/N, every other column kept in order) and writes data/bracket.md.
  check   --assignment <absolute folder> --comps c01,c03,...
          Prints the coverage for those comps. Changes no file.

Features by subject building_type
  co-op, condo     size, beds, baths, floor, condition, quality
  single-family    size, lot, beds, baths, year_built, condition, quality
  2-4 family       the single-family list plus units

Columns read from ranking.csv: candidate_id, rank, exclusion_reason, proposed,
listed_or_measured_sqft (basis in size_basis), lot_sqft, beds, baths, floor,
year_built, units, uad_condition, uad_quality. Others are kept but not read.

Values
  Blank, "not in source" and "unknown" mean unknown. So does 0 for size, lot,
  year_built and units (DOF and PLUTO write 0 for "not recorded").
  Baths use the half-bath convention: 2.5 = two full, one half. Any decimal
  other than .0 or .5 stops the script.
  UAD codes (C1-C6, Q1-Q6) are ordered by their digit: C1 is 1, C6 is 6.
  Size basis is one of measured, plan inside face, sketch, listed, MLS, public
  record, DOF gross. A source text that starts with one of them counts as that
  basis ("sketch (plans/subject_result.md)" is sketch). A house subject's size
  is gla_sqft only; without it, size is "can't check".
  A size bracket that rests only on public record, DOF gross or plan inside face
  values on either side is "bracketed (unverified)".

Errors print "ERROR: ..." to stderr and exit 2.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

MIN_COMPS = 3
BUILDING_TYPES = ("co-op", "condo", "single-family", "2-4 family")
APARTMENT_FEATURES = ["size", "beds", "baths", "floor", "condition", "quality"]
HOUSE_FEATURES = ["size", "lot", "beds", "baths", "year_built", "condition", "quality"]
FEATURES_BY_TYPE = {
    "co-op": APARTMENT_FEATURES,
    "condo": APARTMENT_FEATURES,
    "single-family": HOUSE_FEATURES,
    "2-4 family": HOUSE_FEATURES + ["units"],
}
# feature -> ranking.csv column
RANKING_COLUMNS = {
    "size": "listed_or_measured_sqft",
    "lot": "lot_sqft",
    "beds": "beds",
    "baths": "baths",
    "floor": "floor",
    "year_built": "year_built",
    "units": "units",
    "condition": "uad_condition",
    "quality": "uad_quality",
}
UAD_PREFIX = {"condition": "C", "quality": "Q"}
UNKNOWN_WORDS = {"", "not in source", "unknown"}
# DOF and PLUTO write 0 for "not recorded"; a studio has 0 beds, but no sale has 0 sq ft or was built in year 0
ZERO_MEANS_UNKNOWN = {"size", "lot", "year_built", "units"}
# "plan inside face": a house plan traced to the inside of its walls, short of the ANSI exterior by the wall thickness
SIZE_BASES = ("measured", "plan inside face", "sketch", "listed", "MLS", "public record", "DOF gross")
UNVERIFIED_BASES = {"public record", "DOF gross", "plan inside face"}
HOUSE_TYPES = {"single-family", "2-4 family"}
NO_BASIS = "-"


class CaseError(Exception):
    """A problem with the assignment's files that the agent or appraiser must fix."""


@dataclass
class Reading:
    value: float
    text: str
    basis: str = NO_BASIS


@dataclass
class Feature:
    name: str
    subject: Reading | None
    candidates: dict[str, Reading] = field(default_factory=dict)   # eligible candidate_id -> known reading

    @property
    def checkable(self) -> bool:
        return self.subject is not None and len(self.candidates) > 0


@dataclass
class CoverageRow:
    feature: str
    subject: str
    lowest: str
    highest: str
    basis: str
    status: str


def is_unknown(text: str) -> bool:
    return text.strip().lower() in UNKNOWN_WORDS


def parse_value(feature_name: str, raw: object, owner: str) -> Reading | None:
    """Parse one value as written in its source. Returns None when unknown."""
    if raw is None:
        return None
    text = str(raw).strip()
    if is_unknown(text):
        return None
    if feature_name in UAD_PREFIX:
        prefix = UAD_PREFIX[feature_name]
        code = text.upper()
        valid = len(code) == 2 and code[0] == prefix and code[1] in "123456"
        if not valid:
            raise CaseError(f"{feature_name} '{text}' on {owner}: use a UAD code {prefix}1 to {prefix}6")
        return Reading(float(code[1]), text)
    try:
        value = float(text.replace(",", ""))
    except ValueError:
        raise CaseError(f"{feature_name} '{text}' on {owner} is not a number. "
                        "Write the number only, or 'not in source'") from None
    if value == 0 and feature_name in ZERO_MEANS_UNKNOWN:
        return None
    if feature_name == "baths":
        fraction = value - math.floor(value)
        if fraction not in (0.0, 0.5):
            raise CaseError(f"baths '{text}' on {owner}: use the half-bath convention (2.5 = two full, one half)")
    return Reading(value, text)


def basis_of(source_text: str) -> str:
    """The size basis a source names, e.g. 'sketch (plans/subject_result.md)' -> 'sketch'."""
    text = source_text.strip()
    for basis in SIZE_BASES:
        if text.lower().startswith(basis.lower()):
            return basis
    if text.lower().startswith("listing"):
        return "listed"
    return text or "not in source"


def subject_raw(subject: dict, feature_name: str) -> tuple[object, str]:
    """The subject's raw value and basis for one feature, per the column mapping."""
    if feature_name == "size":
        # a house's living area comes only from its sketch or a source the appraiser names (gla_sqft)
        if subject.get("building_type") in HOUSE_TYPES:
            return subject.get("gla_sqft"), basis_of(str(subject.get("gla_source") or ""))
        return subject.get("interior_sqft_stated"), basis_of(str(subject.get("sqft_source") or ""))
    if feature_name == "year_built":
        pluto = subject.get("pluto") or {}
        return pluto.get("yearbuilt"), NO_BASIS
    subject_keys = {"lot": "lot_sqft", "condition": "uad_condition", "quality": "uad_quality"}
    return subject.get(subject_keys.get(feature_name, feature_name)), NO_BASIS


def building_type_of(subject: dict) -> str:
    building_type = str(subject.get("building_type") or "").strip()
    if building_type not in BUILDING_TYPES:
        shown = f"'{building_type}'" if building_type else "missing"
        raise CaseError(f"subject.json building_type is {shown}. Set it to one of: {', '.join(BUILDING_TYPES)}")
    return building_type


def load_case(folder: Path) -> tuple[dict, list[dict]]:
    subject_path = folder / "subject.json"
    ranking_path = folder / "data" / "ranking.csv"
    if not subject_path.exists():
        raise CaseError(f"{subject_path} not found. Give the absolute assignment folder (stage 1 writes subject.json)")
    if not ranking_path.exists():
        raise CaseError(f"{ranking_path} not found. Rank the pool first (comp-pool stage 3)")
    try:
        subject = json.loads(subject_path.read_text())
    except json.JSONDecodeError as error:
        raise CaseError(f"{subject_path} is not valid JSON ({error})") from None
    with open(ranking_path, newline="") as f:
        rows = list(csv.DictReader(f))
    for line_number, row in enumerate(rows, start=2):
        if None in row:
            raise CaseError(f"{ranking_path} line {line_number} has more cells than the header "
                            "(an unquoted comma?). Fix that row; nothing was changed")
    for required in ("candidate_id", "rank", "exclusion_reason"):
        if rows and required not in rows[0]:
            raise CaseError(f"{ranking_path} has no '{required}' column")
    return subject, rows


def not_excluded(rows: list[dict]) -> list[dict]:
    """Rows with a blank exclusion_reason, in file order. Rank is not needed (appraiser-added comps have none)."""
    return [row for row in rows if not (row.get("exclusion_reason") or "").strip()]


def eligible_rows(rows: list[dict]) -> list[dict]:
    """Rows with a blank exclusion_reason, sorted by rank."""
    eligible = not_excluded(rows)

    def rank_of(row: dict) -> float:
        rank_text = (row.get("rank") or "").strip()
        try:
            return float(rank_text)
        except ValueError:
            raise CaseError(f"rank '{rank_text}' on {row.get('candidate_id')} is not a number") from None

    return sorted(eligible, key=rank_of)


def feature_values(subject: dict, rows: list[dict], building_type: str) -> list[Feature]:
    """Known values of each feature for the subject and every eligible row."""
    features = []
    for feature_name in FEATURES_BY_TYPE[building_type]:
        raw, subject_basis = subject_raw(subject, feature_name)
        subject_reading = parse_value(feature_name, raw, "subject")
        if subject_reading is not None:
            subject_reading.basis = subject_basis
        feature = Feature(feature_name, subject_reading)
        column = RANKING_COLUMNS[feature_name]
        for row in rows:
            candidate_id = row["candidate_id"]
            reading = parse_value(feature_name, row.get(column), candidate_id)
            if reading is None:
                continue
            if feature_name == "size":
                reading.basis = basis_of(row.get("size_basis") or "")
            feature.candidates[candidate_id] = reading
        features.append(feature)
    return features


def open_sides(feature: Feature, ids: list[str]) -> tuple[bool, bool]:
    """(needs a value at or above the subject, needs one at or below) among the given comps."""
    if feature.subject is None:
        return False, False
    target = feature.subject.value
    values = [feature.candidates[i].value for i in ids if i in feature.candidates]
    has_above = any(value >= target for value in values)
    has_below = any(value <= target for value in values)
    return not has_above, not has_below


def closes_a_gap(feature: Feature, candidate_id: str, needs_above: bool, needs_below: bool) -> bool:
    if candidate_id not in feature.candidates or feature.subject is None:
        return False
    value = feature.candidates[candidate_id].value
    target = feature.subject.value
    return (needs_above and value >= target) or (needs_below and value <= target)


def propose(rows: list[dict], subject: dict) -> list[str]:
    """Candidate ids: the top 3 eligible by rank, plus any later one that closes an open gap."""
    eligible = eligible_rows(rows)
    features = [f for f in feature_values(subject, eligible, building_type_of(subject)) if f.checkable]
    proposed = [row["candidate_id"] for row in eligible[:MIN_COMPS]]
    for row in eligible[MIN_COMPS:]:
        gaps = {f.name: open_sides(f, proposed) for f in features}
        still_open = [f for f in features if any(gaps[f.name])]
        if not still_open:
            break
        candidate_id = row["candidate_id"]
        if any(closes_a_gap(f, candidate_id, *gaps[f.name]) for f in still_open):
            proposed.append(candidate_id)
    return proposed


def basis_label(feature: Feature, ids: list[str]) -> str:
    if feature.name != "size":
        return NO_BASIS
    bases = {feature.candidates[i].basis for i in ids if i in feature.candidates}
    if feature.subject is not None:
        bases.add(feature.subject.basis)
    if len(bases) == 1:
        return bases.pop()
    if not bases:
        return NO_BASIS
    return f"mixed basis ({', '.join(sorted(bases))})"


def size_rests_on_unverified(feature: Feature, ids: list[str]) -> bool:
    """True when, on either side, every comp supplying that side has an unverified basis."""
    if feature.subject is None:
        return False
    target = feature.subject.value
    readings = [feature.candidates[i] for i in ids if i in feature.candidates]
    above = [r for r in readings if r.value >= target]
    below = [r for r in readings if r.value <= target]

    def all_unverified(side: list[Reading]) -> bool:
        return all(r.basis in UNVERIFIED_BASES or r.basis == "not in source" for r in side)

    return all_unverified(above) or all_unverified(below)


def coverage(rows: list[dict], subject: dict, ids: list[str]) -> list[CoverageRow]:
    features = feature_values(subject, not_excluded(rows), building_type_of(subject))
    table = []
    for feature in features:
        subject_text = feature.subject.text if feature.subject is not None else "not in source"
        readings = [feature.candidates[i] for i in ids if i in feature.candidates]
        lowest = min(readings, key=lambda r: r.value).text if readings else NO_BASIS
        highest = max(readings, key=lambda r: r.value).text if readings else NO_BASIS
        if not feature.checkable:
            status = "can't check"
        else:
            needs_above, needs_below = open_sides(feature, ids)
            if needs_above and needs_below:
                status = "not bracketed: nothing above or below in pool"
            elif needs_above:
                status = "not bracketed: nothing above in pool"
            elif needs_below:
                status = "not bracketed: nothing below in pool"
            elif feature.name == "size" and size_rests_on_unverified(feature, ids):
                status = "bracketed (unverified)"
            else:
                status = "bracketed"
        table.append(CoverageRow(feature.name, subject_text, lowest, highest, basis_label(feature, ids), status))
    return table


def render(subject: dict, ids: list[str], table: list[CoverageRow], heading: str, eligible_count: int) -> str:
    lines = [f"{heading}: {', '.join(ids) if ids else 'none'}"]
    if eligible_count < MIN_COMPS:
        lines.append(f"only {eligible_count} eligible candidates; at least {MIN_COMPS} needed. "
                     "Widen the radius or lookback (settings/conventions.md) and re-pull")
    lines += ["", f"Coverage for {building_type_of(subject)} subject:",
              "| Feature | Subject | Lowest | Highest | Basis | Status |",
              "| --- | --- | --- | --- | --- | --- |"]
    for row in table:
        lines.append(f"| {row.feature} | {row.subject} | {row.lowest} | {row.highest} | {row.basis} | {row.status} |")
    gaps = [row.feature for row in table if row.status.startswith("not bracketed")]
    if gaps:
        lines += ["", f"Open gaps: {', '.join(gaps)}. Widen the radius or lookback and re-run; "
                      "log each gap as BRACKET_GAP. Never fill a value to close one."]
    cant_check = [row.feature for row in table if row.status == "can't check"]
    if cant_check:
        lines.append(f"Can't check (subject value unknown, or no candidate has a known value): {', '.join(cant_check)}")
    return "\n".join(lines)


def write_proposed(ranking_path: Path, proposed_ids: list[str]) -> None:
    with open(ranking_path, newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    if "proposed" not in columns:
        columns.append("proposed")
    for row in rows:
        row["proposed"] = "Y" if row["candidate_id"] in proposed_ids else "N"
    # write a temporary file first so a failed write never leaves ranking.csv half written
    temporary_path = ranking_path.with_name(ranking_path.name + ".tmp")
    with open(temporary_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary_path, ranking_path)


def run_propose(folder: Path) -> str:
    subject, rows = load_case(folder)
    proposed_ids = propose(rows, subject)
    table = coverage(rows, subject, proposed_ids)
    text = render(subject, proposed_ids, table, "Proposed", len(eligible_rows(rows)))
    write_proposed(folder / "data" / "ranking.csv", proposed_ids)
    (folder / "data" / "bracket.md").write_text(text + "\n")
    return text


def run_check(folder: Path, comps_text: str) -> str:
    subject, rows = load_case(folder)
    comp_ids = [c.strip() for c in comps_text.split(",") if c.strip()]
    known_ids = {row["candidate_id"] for row in rows}
    unknown_ids = [c for c in comp_ids if c not in known_ids]
    if unknown_ids:
        raise CaseError(f"not in ranking.csv: {', '.join(unknown_ids)}. Use candidate_id values from data/ranking.csv")
    eligible_ids = {row["candidate_id"] for row in not_excluded(rows)}
    excluded = [c for c in comp_ids if c not in eligible_ids]
    if excluded:
        raise CaseError(f"excluded in ranking.csv: {', '.join(excluded)}. Clear exclusion_reason first if "
                        "the appraiser kept them")
    table = coverage(rows, subject, comp_ids)
    return render(subject, comp_ids, table, "Grid comps", len(eligible_ids))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    propose_parser = commands.add_parser("propose", help="propose comps by bracketing; rewrites ranking.csv proposed")
    propose_parser.add_argument("--assignment", required=True, help="absolute assignment folder")
    check_parser = commands.add_parser("check", help="coverage for the given comps; changes no file")
    check_parser.add_argument("--assignment", required=True, help="absolute assignment folder")
    check_parser.add_argument("--comps", required=True, help="comma-separated candidate ids, e.g. c01,c03")
    args = parser.parse_args()

    folder = Path(args.assignment)
    try:
        if not folder.is_absolute():
            raise CaseError(f"--assignment '{args.assignment}' must be an absolute folder path")
        if args.command == "propose":
            text = run_propose(folder)
        else:
            text = run_check(folder, args.comps)
    except CaseError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
    print(text)


if __name__ == "__main__":
    main()
