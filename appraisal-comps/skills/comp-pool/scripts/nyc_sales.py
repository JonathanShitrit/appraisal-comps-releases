#!/usr/bin/env python3
"""
nyc_sales.py - pull NYC recorded sales from official open data in one call.

Code-as-the-action: the agent runs this script, the raw rows go to CSV files
in the assignment folder, and only a short summary is printed back into the
model's context.

Sources (all NYC Open Data / Socrata, no login):
  PLUTO ............................ 64uk-42ks  (lot facts, lat/lon)
  DOF Rolling Calendar Sales ....... usep-8jbt  (last ~12 months; condos + co-ops)
  DOF Annualized Calendar Sales .... w2pb-icbu  (2016 -> last full year; has lat/lon)
  ACRIS Real Property Legals ....... 8h5j-fqxa
  ACRIS Real Property Master ....... bnx9-e6tj

  DOF sales field names (checked 2026-11-17 on one row of each dataset; both use the same names):
    building_class_category     e.g. "01 ONE FAMILY DWELLINGS"
    building_class_at_time_of   building class at the time of sale, e.g. "C3"
    land_square_feet            lot area; may carry thousands commas ("2,021"); 0 = not recorded
    gross_square_feet           DOF gross building area, NOT living area; 0 = not recorded
    residential_units           e.g. "8"
    year_built
  The rolling set's present-day class is building_class_at_present; the annualized set's is
  building_class_as_of_final. Neither is used: the filter reads the class at the time of sale.

Commands
  subject   --bbl 1004410012
  sales     --bbl 1004410012 --since 2025-01-01 --type apartment|house [--radius-mi 0.25] --out DIR
            apartment: co-op and condo unit categories. house: one-, two- and three-family
            categories (01, 02, 03) plus four-family walk-ups (category 07, class C3).
  acris     --bbl 1004411203 [--since 2020-01-01] [--expect-price 1692000 --expect-date 2025-03-14]
            with --expect-* it prints DEED CHECK: MATCH | DATE_DIFF | MISMATCH | ZERO_DEED | PARTIAL

BBL = 10 digits: borough (1) + block (5) + lot (4). Example 1004410012.
Condo unit lots are 1001-6999 and are not in PLUTO; co-op buildings are one lot.
Co-op share sales appear in DOF sales (categories 09/10) but NOT as ACRIS deeds.
"""
import argparse, csv, json, math, os, re, sys, urllib.parse, urllib.request
from datetime import date

BASE = "https://data.cityofnewyork.us/resource/"
DS = {"pluto": "64uk-42ks", "rolling": "usep-8jbt", "annual": "w2pb-icbu",
      "legals": "8h5j-fqxa", "master": "bnx9-e6tj"}
BORO = {"1": "MN", "2": "BX", "3": "BK", "4": "QN", "5": "SI"}
# Residential unit categories only (walk-up / elevator co-ops and condos, small condos, condo-co-ops)
RES_CATS = ("09 ", "10 ", "12 ", "13 ", "15 ", "17 ")
# One-, two- and three-family dwellings; four-family houses are filed under 07 with class C3
HOUSE_CATS = ("01 ", "02 ", "03 ")
FOUR_FAMILY_CATEGORY = "07 "
FOUR_FAMILY_CLASS = "C3"
SALE_CLASS_FIELD = "building_class_at_time_of"
MAX_PRINT = 40          # hard cap on rows printed back to the model
TODAY = date.today().isoformat()


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def q(ds, params):
    url = BASE + DS[ds] + ".json?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    except Exception as e:  # make the error actionable for the model
        die(f"{ds} query failed ({e}). URL: {url}")


def parse_bbl(s):
    s = re.sub(r"\D", "", str(s))
    if len(s) != 10 or s[0] not in BORO:
        die(f"BBL '{s}' must be 10 digits: borough(1-5) + block(5) + lot(4), e.g. 1004410012. "
            "Look it up on ACRIS or the DOF property tax site if unknown.")
    return s[0], int(s[1:6]), int(s[6:])


def norm_street(a):
    """'425 EAST 13TH STREET, C' -> '425 E 13 ST' so DOF, ACRIS and PLUTO addresses match."""
    a = (a or "").upper().split(",")[0]
    a = re.sub(r"(\d+)(ST|ND|RD|TH)\b", r"\1", a)
    for k, v in {"EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S", "STREET": "ST",
                 "AVENUE": "AVE", "PLACE": "PL", "BOULEVARD": "BLVD", "ROAD": "RD", "DRIVE": "DR"}.items():
        a = re.sub(rf"\b{k}\b", v, a)
    return re.sub(r"\s+", " ", a).strip()


def miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    return 2 * r * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


def keep_sale(row: dict, property_kind: str) -> bool:
    """True when a DOF sale row is the kind of property being searched for."""
    category = row.get("building_class_category") or ""
    if property_kind == "apartment":
        return category.startswith(RES_CATS)
    if property_kind == "house":
        if category.startswith(HOUSE_CATS):
            return True
        sale_class = (row.get(SALE_CLASS_FIELD) or "").strip().upper()
        return category.startswith(FOUR_FAMILY_CATEGORY) and sale_class == FOUR_FAMILY_CLASS
    raise ValueError(f"property_kind must be 'apartment' or 'house', not '{property_kind}'")


def dof_number(value: object) -> str:
    """A DOF count or area as plain digits ('2,021' -> '2021'). Blank when missing or 0 (DOF's 'not recorded')."""
    text = str(value or "").replace(",", "").strip()
    try:
        number = float(text)
    except ValueError:
        return ""
    if number <= 0:
        return ""
    return str(int(number)) if number == int(number) else text


def dof_fields(row: dict) -> dict:
    """The DOF building facts carried into the sales CSV, with DOF's zeros blanked."""
    return {
        "building_class": row.get(SALE_CLASS_FIELD, ""),
        "land_sqft": dof_number(row.get("land_square_feet")),
        "gross_sqft": dof_number(row.get("gross_square_feet")),
        "residential_units": dof_number(row.get("residential_units")),
        "year_built": dof_number(row.get("year_built")),
    }


def pluto_lot(boro, block, lot):
    rows = q("pluto", {"$where": f"bbl={boro}{block:05d}{lot:04d}"})
    return rows[0] if rows else None


def subject_facts(bbl):
    boro, block, lot = parse_bbl(bbl)
    rec = pluto_lot(boro, block, lot)
    note = "PLUTO match on BBL"
    addrs = set()
    leg = q("legals", {"$select": "street_number,street_name", "$group": "street_number,street_name",
                       "borough": boro, "block": block, "lot": lot})
    addrs |= {norm_street(f"{l.get('street_number','')} {l.get('street_name','')}") for l in leg}
    if rec is None and lot >= 1001:
        # condo unit lot: match its ACRIS address(es) to the condo billing lot in PLUTO
        if not addrs:
            die(f"No PLUTO or ACRIS record for {bbl}. Confirm the BBL with the appraiser.")
        condos = [c for c in q("pluto", {"borough": BORO[boro], "block": block}) if c.get("condono")]
        match = [c for c in condos if norm_street(c.get("address")) in addrs]
        if not match and len(condos) == 1:
            match = condos
        if not match:
            opts = "; ".join(f"{c['bbl'].split('.')[0]} {c.get('address')}" for c in condos) or "none"
            die(f"Unit lot {bbl} has ACRIS address(es) {sorted(addrs)} but no matching PLUTO billing lot. "
                f"Condo billing lots on block {block}: {opts}. Ask the appraiser which building it is.")
        rec, note = match[0], f"condo unit lot; facts from billing lot {match[0]['bbl'].split('.')[0]}"
    if rec is None:
        die(f"No PLUTO record for {bbl}. Confirm the BBL with the appraiser.")
    keep = ["bbl", "address", "zipcode", "bldgclass", "condono", "yearbuilt", "yearalter1",
            "numfloors", "unitsres", "bldgarea", "resarea", "lotarea", "lotfront", "lotdepth",
            "bldgfront", "bldgdepth", "latitude", "longitude", "histdist", "version"]
    out = {k: rec.get(k) for k in keep}
    out["bbl"] = str(out["bbl"]).split(".")[0]
    addrs.add(norm_street(rec.get("address")))
    out["building_addresses"] = sorted(a for a in addrs if a)
    out["_note"], out["_source"], out["_retrieved"] = note, "PLUTO 64uk-42ks + ACRIS legals", TODAY
    return out


def sales_on_blocks(boro, blocks, since):
    rows = []
    blocks = sorted(set(blocks))
    for i in range(0, len(blocks), 40):
        chunk = ",".join(f"'{b}'" for b in blocks[i:i + 40])
        where = f"borough='{boro}' AND block in({chunk}) AND sale_date>='{since}' AND sale_price>'10000'"
        for ds in ("rolling", "annual"):
            for r in q(ds, {"$where": where, "$limit": 50000}):
                r["_source"] = f"DOF {'Rolling' if ds == 'rolling' else 'Annualized'} Sales {DS[ds]}"
                rows.append(r)
    # de-duplicate (a sale can appear in both datasets)
    seen, out = set(), []
    for r in rows:
        key = (r.get("block"), r.get("lot"), r.get("sale_date", "")[:10], r.get("sale_price"))
        if key not in seen:
            seen.add(key); out.append(r)
    return out


def cmd_subject(a):
    print(json.dumps(subject_facts(a.bbl), indent=1))


def cmd_sales(a):
    subj = subject_facts(a.bbl)
    boro, block, _ = parse_bbl(subj["bbl"])
    s_lat, s_lon = float(subj["latitude"]), float(subj["longitude"])
    s_addrs = set(subj["building_addresses"])
    blocks, lot_xy = [block], {}
    if a.radius_mi:
        d_lat = a.radius_mi / 69.0
        d_lon = a.radius_mi / (69.0 * math.cos(math.radians(s_lat)))
        where = (f"latitude between {s_lat-d_lat} and {s_lat+d_lat} AND "
                 f"longitude between {s_lon-d_lon} and {s_lon+d_lon} AND borough='{BORO[boro]}'")
        for p in q("pluto", {"$where": where, "$select": "block,lot,address,latitude,longitude", "$limit": 50000}):
            if p.get("latitude"):
                blocks.append(int(p["block"]))
                lot_xy[(int(p["block"]), norm_street(p["address"]))] = (float(p["latitude"]), float(p["longitude"]))
                lot_xy[(int(p["block"]), int(p["lot"]))] = (float(p["latitude"]), float(p["longitude"]))
    rows = sales_on_blocks(boro, blocks, a.since)
    if not a.all_types:
        rows = [r for r in rows if keep_sale(r, a.type)]
    out = []
    for r in rows:
        b, l, street = int(r["block"]), int(r["lot"]), norm_street(r.get("address"))
        same = (b == block and street in s_addrs)
        if r.get("latitude"):
            xy, how = (float(r["latitude"]), float(r["longitude"])), "DOF lat/lon"
        elif (b, l) in lot_xy:
            xy, how = lot_xy[(b, l)], "PLUTO lot"
        elif (b, street) in lot_xy:
            xy, how = lot_xy[(b, street)], "PLUTO address on block"
        elif same:
            xy, how = (s_lat, s_lon), "same building"
        else:
            xy, how = None, "unknown"
        dist = 0.0 if same else (round(miles(s_lat, s_lon, *xy), 3) if xy else None)
        if a.radius_mi and dist is not None and dist > a.radius_mi and not same:
            continue
        out.append({
            "same_building": same, "distance_mi": dist, "distance_method": how,
            "address": (r.get("address") or "").split(",")[0], "apt": r.get("apartment_number", ""),
            "bbl": f"{boro}{b:05d}{l:04d}", "category": r.get("building_class_category"),
            "sale_price": int(float(r["sale_price"])), "sale_date": r["sale_date"][:10],
            **dof_fields(r), "source": r["_source"], "retrieved": TODAY,
        })
    # flag package deals: several units sharing one date and price are rarely arm's-length single sales
    from collections import Counter
    pk = Counter((x["sale_date"], x["sale_price"]) for x in out)
    for x in out:
        x["flag"] = "multi-unit same date+price" if pk[(x["sale_date"], x["sale_price"])] > 1 else ""
    out.sort(key=lambda x: (not x["same_building"], x["distance_mi"] if x["distance_mi"] is not None else 9, x["sale_date"]))
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"sales_{subj['bbl']}_{TODAY}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()) if out else ["none"])
        w.writeheader(); w.writerows(out)
    with open(os.path.join(a.out, f"subject_pluto_{subj['bbl']}.json"), "w") as f:
        json.dump(subj, f, indent=1)
    same_n = sum(x["same_building"] for x in out)
    print(f"Subject {subj['address']} ({subj['bbl']}), {subj['_note']}. Building addresses: {subj['building_addresses']}")
    print(f"{len(out)} {a.type} sales since {a.since} ({same_n} same building). Full rows: {path}")
    if a.type == "house":
        print("land_sqft and gross_sqft are DOF public record (unverified); gross_sqft is DOF gross, never living area.")
    print("same | dist_mi | sale_date | price | address, apt | bbl | flag")
    for x in out[:MAX_PRINT]:
        print(f"{'Y' if x['same_building'] else ' '} | {x['distance_mi']} | {x['sale_date']} | "
              f"{x['sale_price']:,} | {x['address']} {x['apt']} | {x['bbl']} | {x['flag']}")
    if len(out) > MAX_PRINT:
        print(f"... {len(out)-MAX_PRINT} more in the CSV. Filter there rather than printing more.")


def deed_check(docs, expect_price, expect_date, max_days=31):
    """Compare ACRIS deeds with the DOF row. Pure function so the tests can run it offline.
    Returns (status, detail). status is MATCH, DATE_DIFF, MISMATCH, ZERO_DEED or PARTIAL."""
    exp_d = date.fromisoformat(expect_date)
    best = None
    for d in docs:
        amt = int(float(d.get("document_amt") or 0))
        dd = (d.get("document_date") or "")[:10]
        days = abs((date.fromisoformat(dd) - exp_d).days) if dd else 99999
        if best is None or (amt == expect_price, -days) > (best[0] == expect_price, -best[1]):
            best = (amt, days, dd, d)
    if best is None:
        return "MISMATCH", "no deed found to compare"
    amt, days, dd, d = best
    pct = d.get("percent_trans")
    if amt == 0:
        return "ZERO_DEED", f"closest deed {dd} has amount 0"
    if pct not in (None, "", "100", "100.0", 100, 100.0):
        try:
            if float(pct) < 100:
                return "PARTIAL", f"deed {dd} {amt:,} transfers {pct}%"
        except ValueError:
            pass
    if amt != expect_price:
        return "MISMATCH", f"DOF {expect_price:,} on {expect_date}; closest ACRIS deed {amt:,} on {dd}"
    if days > max_days:
        return "DATE_DIFF", f"amount matches but ACRIS doc date {dd} is {days} days from DOF {expect_date}"
    return "MATCH", f"ACRIS deed {amt:,} on {dd} ({days} days from DOF date)"


def cmd_acris(a):
    if (a.expect_price is None) != (a.expect_date is None):
        die("give both --expect-price and --expect-date (from the DOF row), e.g. --expect-price 1692000 --expect-date 2025-03-14")
    boro, block, lot = parse_bbl(a.bbl)
    legs = q("legals", {"borough": boro, "block": block, "lot": lot, "$limit": 5000})
    ids = sorted({l["document_id"] for l in legs})
    if not ids:
        print(f"No ACRIS legals for {a.bbl}. Co-op units have no deed in ACRIS; use DOF sales and the appraiser's sources.")
        return
    docs = []
    for i in range(0, len(ids), 100):
        chunk = ",".join(f"'{d}'" for d in ids[i:i + 100])
        docs += q("master", {"$where": f"document_id in({chunk}) AND doc_type in('DEED','DEEDO')"
                                       f" AND document_date>='{a.since}'", "$limit": 5000})
    docs.sort(key=lambda d: d.get("document_date", ""), reverse=True)
    print(f"ACRIS deeds for {a.bbl} since {a.since} (retrieved {TODAY}, source ACRIS bnx9-e6tj/8h5j-fqxa):")
    print("doc_date | recorded | amount | doc_type | % transferred | link")
    for d in docs[:MAX_PRINT]:
        link = f"https://a836-acris.nyc.gov/DS/DocumentSearch/DocumentImageView?doc_id={d['document_id']}"
        print(f"{d.get('document_date','')[:10]} | {d.get('recorded_datetime','')[:10]} | "
              f"{int(float(d.get('document_amt') or 0)):,} | {d['doc_type']} | {d.get('percent_trans')} | {link}")
    if not docs:
        print("None found. Check the date range, or whether the transfer used another doc type.")
    if a.expect_price is not None:
        status, detail = deed_check(docs, a.expect_price, a.expect_date)
        print(f"DEED CHECK: {status} - {detail}")
        if status != "MATCH":
            print("Flag this comp to the appraiser and log `ledger.py error --code ACRIS_MISMATCH`. Do not drop it silently.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("subject"); s.add_argument("--bbl", required=True); s.set_defaults(f=cmd_subject)
    s = sp.add_parser("sales")
    s.add_argument("--bbl", required=True)
    s.add_argument("--since", required=True, help="ISO date, e.g. 2025-01-01")
    s.add_argument("--type", required=True, choices=["apartment", "house"],
                   help="apartment = co-op/condo units; house = 1-4 family (DOF 01, 02, 03, and 07 class C3)")
    s.add_argument("--radius-mi", type=float, default=0.0, help="0 = same block only")
    s.add_argument("--out", required=True, help="absolute path to the assignment data folder")
    s.add_argument("--all-types", action="store_true", help="include non-residential categories")
    s.set_defaults(f=cmd_sales)
    s = sp.add_parser("acris"); s.add_argument("--bbl", required=True)
    s.add_argument("--since", default="2015-01-01")
    s.add_argument("--expect-price", type=int, help="DOF sale price for this unit; prints DEED CHECK")
    s.add_argument("--expect-date", help="DOF sale date YYYY-MM-DD; required with --expect-price")
    s.set_defaults(f=cmd_acris)
    a = p.parse_args()
    for flag in ("since", "expect_date"):
        v = getattr(a, flag, None)
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            die(f"--{flag.replace('_', '-')} '{v}' must be YYYY-MM-DD, e.g. 2025-01-01")
    a.f(a)


if __name__ == "__main__":
    main()
