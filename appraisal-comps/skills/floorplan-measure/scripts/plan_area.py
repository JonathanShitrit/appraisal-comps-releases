#!/usr/bin/env python3
"""
plan_area.py - deterministic area math for a floor plan reading.

The model READS the plan; this script does the ARITHMETIC and the checks, so
totals are never mental math. Inputs are CSVs the model writes after reading.

Rooms CSV (optional):
  room,dims,subtract,flag,overlaps,px_w,px_d   (px_w,px_d optional)
  Primary bedroom,10-6 x 16-3,,,
  Living room,11-11 x 24-2,,,
  Living/dining L,15-0 x 20-0,4-0 x 6-0,,
  Walk-in closet,6-3 x 7-8,,confirm 7-8 vs 7-3,
  Dining area,7-6 x 9-11,,,Living room

  dims      Feet-inches "A x B". Accepts 10-6 (safest in CSV), 10'6, 10' 6, 10.5
  subtract  Optional notch(es) to remove for L-shapes/alcoves, ';'-separated
  flag      Any text = a hard-to-read digit. One flag caps confidence at MEDIUM, two or more at LOW
  overlaps  Name of a room this one may share floor area with (living/dining)
  px_w,px_d Optional. Drawn length in pixels of the width / depth wall, read from the
            image. With 3 or more walls the script runs the scale check: feet per pixel
            may not vary by more than 5%. The pixel reading is the model's; the
            comparison is the script's.

Pixel outline CSV (--outline): the corners of the inside face of the perimeter
walls, in order around the outline, in image pixels.
  x,y
  0,0
  280,0
  280,200
  0,200
  The outline is measured only when the rooms CSV's scale check ran and passed;
  its median feet per pixel converts the pixel area to square feet.

House sketch CSV (--sketch): the exterior sides of each floor, in order around it.
  floor,area_type,side,length,direction,flag
  1st,finished,1,30-0,R,
  1st,finished,2,20-0,D,
  1st,finished,3,30-0,L,
  1st,finished,4,20-0,U,confirm 20-0 vs 26-0

  area_type  finished | below_grade | unfinished | garage | porch, the same on every
             row of one floor. Only finished floors count; the others are listed as excluded.
  length     Feet-inches, as for room dims. Zero or negative is rejected.
  direction  R (+x), L (-x), U (+y), D (-y). An angled side (bay, clipped corner) can't
             be walked: the floor is not measured until the appraiser gives the missing sides.
  flag       Any text = a hard-to-read dimension. Caps confidence like a flagged room digit.
  A floor is measured only if its walk closes within 0.5 ft of its start.

Attic CSV (--attic, with --sketch): one row per attic or sloped-ceiling area.
  floor,dims,pct_7ft_plus,dims_5ft_plus
  Attic,20-0 x 30-0,60,12-0 x 30-0

  pct_7ft_plus   Percent of the area with a ceiling of at least 7 ft (from the appraiser).
  dims_5ft_plus  The part with a ceiling of at least 5 ft. Counted only when
                 pct_7ft_plus is more than 50; otherwise the attic is excluded.

Usage
  plan_area.py [rooms.csv] [--listed 950] [--outline corners.csv | --sketch sides.csv]
               [--attic attic.csv] [--convention labeled|outline] [--out result.md]

Output: room-by-room markdown table, then (a) labeled total, (d) outline total
and the unlabeled space (outline minus labeled), (c) total under the stated
convention, plus the listed-vs-measured difference and every check that failed.
An outline whose check failed is "not measured", never estimated; under the
outline convention (c) then falls back to the labeled total, capped at LOW.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys

DIM = re.compile(r"""^\s*(\d+(?:\.\d+)?)\s*(?:'|ft|-)?\s*(?:(\d+(?:\.\d+)?)\s*(?:"|''|in)?)?\s*$""")
SCALE_TOLERANCE = 0.05            # feet per pixel may vary by at most 5%
UNLABELED_SHARE_LIMIT = 0.20      # unlabeled space over 20% of the outline is flagged
MIN_CORNERS = 4
CLOSURE_TOLERANCE_FT = 0.5        # a sketch walk must end within 0.5 ft of its start
ATTIC_MIN_PCT_7FT = 50.0          # an attic counts only if MORE than 50% is at least 7 ft high
AREA_TYPES = ("finished", "below_grade", "unfinished", "garage", "porch")
DIRECTION_STEPS = {"R": (1.0, 0.0), "L": (-1.0, 0.0), "U": (0.0, 1.0), "D": (0.0, -1.0)}

Point = tuple[float, float]


def to_ft(token: str) -> float:
    t = token.strip().replace("’", "'").replace("”", '"').replace("′", "'").replace("″", '"')
    m = DIM.match(t)
    if not m:
        raise ValueError(f"cannot parse length '{token}'. Use feet-inches like 10'6\" or decimal feet like 10.5")
    ft, inch = float(m.group(1)), float(m.group(2) or 0)
    if inch >= 12:
        raise ValueError(f"'{token}' has {inch:g} inches (>= 12). Re-read the plan; this digit is probably misread")
    return ft + inch / 12.0


def rect(dims: str) -> tuple[float, float, float]:
    parts = re.split(r"\s*[xX×]\s*", dims.strip())
    if len(parts) != 2:
        raise ValueError(f"dims '{dims}' must be 'A x B'")
    a, b = to_ft(parts[0]), to_ft(parts[1])
    return a, b, a * b


def px(v: str | None, line: int, name: str, problems: list[str]) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        f = float(v)
        if f <= 0:
            raise ValueError
        return f
    except ValueError:
        problems.append(f"line {line} {name}: pixel length '{v}' must be a positive number")
        return None


def read_rooms(path: str, problems: list[str]) -> list[dict]:
    rows, names = [], set()
    with open(path, newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            name = (r.get("room") or "").strip()
            try:
                w, d, area = rect(r["dims"])
                for notch in filter(None, (r.get("subtract") or "").split(";")):
                    nw, nd, na = rect(notch)
                    if nw > w + 0.01 and nw > d + 0.01:
                        problems.append(f"line {i} {name}: notch {notch} is wider than the room")
                    area -= na
            except (ValueError, KeyError) as e:
                problems.append(f"line {i} {name}: {e}")
                continue
            if name.lower() in names:
                problems.append(f"line {i}: room '{name}' listed twice. Possible double count")
            names.add(name.lower())
            if w < 2 or d < 2 or w > 60 or d > 60:
                problems.append(f"line {i} {name}: side {w:.2f} x {d:.2f} ft is implausible. Re-read")
            rows.append({"room": name, "dims": r["dims"].strip(), "subtract": (r.get("subtract") or "").strip(),
                         "flag": (r.get("flag") or "").strip(), "overlaps": (r.get("overlaps") or "").strip(),
                         "area": area, "w": w, "d": d,
                         "px_w": px(r.get("px_w"), i, name, problems), "px_d": px(r.get("px_d"), i, name, problems)})
    return rows


def scale_check(rows: list[dict], problems: list[str]) -> tuple[str, float | None]:
    """Feet per pixel must agree within 5% across every wall with a pixel length.

    Returns the report line and the median feet per pixel, which is None unless
    the check ran (3 or more walls) and passed.
    """
    ratios = [(r["room"], ft / pixels) for r in rows
              for ft, pixels in ((r["w"], r["px_w"]), (r["d"], r["px_d"])) if pixels]
    if len(ratios) < 3:
        return "Scale check: not run (give px_w/px_d for at least 3 walls)", None
    vals = sorted(v for _, v in ratios)
    median_feet_per_pixel = vals[len(vals) // 2]
    off = [(n, (v - median_feet_per_pixel) / median_feet_per_pixel * 100) for n, v in ratios
           if abs(v - median_feet_per_pixel) / median_feet_per_pixel > SCALE_TOLERANCE]
    for n, pct in off:
        problems.append(f"scale check: {n} is {pct:+.1f}% off the median feet/pixel. "
                        "Plan may not be to scale, or a dimension is misread")
    if off:
        return f"Scale check: {len(ratios)} walls, FAIL", None
    return f"Scale check: {len(ratios)} walls, PASS (within 5%)", median_feet_per_pixel


def read_corners(path: str, problems: list[str]) -> list[Point]:
    """Read x,y corners in order. A corner that isn't two numbers is a problem naming its line."""
    corners = []
    with open(path, newline="") as f:
        for line_number, row in enumerate(csv.DictReader(f), start=2):
            x_text = (row.get("x") or "").strip()
            y_text = (row.get("y") or "").strip()
            try:
                corners.append((float(x_text), float(y_text)))
            except ValueError:
                problems.append(f"outline line {line_number}: corner '{x_text},{y_text}' is not two numbers. "
                                "Re-read the corner from the plan")
    return corners


def polygon_area(points: list[Point]) -> float:
    """Shoelace formula, absolute value."""
    twice_area = 0.0
    for index, (x1, y1) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        twice_area += x1 * y2 - x2 * y1
    return abs(twice_area) / 2.0


def orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(a: Point, b: Point, point: Point) -> bool:
    return (min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= point[1] <= max(a[1], b[1]))


def segments_touch(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1 = orientation(q1, q2, p1)
    d2 = orientation(q1, q2, p2)
    d3 = orientation(p1, p2, q1)
    d4 = orientation(p1, p2, q2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    if d1 == 0 and on_segment(q1, q2, p1):
        return True
    if d2 == 0 and on_segment(q1, q2, p2):
        return True
    if d3 == 0 and on_segment(p1, p2, q1):
        return True
    if d4 == 0 and on_segment(p1, p2, q2):
        return True
    return False


def crosses_itself(points: list[Point]) -> bool:
    """True when any two non-adjacent edges of the closed outline touch or intersect."""
    count = len(points)
    edges = [(points[index], points[(index + 1) % count]) for index in range(count)]
    for first in range(count):
        for second in range(first + 1, count):
            adjacent = second == first + 1 or (first == 0 and second == count - 1)
            if adjacent:
                continue
            if segments_touch(*edges[first], *edges[second]):
                return True
    return False


def outline_problem(points: list[Point]) -> str | None:
    """The reason a closed outline can't be measured, or None when its shape checks pass."""
    if len(points) < MIN_CORNERS:
        return f"needs at least 4 corners, got {len(points)}"
    if crosses_itself(points):
        return "the outline crosses itself; re-read the corner order"
    return None


def measure_pixel_outline(path: str, median_feet_per_pixel: float | None,
                          problems: list[str]) -> tuple[float | None, str]:
    """Returns (area in sq ft, description) or (None, reason not measured)."""
    problems_before = len(problems)
    corners = read_corners(path, problems)
    if len(problems) > problems_before:
        return None, "a corner is not a number"
    # a trace that repeats its first corner at the end is already closed; drop the repeat
    if len(corners) > 1 and corners[-1] == corners[0]:
        corners = corners[:-1]
    shape_problem = outline_problem(corners)
    if shape_problem:
        return None, shape_problem
    if median_feet_per_pixel is None:
        return None, "the scale check did not run or did not pass"
    area = polygon_area(corners) * median_feet_per_pixel * median_feet_per_pixel
    return area, f"pixel outline, {len(corners)} corners"


def walk_sides(sides: list[tuple[float, str]]) -> tuple[list[Point], float]:
    """Walk (length, direction) sides from (0, 0).

    Returns the corners (the start and the end of every side but the last, so
    the last point snaps to the first) and the closure gap in feet.
    """
    x, y = 0.0, 0.0
    corners = [(x, y)]
    for length, direction in sides:
        if direction not in DIRECTION_STEPS:
            raise ValueError(f"direction '{direction}' must be one of R, L, U, D")
        step_x, step_y = DIRECTION_STEPS[direction]
        x += step_x * length
        y += step_y * length
        corners.append((x, y))
    gap = (x * x + y * y) ** 0.5
    return corners[:-1], gap


def read_sketch(path: str) -> dict[str, list[dict]]:
    """Group sketch rows by floor, keeping the order floors first appear in."""
    floors: dict[str, list[dict]] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            floor_name = (row.get("floor") or "").strip()
            floors.setdefault(floor_name, []).append(row)
    return floors


def measure_floor(floor_rows: list[dict]) -> tuple[str, float | None, str, int]:
    """Returns (area_type, area or None, reason not measured, flagged side count) for one floor."""
    area_types = {(row.get("area_type") or "").strip() for row in floor_rows}
    area_type = area_types.pop() if len(area_types) == 1 else ""
    flagged_sides = sum(1 for row in floor_rows if (row.get("flag") or "").strip())
    if not area_type:
        return "", None, "area_type differs between rows of this floor", flagged_sides
    if area_type not in AREA_TYPES:
        return area_type, None, f"area_type '{area_type}' must be one of {', '.join(AREA_TYPES)}", flagged_sides

    side_problems = []
    sides = []
    for row in floor_rows:
        side_label = (row.get("side") or "").strip()
        length_text = (row.get("length") or "").strip()
        direction = (row.get("direction") or "").strip().upper()
        try:
            length = to_ft(length_text)
        except ValueError as error:
            side_problems.append(f"side {side_label}: {error}")
            continue
        if length <= 0:
            side_problems.append(f"side {side_label}: length '{length_text}' is zero or negative. Re-read the sketch")
            continue
        if direction not in DIRECTION_STEPS:
            side_problems.append(f"side {side_label}: direction '{direction}' is angled or unknown. Only R, L, U, D "
                                 "can be walked; ask the appraiser for the missing dimensions")
            continue
        sides.append((length, direction))
    if side_problems:
        return area_type, None, "; ".join(side_problems), flagged_sides
    if len(sides) < MIN_CORNERS:
        return area_type, None, f"needs at least 4 sides, got {len(sides)}", flagged_sides

    corners, gap = walk_sides(sides)
    if gap > CLOSURE_TOLERANCE_FT:
        return area_type, None, f"does not close: {gap:.1f} ft gap", flagged_sides
    if crosses_itself(corners):
        return area_type, None, "the outline crosses itself; re-read the side order", flagged_sides
    return area_type, polygon_area(corners), "", flagged_sides


def attic_problem(row: dict) -> str | None:
    """The reason an attic row can't be used, or None when its numbers make sense."""
    pct_text = (row.get("pct_7ft_plus") or "").strip()
    try:
        pct_7ft = float(pct_text)
    except ValueError:
        return f"pct_7ft_plus '{pct_text}' is not a number; the appraiser gives the heights"
    if pct_7ft < 0 or pct_7ft > 100:
        return f"pct_7ft_plus '{pct_text}' must be a percent from 0 to 100"
    if pct_7ft <= ATTIC_MIN_PCT_7FT:
        return None
    try:
        _, _, attic_area = rect(row.get("dims") or "")
        _, _, area_5ft = rect(row.get("dims_5ft_plus") or "")
    except ValueError as error:
        return f"dims or dims_5ft_plus: {error}"
    if area_5ft > attic_area:
        return (f"the 5 ft area {round(area_5ft)} sq ft is larger than the attic {round(attic_area)} sq ft. "
                "Re-read the dimensions")
    return None


def measure_attics(path: str, out: list[str], problems: list[str]) -> tuple[float, bool]:
    """Apply the attic rule. Returns (counted area, every row measured)."""
    counted_total = 0.0
    all_measured = True
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("floor") or "").strip()
            reason = attic_problem(row)
            if reason:
                out.append(f"Attic {name}: not measured ({reason})")
                problems.append(f"attic {name} not measured: {reason}")
                all_measured = False
                continue
            pct_7ft = float((row.get("pct_7ft_plus") or "").strip())
            if pct_7ft <= ATTIC_MIN_PCT_7FT:
                out.append(f"Attic {name}: excluded ({pct_7ft:g}% at 7 ft or more, needs more than 50%)")
                continue
            _, _, area_5ft = rect(row.get("dims_5ft_plus") or "")
            out.append(f"Attic {name}: {round(area_5ft)} sq ft counted ({pct_7ft:g}% at 7 ft or more; "
                       "area at 5 ft or more)")
            counted_total += area_5ft
    return counted_total, all_measured


def measure_sketch(sketch_path: str, attic_path: str | None, out: list[str],
                   problems: list[str]) -> tuple[float | None, str, int]:
    """Returns (finished area + counted attic, or None; description or reason; flagged side count)."""
    finished_total = 0.0
    all_measured = True
    flagged_sides = 0
    for floor_name, floor_rows in read_sketch(sketch_path).items():
        area_type, area, reason, floor_flags = measure_floor(floor_rows)
        flagged_sides += floor_flags
        if area is None:
            out.append(f"Floor {floor_name}: not measured ({reason})")
            problems.append(f"floor {floor_name} not measured: {reason}")
            if area_type == "finished" or area_type not in AREA_TYPES:
                all_measured = False
        elif area_type == "finished":
            out.append(f"Floor {floor_name} ({area_type}): {round(area)} sq ft")
            finished_total += area
        else:
            out.append(f"Excluded: {floor_name} ({area_type}) {round(area)} sq ft")
    attic_total = 0.0
    if attic_path:
        attic_total, attics_measured = measure_attics(attic_path, out, problems)
        if not attics_measured:
            all_measured = False
    if not all_measured:
        return None, "a finished floor or attic is not measured", flagged_sides
    if finished_total == 0:
        return None, "no finished floor in the sketch", flagged_sides
    return finished_total + attic_total, "sketch: finished floors + attic", flagged_sides


def room_table(rows: list[dict], labeled: float) -> list[str]:
    out = ["| Room | Labeled size | Area (sq ft) |", "| --- | --- | --- |"]
    for r in rows:
        size = r["dims"] + (f" minus {r['subtract']}" if r["subtract"] else "")
        if r["flag"]:
            size += f" (digit flagged: {r['flag']})"
        out.append(f"| {r['room']} | {size} | {r['area']:.1f} |")
    out.append(f"| **Labeled rooms total** |  | **{labeled:.1f}** |")
    out.append("")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rooms_csv", nargs="?", help="rooms CSV (optional when an outline is given)")
    ap.add_argument("--listed", type=float, help="listed/marketed sq ft, for the difference column")
    outline_inputs = ap.add_mutually_exclusive_group()
    outline_inputs.add_argument("--outline", help="pixel corners CSV (x,y) of the inside face of the perimeter walls")
    outline_inputs.add_argument("--sketch", help="house sketch CSV (floor,area_type,side,length,direction,flag)")
    ap.add_argument("--attic", help="attic CSV (floor,dims,pct_7ft_plus,dims_5ft_plus); needs --sketch")
    ap.add_argument("--convention", choices=["labeled", "outline"], default="labeled",
                    help="appraiser's convention from settings/conventions.md")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.attic and not a.sketch:
        ap.error("--attic needs --sketch: the attic rule applies to a house sketch")

    problems: list[str] = []
    rows = read_rooms(a.rooms_csv, problems) if a.rooms_csv else []
    rooms_given = a.rooms_csv is not None
    if rooms_given:
        scale_line, median_feet_per_pixel = scale_check(rows, problems)
    else:
        scale_line, median_feet_per_pixel = "Scale check: not run (no rooms CSV)", None

    for r in rows:
        if r["overlaps"]:
            problems.append(f"{r['room']} may overlap {r['overlaps']}. Confirm on the plan that the dimension "
                            f"strings do not cover the same floor before keeping both")
    if rooms_given and not rows:
        problems.append("no readable rooms in the CSV. Check the header is room,dims,subtract,flag,overlaps[,px_w,px_d]")

    labeled = sum(r["area"] for r in rows)
    flagged_rooms = sum(1 for r in rows if r["flag"])
    rooms_measured = len(rows) > 0

    out = room_table(rows, labeled) if rooms_given else []
    if rooms_given:
        out.append(f"(a) Labeled rooms total: {labeled:.1f} sq ft ({round(labeled)} rounded)")
    else:
        out.append("(a) Labeled rooms total: not given (no rooms CSV)")

    # (d) the outline
    outline_requested = a.outline is not None or a.sketch is not None or a.convention == "outline"
    outline_area: float | None = None
    flagged_sides = 0
    if a.outline:
        outline_area, outline_note = measure_pixel_outline(a.outline, median_feet_per_pixel, problems)
    elif a.sketch:
        outline_area, outline_note, flagged_sides = measure_sketch(a.sketch, a.attic, out, problems)
    else:
        outline_note = "no --outline or --sketch given"
    if outline_area is not None and rooms_measured and outline_area < labeled:
        problems.append(f"outline {round(outline_area)} sq ft is smaller than the labeled room total "
                        f"{round(labeled)} sq ft. Re-read the outline corners or the room dimensions")
        outline_area = None
        outline_note = "smaller than the labeled room total"
    if outline_area is not None:
        out.append(f"(d) Outline total: {round(outline_area)} sq ft ({outline_note})")
    elif outline_requested:
        out.append(f"(d) Outline: not measured ({outline_note})")
        problems.append(f"outline not measured: {outline_note}")
    else:
        out.append("(d) Outline: not given")

    if outline_area is not None and rooms_measured:
        unlabeled = outline_area - labeled
        share = unlabeled / outline_area
        out.append(f"Unlabeled space: {round(unlabeled)} sq ft ({share * 100:.1f}% of outline)")
        if share > UNLABELED_SHARE_LIMIT:
            problems.append(f"unlabeled space is more than 20% of the outline ({share * 100:.1f}%). "
                            "Check for a missed room or a misread outline corner")

    # (c) the figure under the appraiser's convention
    conv = labeled
    if a.convention == "labeled":
        out.append(f"(c) Total under convention 'labeled rooms only': {round(conv)} sq ft")
    elif outline_area is not None:
        conv = outline_area
        out.append(f"(c) Total under convention 'outline': {round(conv)} sq ft")
    elif rooms_measured:
        out.append(f"(c) Total under convention 'outline': {round(conv)} sq ft (labeled only, outline not measured)")
    else:
        out.append("(c) Total under convention 'outline': not measured (no outline and no labeled rooms)")

    if a.listed and conv > 0:
        diff = a.listed - conv
        out.append(f"Listed {a.listed:g} vs measured {round(conv)}: listed is {diff:+.0f} sq ft ({diff / conv * 100:+.1f}%)")
        if abs(diff / conv) > 0.10:
            problems.append(f"listed size differs from measured by more than 10% ({diff / conv * 100:+.1f}%). Flag to appraiser")

    # confidence: the model still states its reason; the script only sets a ceiling
    flagged = flagged_rooms + flagged_sides
    if problems or flagged > 1:
        ceiling = "LOW"
    elif flagged:
        ceiling = "MEDIUM"
    else:
        ceiling = "HIGH"
    out.append(scale_line)
    out.append(f"Confidence ceiling from checks: {ceiling}"
               + (f" ({flagged} flagged digit(s))" if flagged else ""))
    if problems:
        out.append("")
        out.append("Checks that need attention:")
        out += [f"- {p}" for p in problems]

    text = "\n".join(out)
    print(text)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    sys.exit(1 if any("cannot parse" in p or ">= 12" in p for p in problems) else 0)


if __name__ == "__main__":
    main()
