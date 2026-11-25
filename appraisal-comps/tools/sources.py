#!/usr/bin/env python3
"""
sources.py - the sites Claude and the appraiser use for each job (sold
comps, tax map, orders...), for a given area.

Two layers merge for every lookup: the defaults shipped in the plugin
(product/sources/defaults.csv) and the appraiser's own list in his folder
(shared/sources.csv). His row replaces a default with the same job, area
and site. His file is created and edited only through `add`, so it always
stays valid; `check` validates both.

Commands (run from the Appraisal Comps folder)

  lookup  --job <job> --area <area>
          print the use sites to try for a job and area, most specific
          area first then rank, then an Avoid: list if any
  add     --job <job> --area <area> --site <site> --url <url> --rank <n>
          --gives "<what it gives>" [--status use|avoid] [--reader cloud|chrome]
          add or replace his row for a job, area and site, with
          added_by appraiser and today's date. The reader says who opens the
          site: cloud (cloud_read.py) or chrome (his own Chrome); it defaults
          to chrome for sites that need his login, else cloud
  list    [--job <job>] [--area <area>]
          print every row from both layers, marked default or his
  check   validate the shipped defaults and his file; non-zero exit and
          the bad rows on failure

Jobs: sold-comps, listing-details, sales-deeds, tax-map-lot, property-card,
building-footprint, orders, report.

Areas: everywhere, NYC, a county, or <county>/<town>. Counties: Bronx,
Brooklyn, Manhattan, Queens, Staten Island, Westchester, Putnam, Rockland,
Nassau, Suffolk. NYC covers the five boroughs.
"""
from __future__ import annotations

import argparse, csv, re, sys
import urllib.parse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import require_workspace

DEFAULTS_PATH = Path(__file__).resolve().parents[1] / "sources/defaults.csv"

HEADER = ["job", "area", "site", "url", "rank", "gives", "status", "added_by", "checked", "reader"]
OLD_HEADER = HEADER[:-1]   # his file from before this release: read, never written
READERS = ("cloud", "chrome")
# Sites that need his own logged-in Chrome or session: never read in the cloud.
LOCAL_ONLY_HOSTS = ("onekeymls.com", "dwellingblocks.com", "reggora.com", "appraisalport.com",
                    "valueconnect.com", "valueconnect.ca", "aivre.com")
# utf-8-sig strips a leading BOM (Excel's "CSV UTF-8" export always writes one);
# a plain file without a BOM reads the same way.
READ_ENCODING = "utf-8-sig"
JOBS = ["sold-comps", "listing-details", "sales-deeds", "tax-map-lot", "property-card",
        "building-footprint", "orders", "report"]
COUNTIES = ["Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island", "Westchester",
            "Putnam", "Rockland", "Nassau", "Suffolk"]
NYC_BOROUGHS = {"Bronx", "Brooklyn", "Manhattan", "Queens", "Staten Island"}
STATUSES = {"use", "avoid"}
ADDED_BY_VALUES = {"maintainer", "appraiser"}
AREA_ERROR = ("must be 'everywhere', 'NYC', a county, or '<county>/<town>'. "
              f"Counties: {', '.join(COUNTIES)}")


@dataclass
class SourceRow:
    """One row of a sources CSV file, plus which layer it came from."""

    job: str
    area: str
    site: str
    url: str
    rank: int
    gives: str
    status: Literal["use", "avoid"]
    added_by: Literal["maintainer", "appraiser"]
    checked: str
    reader: Literal["cloud", "chrome"]
    layer: Literal["default", "his"]


def host_of(url: str) -> str:
    """The lowercase host of url, with a leading "www." removed; "" when it has none."""
    host = urllib.parse.urlsplit(url).hostname
    if host is None:
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[len("www."):]
    return host


def is_local_only_host(host: str) -> bool:
    """True when host is, or is under, a site that is only read in his Chrome."""
    for local_only in LOCAL_ONLY_HOSTS:
        if host == local_only or host.endswith("." + local_only):
            return True
    return False


def default_reader(url: str) -> str:
    """"chrome" for a local-only host, else "cloud"."""
    if is_local_only_host(host_of(url)):
        return "chrome"
    return "cloud"


class SourceFileError(Exception):
    """path has a bad header or a bad row, at row_number, for problem."""

    def __init__(self, path: Path, row_number: int, problem: str) -> None:
        self.path = path
        self.row_number = row_number
        self.problem = problem
        super().__init__(f"{path} row {row_number}: {problem}")


def match_county(name: str) -> str | None:
    """Return the canonical spelling of name if it is one of COUNTIES
    (matched case-insensitively), else None."""
    for county in COUNTIES:
        if county.casefold() == name.casefold():
            return county
    return None


def normalize_area(raw: str) -> str:
    """Normalize an area string: split on '/' and strip each part; match
    the county case-insensitively and store it in its canonical spelling;
    store 'nyc'/'everywhere' as 'NYC'/'everywhere'; keep a town as typed.
    Raises ValueError, naming the allowed values, when raw doesn't parse."""
    parts = [part.strip() for part in raw.split("/")]
    if len(parts) > 2 or any(part == "" for part in parts):
        raise ValueError(AREA_ERROR)
    if len(parts) == 1:
        value = parts[0]
        if value.casefold() == "everywhere":
            return "everywhere"
        if value.casefold() == "nyc":
            return "NYC"
        county = match_county(value)
        if county is None:
            raise ValueError(AREA_ERROR)
        return county
    county_part, town_part = parts
    county = match_county(county_part)
    if county is None:
        raise ValueError(AREA_ERROR)
    return f"{county}/{town_part}"


def areas_for_lookup(area: str) -> list[str]:
    """Return the areas that apply to area, most specific first: town,
    county, NYC (when area is a borough or is NYC itself), everywhere."""
    if area == "everywhere":
        return ["everywhere"]
    if area == "NYC":
        return ["NYC", "everywhere"]
    if "/" in area:
        county, _town = area.split("/", 1)
        areas = [area, county]
    else:
        county = area
        areas = [county]
    if county in NYC_BOROUGHS:
        areas.append("NYC")
    areas.append("everywhere")
    return areas


def parse_rank(value: str) -> int | None:
    """Parse value as a whole number, 1 or more, or None if it isn't one."""
    if not value.isdigit():
        return None
    rank = int(value)
    return rank if rank >= 1 else None


def with_reader(fields: dict[str, str | None]) -> dict[str, str | None]:
    """fields from a file written before the reader column, with the default reader filled in."""
    if "reader" in fields or None in fields or None in fields.values():
        return fields
    return {**fields, "reader": default_reader(fields["url"] or "")}


def is_blank_row(fields: dict[str, str | None]) -> bool:
    """True when every field is missing or blank: an Excel trailing empty
    row (',,,,,,,,' or a fully blank line)."""
    return all(value is None or value.strip() == "" for value in fields.values())


def row_problem(fields: dict[str, str | None]) -> str:
    """Return a plain-language problem with fields, a CSV row already read
    against HEADER, or "" if the row is fine."""
    if None in fields or None in fields.values():
        return "wrong number of columns"
    if fields["job"] not in JOBS:
        return f"job must be one of {', '.join(JOBS)}"
    try:
        normalize_area(fields["area"])
    except ValueError as error:
        return f"area {error}"
    if parse_rank(fields["rank"]) is None:
        return "rank must be a whole number, 1 or more"
    if fields["status"] not in STATUSES:
        return f"status must be one of {', '.join(sorted(STATUSES))}"
    if fields["added_by"] not in ADDED_BY_VALUES:
        return f"added_by must be one of {', '.join(sorted(ADDED_BY_VALUES))}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fields["checked"]):
        return "checked must be YYYY-MM-DD"
    if not re.match(r"https?://", fields["url"]):
        return "url must start with http:// or https://"
    if fields["reader"] not in READERS:
        return f"reader must be one of {', '.join(READERS)}"
    if fields["reader"] == "cloud" and is_local_only_host(host_of(fields["url"])):
        return "reader cloud is not allowed here: this site is read in his Chrome"
    if not fields["site"].strip():
        return "site must not be blank"
    if not fields["gives"].strip():
        return "gives must not be blank"
    return ""


def header_problem(header: list[str], layer: Literal["default", "his"]) -> bool:
    """True when header is not allowed for layer. His file may still have the
    older header without the reader column; the shipped defaults may not."""
    if header == HEADER:
        return False
    return not (layer == "his" and header == OLD_HEADER)


def file_problems(path: Path, layer: Literal["default", "his"]) -> list[str]:
    """Every problem in path, as 'row <n>: <problem>' lines, without
    raising. A missing file has no problems."""
    if not path.is_file():
        return []
    with path.open(newline="", encoding=READ_ENCODING) as handle:
        header = next(csv.reader(handle), [])
    if header_problem(header, layer):
        return [f"row 1: header must be {','.join(HEADER)}"]
    problems = []
    with path.open(newline="", encoding=READ_ENCODING) as handle:
        for row_number, fields in enumerate(csv.DictReader(handle), start=2):
            if is_blank_row(fields):
                continue
            problem = row_problem(with_reader(fields))
            if problem:
                problems.append(f"row {row_number}: {problem}")
    return problems


def to_source_row(fields: dict[str, str], layer: Literal["default", "his"]) -> SourceRow:
    """Build a SourceRow from a CSV row already passed by row_problem (so
    fields["area"] is known to normalize). The area is stored normalized,
    not as typed, so a row written with different case or spacing still
    matches other rows for the same job, area and site."""
    return SourceRow(job=fields["job"], area=normalize_area(fields["area"]), site=fields["site"],
                      url=fields["url"], rank=int(fields["rank"]), gives=fields["gives"],
                      status=fields["status"], added_by=fields["added_by"], checked=fields["checked"],
                      reader=fields["reader"], layer=layer)


def row_key(row: SourceRow) -> tuple[str, str, str]:
    """The identity used to match a row across layers and within a lookup:
    job, area casefolded, and site casefolded."""
    return row.job, row.area.casefold(), row.site.casefold()


def read_rows(path: Path, layer: Literal["default", "his"]) -> list[SourceRow]:
    """Read path as a sources CSV file for layer. A missing file is an
    empty list. A bad header or row raises SourceFileError naming path and
    the row."""
    if not path.is_file():
        return []
    with path.open(newline="", encoding=READ_ENCODING) as handle:
        header = next(csv.reader(handle), [])
    if header_problem(header, layer):
        raise SourceFileError(path, 1, f"header must be {','.join(HEADER)}")
    rows = []
    with path.open(newline="", encoding=READ_ENCODING) as handle:
        for row_number, fields in enumerate(csv.DictReader(handle), start=2):
            if is_blank_row(fields):
                continue
            fields = with_reader(fields)
            problem = row_problem(fields)
            if problem:
                raise SourceFileError(path, row_number, problem)
            rows.append(to_source_row(fields, layer))
    return rows



def listed_hosts(workspace: Path) -> set[str]:
    """host_of(url) of every row in both layers. Raises SourceFileError for a broken file."""
    rows = read_rows(DEFAULTS_PATH, "default") + read_rows(his_path(workspace), "his")
    return {host_of(row.url) for row in rows}


def merge(defaults: list[SourceRow], his: list[SourceRow]) -> list[SourceRow]:
    """Combine the two layers: a his row replaces a default with the same
    job, area and site (row_key, so area and site are case-folded)."""
    his_keys = {row_key(row) for row in his}
    kept_defaults = [row for row in defaults if row_key(row) not in his_keys]
    return kept_defaults + his


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(2)


def his_path(workspace: Path) -> Path:
    return workspace / "shared" / "sources.csv"


def display_path(path: Path, workspace: Path) -> str:
    """path, relative to the workspace when it is inside it, else as-is
    (the shipped defaults file lives in the plugin, outside the workspace)."""
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def load_rows(workspace: Path) -> tuple[list[SourceRow], list[SourceRow]]:
    """Read both layers raw, or die with a fix-it message naming the
    broken file and row."""
    try:
        defaults = read_rows(DEFAULTS_PATH, "default")
        his = read_rows(his_path(workspace), "his")
    except SourceFileError as error:
        die(f"{display_path(error.path, workspace)} row {error.row_number}: {error.problem}. "
            "Fix the row or remove the file, then run sources.py check")
    return defaults, his


def write_his_rows(path: Path, rows: list[SourceRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({"job": row.job, "area": row.area, "site": row.site, "url": row.url,
                              "rank": row.rank, "gives": row.gives, "status": row.status,
                              "added_by": row.added_by, "checked": row.checked,
                              "reader": row.reader})


def select_rows(rows: list[SourceRow], job: str, area: str) -> tuple[list[SourceRow], list[SourceRow]]:
    """From merged rows, the (use, avoid) rows that apply to job and a
    normalized area, each sorted most specific area first, then rank."""
    areas = areas_for_lookup(area)
    area_folds = [candidate.casefold() for candidate in areas]

    def area_specificity(row: SourceRow) -> int:
        return area_folds.index(row.area.casefold())

    def sort_key(row: SourceRow) -> tuple[int, int]:
        return area_specificity(row), row.rank

    matching = [row for row in rows if row.job == job and row.area.casefold() in area_folds]
    rows_by_site: dict[str, list[SourceRow]] = {}
    for row in matching:
        rows_by_site.setdefault(row.site.casefold(), []).append(row)

    # One row per site: an avoid row at any applicable area keeps that site
    # out of the use list entirely; otherwise keep the most specific row.
    use_rows = []
    avoid_rows = []
    for site_rows in rows_by_site.values():
        avoid_candidates = [row for row in site_rows if row.status == "avoid"]
        if avoid_candidates:
            avoid_rows.append(min(avoid_candidates, key=area_specificity))
        else:
            use_rows.append(min(site_rows, key=area_specificity))
    use_rows.sort(key=sort_key)
    avoid_rows.sort(key=sort_key)
    return use_rows, avoid_rows


def lookup_rows(workspace: Path, job: str, area: str) -> list[SourceRow]:
    """The use rows `lookup` prints for job and area, in the same order.
    Raises ValueError for a bad job or area and SourceFileError for a
    broken file."""
    if job not in JOBS:
        raise ValueError(f"--job must be one of {', '.join(JOBS)}")
    try:
        normalized_area = normalize_area(area)
    except ValueError as error:
        raise ValueError(f"--area {error}") from error
    rows = merge(read_rows(DEFAULTS_PATH, "default"), read_rows(his_path(workspace), "his"))
    use_rows, _avoid_rows = select_rows(rows, job, normalized_area)
    return use_rows


def cmd_lookup(args: argparse.Namespace) -> None:
    if args.job not in JOBS:
        die(f"--job must be one of {', '.join(JOBS)}")
    try:
        area = normalize_area(args.area)
    except ValueError as error:
        die(f"--area {error}")
    defaults, his = load_rows(args.workspace)
    use_rows, avoid_rows = select_rows(merge(defaults, his), args.job, area)

    if not use_rows and not avoid_rows:
        print(f"No sources listed yet for {args.job} in {area}. Search the web for a source, "
              "then ask the appraiser before running sources.py add.")
        return
    for row in use_rows:
        print(f"{row.rank}. {row.site} {row.url} - {row.gives} ({row.layer}) ({row.reader})")
    if avoid_rows:
        print("Avoid:")
        for row in avoid_rows:
            print(f"- {row.site} {row.url} - {row.gives} ({row.layer}) ({row.reader})")


def cmd_add(args: argparse.Namespace) -> None:
    if args.job not in JOBS:
        die(f"--job must be one of {', '.join(JOBS)}")
    try:
        area = normalize_area(args.area)
    except ValueError as error:
        die(f"--area {error}")
    if args.rank < 1:
        die("--rank must be a whole number, 1 or more")
    if not re.match(r"https?://", args.url):
        die("--url must start with http:// or https://")
    if not args.site.strip():
        die("--site must not be blank")
    if not args.gives.strip():
        die("--gives must not be blank")
    reader = args.reader
    if reader is None:
        reader = default_reader(args.url)
    if reader == "cloud" and is_local_only_host(host_of(args.url)):
        die(f"{args.site} is read in his Chrome → use --reader chrome")
    try:
        existing = read_rows(his_path(args.workspace), "his")
    except SourceFileError as error:
        die(f"{display_path(error.path, args.workspace)} row {error.row_number}: {error.problem}. "
            "Fix the row or remove the file, then run sources.py check")
    new_row = SourceRow(job=args.job, area=area, site=args.site, url=args.url, rank=args.rank,
                         gives=args.gives, status=args.status, added_by="appraiser",
                         checked=date.today().isoformat(), reader=reader, layer="his")
    new_key = row_key(new_row)
    kept = [row for row in existing if row_key(row) != new_key]
    write_his_rows(his_path(args.workspace), [*kept, new_row])
    print(f"{new_row.job} {new_row.area}: {new_row.rank}. {new_row.site} {new_row.url} - {new_row.gives} "
          f"({new_row.status})")


def cmd_list(args: argparse.Namespace) -> None:
    defaults, his = load_rows(args.workspace)
    rows = defaults + his
    if args.job:
        rows = [row for row in rows if row.job == args.job]
    if args.area:
        try:
            area = normalize_area(args.area)
        except ValueError as error:
            die(f"--area {error}")
        rows = [row for row in rows if row.area.casefold() == area.casefold()]
    rows.sort(key=lambda row: (row.job, row.area, row.rank))
    for row in rows:
        print(f"{row.job} {row.area}: {row.rank}. {row.site} {row.url} - {row.gives} "
              f"({row.status}, {row.layer}) ({row.reader})")


def cmd_check(args: argparse.Namespace) -> None:
    problems = []
    for path, layer in ((DEFAULTS_PATH, "default"), (his_path(args.workspace), "his")):
        problems += [f"{display_path(path, args.workspace)} {problem}" for problem in file_problems(path, layer)]
    if problems:
        for problem in problems:
            print(problem)
        sys.exit(1)
    defaults, his = load_rows(args.workspace)
    print(f"sources OK ({len(defaults)} default, {len(his)} his)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    lookup = subparsers.add_parser("lookup")
    lookup.add_argument("--job", required=True)
    lookup.add_argument("--area", required=True)
    lookup.set_defaults(f=cmd_lookup)

    add = subparsers.add_parser("add")
    add.add_argument("--job", required=True)
    add.add_argument("--area", required=True)
    add.add_argument("--site", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--rank", required=True, type=int)
    add.add_argument("--gives", required=True)
    add.add_argument("--status", choices=["use", "avoid"], default="use")
    add.add_argument("--reader", choices=list(READERS), default=None)
    add.set_defaults(f=cmd_add)

    list_sp = subparsers.add_parser("list")
    list_sp.add_argument("--job")
    list_sp.add_argument("--area")
    list_sp.set_defaults(f=cmd_list)

    check = subparsers.add_parser("check")
    check.set_defaults(f=cmd_check)

    args = parser.parse_args()
    args.workspace = require_workspace(Path.cwd())
    args.f(args)


if __name__ == "__main__":
    main()
