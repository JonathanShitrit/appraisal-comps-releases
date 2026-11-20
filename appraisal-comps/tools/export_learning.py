#!/usr/bin/env python3
"""
export_learning.py - build the learning export for the product maintainer.

The appraiser runs this, reviews what it wrote, and approves it. Nothing is sent
anywhere: the approved zip sits in exports/ and the appraiser decides whether to
send it.

Commands (run from the Appraisal Comps folder)
  init     --contributor A-001 --mode firm|external   one time: contributor code, mode, random salt
  mode     firm|external                 change the mode later (keeps the salt)
  build    [--drop file1,file2] [--no-selection]
                                       writes exports/_pending/<date>/ and a MANIFEST.md, runs the leak scan
  approve                              only after the appraiser has reviewed the pending export:
                                       zips it to exports/<date>_learning_<contributor>.zip and logs it

Modes
  external  for appraisers OUTSIDE the firm: everything de-identified, no row links an assignment to a building,
            no prices, adjustments, values or links.
  firm      for use INSIDE the appraiser's firm (the maintainer works in the firm). Adds comp addresses, BBLs,
            units, exact sale dates, sale prices, ACRIS links, comps linked to their assignment, and the
            appraiser's own adjustments and final value. Treat a firm export as confidential firm data.

Both modes
  lines.csv, building_notes.csv, lessons.md, corrections.csv, timing.csv, errors.csv (structured codes),
  assignments.csv (incl. subject UAD condition/quality codes), comp_candidates.csv (incl. comp UAD codes),
  plan_readings.csv, plan_results.csv, evals_runs.csv, evals_calibration.csv
Firm mode only
  adjustments.csv (appraiser's adjustment lines), adjusted_comps.csv (net/gross/adjusted price, computed here),
  final value and approach in assignments.csv

Never in any export
  subject address, unit, BBL, exact effective date (month only), inspection notes, log text, folder names,
  client/borrower/lender/AMC/order details, links other than ACRIS, floor plan images, settings/export.json.
  A comp that is the subject's own prior sale is masked.
"""
import argparse, csv, hashlib, json, re, secrets, shutil, sys, zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace import plugin_version, require_workspace

ROOT = require_workspace(Path.cwd())
SETTINGS = ROOT / "settings" / "export.json"
EXPORTS = ROOT / "exports"
PENDING = EXPORTS / "_pending"
TODAY = date.today().isoformat()
VERSION = plugin_version()

BLOCK_RE = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "email address"),
    (re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"), "phone number"),
    (re.compile(r"\b(borrower|lender|amc|client|loan\s*(no|#|number)?|order\s*(no|#|number)|mortgagee?|"
                r"appraisal\s*management|underwriter)\b", re.I), "client/lender/order word"),
    (re.compile(r"(?:https?://|www\.)(?!a836-acris\.nyc\.gov/)\S*"), "link (only ACRIS links are allowed)"),
    (re.compile(r"\$\s?\d[\d,]{4,}"), "dollar amount"),
]
ACRIS_RE = re.compile(r"https?://a836-acris\.nyc\.gov/\S*")
MONEY_OK_FIRM = {"comp_candidates.csv", "adjustments.csv", "adjusted_comps.csv", "assignments.csv", "corrections.csv"}
ACRIS_OK_FIRM = {"comp_candidates.csv"}
DIST_BANDS = [(0.0001, "same building/0"), (0.1, "<0.1 mi"), (0.25, "0.1-0.25 mi"), (0.5, "0.25-0.5 mi"),
              (1.0, "0.5-1 mi"), (99, ">1 mi")]


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def settings():
    if not SETTINGS.exists():
        die("run `export_learning.py init --contributor <code>` first (e.g. A-001; a code, not a name)")
    return json.loads(SETTINGS.read_text())


def h(salt, *parts):
    return hashlib.sha256((salt + "|" + "|".join(parts)).encode()).hexdigest()[:10]


def read_csv(p):
    if not p.exists() or p.stat().st_size == 0:
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(p, rows, header=None):
    header = header or (list(rows[0].keys()) if rows else ["none"])
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    return len(rows)


def month(s):
    m = re.match(r"(\d{4}-\d{2})", s or "")
    return m.group(1) if m else ""


def num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def band(v, step):
    return "" if v is None else f"{int(v // step * step)}-{int(v // step * step + step - 1)}"


def units_band(v):
    if v is None:
        return ""
    for lim, lab in ((20, "<20"), (50, "20-49"), (100, "50-99"), (200, "100-199")):
        if v < lim:
            return lab
    return "200+"


def dist_band(v):
    v = num(v)
    if v is None:
        return "unknown"
    for lim, lab in DIST_BANDS:
        if v < lim:
            return lab
    return ">1 mi"


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


def cmd_init(a):
    if SETTINGS.exists():
        die(f"{SETTINGS.relative_to(ROOT)} already exists. Keep the same salt so codes stay stable (use `mode` to switch)")
    if not re.fullmatch(r"[A-Z]-\d{3}", a.contributor):
        die("contributor code looks like A-001 (a code, not a name)")
    SETTINGS.write_text(json.dumps({"contributor": a.contributor, "mode": a.mode, "salt": secrets.token_hex(16)}, indent=1))
    print(f"saved {SETTINGS.relative_to(ROOT)} (mode {a.mode}). Never include this file in an export (the salt protects the codes)")


def cmd_mode(a):
    s = settings()
    s["mode"] = a.mode
    SETTINGS.write_text(json.dumps(s, indent=1))
    print(f"export mode is now {a.mode}")


def cmd_build(a):
    s = settings(); salt = s["salt"]
    firm = s.get("mode") == "firm"
    drop = {x.strip() for x in (a.drop or "").split(",") if x.strip()}
    out = PENDING / TODAY
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*"):
        old.unlink()
    sensitive = []          # (text, label, strict)
    counts, excluded = {}, defaultdict(int)

    # --- per-assignment records
    asg_rows, cand_rows, time_rows, read_rows, res_rows = [], [], [], [], []
    err_rows, adj_rows, adjc_rows = [], [], []
    ref_of = {}
    for d in sorted((ROOT / "assignments").glob("*")):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        ref = "asg_" + h(salt, d.name)
        ref_of[d.name] = ref
        sensitive.append((d.name, "assignment folder name", True))
        if not closed(d):
            excluded["open assignments (not closed out)"] += 1
            continue
        subj = json.loads((d / "subject.json").read_text() or "{}") if (d / "subject.json").exists() else {}
        sb = re.sub(r"\D", "", str(subj.get("bbl") or ""))
        if len(sb) == 10:
            # a condo unit lot identifies the subject itself; a co-op lot is the whole building
            sensitive.append((sb, "subject unit BBL" if int(sb[6:]) >= 1001 else "subject building BBL",
                              int(sb[6:]) >= 1001))
        if subj.get("address") and subj.get("unit"):
            sensitive.append((f"{subj['address']} {subj['unit']}", "subject address+unit", True))
        if subj.get("billing_bbl"):
            sensitive.append((str(subj["billing_bbl"]), "subject building BBL", False))
        if subj.get("address"):
            sensitive.append((str(subj["address"]), "subject building address", False))
        notes = subj.get("inspection_notes") or ""
        for i in range(0, max(len(notes) - 24, 0), 12):
            sensitive.append((notes[i:i + 25], "inspection notes", True))
        s_unit_bbl = sb if len(sb) == 10 and int(sb[6:]) >= 1001 else ""
        s_addr, s_unit = norm(subj.get("address")), norm(subj.get("unit"))
        pl = subj.get("pluto") or {}
        eff = subj.get("effective_date") or ""
        since = (subj.get("search") or {}).get("since", "")
        rk = read_csv(d / "data" / "ranking.csv")
        # houses carry their living area in gla_sqft; apartments in interior_sqft_stated
        sqft = num(subj.get("gla_sqft"))
        if sqft is None:
            sqft = num(subj.get("interior_sqft_stated"))
        yb = num(pl.get("yearbuilt"))
        corr_n = sum(1 for c in read_csv(ROOT / "shared" / "corrections.csv") if c.get("assignment_ref") == d.name)
        plans = measured_plan_stems(d / "plans")
        asg_rows.append({
            "assignment": ref, "effective_month": month(eff),
            "borough": {"1": "MN", "2": "BX", "3": "BK", "4": "QN", "5": "SI"}.get(str(subj.get("bbl", " "))[0], ""),
            "building_type": subj.get("building_type", ""), "beds": subj.get("beds", ""), "baths": subj.get("baths", ""),
            "size_band_sqft": band(sqft, 50), "decade_built": f"{int(yb // 10 * 10)}s" if yb else "",
            "building_units_band": units_band(num(pl.get("unitsres"))),
            "radius_mi": (subj.get("search") or {}).get("radius_mi", ""),
            "lookback_months": months_between(since, eff) if since and eff else "",
            "candidates_ranked": len(rk), "same_building_candidates": sum(r.get("same_building") in ("True", "Y", "1") for r in rk),
            "proposed": sum(r.get("proposed") in ("Y", "True", "1") for r in rk),
            "appraiser_removed": sum(r.get("appraiser_decision") == "removed" for r in rk),
            "appraiser_added": sum(r.get("appraiser_decision") == "added" for r in rk),
            "used_in_grid": sum(r.get("used_in_grid") in ("Y", "True", "1") for r in rk),
            "plans_measured": len(plans), "corrections": corr_n,
            "subject_uad_condition": subj.get("uad_condition", ""), "subject_uad_quality": subj.get("uad_quality", ""),
            "errors_logged": len(read_csv(d / "errors.csv")), "version": VERSION})
        concl = read_csv(d / "data" / "conclusion.csv")
        if firm and concl:
            asg_rows[-1]["final_value"] = concl[-1].get("final_value", "")
            asg_rows[-1]["value_approach"] = concl[-1].get("approach", "")
            asg_rows[-1]["value_per_subject_sqft"] = (round(num(concl[-1]["final_value"]) / sqft)
                                                      if sqft and num(concl[-1].get("final_value")) else "")
        elif concl:
            excluded["final value (firm mode only)"] += 1
        by_id = {}
        for r in rk:
            prior = bool((s_unit_bbl and r.get("bbl") == s_unit_bbl) or
                         (s_addr and s_unit and norm(r.get("address")) == s_addr and norm(r.get("apt")) == s_unit))
            row = {"assignment": ref, "candidate_id": r.get("candidate_id", ""),
                   "same_building": r.get("same_building", ""),
                   "distance_band": dist_band(r.get("distance_mi")),
                   "months_before_effective": r.get("months_before_effective") or months_between(r.get("sale_date", ""), eff),
                   "size_diff_pct": round(num(r["size_diff_pct"])) if num(r.get("size_diff_pct")) is not None else "",
                   "bed_diff": r.get("bed_diff", ""), "bath_diff": r.get("bath_diff", ""), "score": r.get("score", ""),
                   "rank": r.get("rank", ""), "proposed": r.get("proposed", ""),
                   "appraiser_decision": "" if a.no_selection else r.get("appraiser_decision", ""),
                   "exclusion_reason": r.get("exclusion_reason", ""),
                   "used_in_grid": "" if a.no_selection else r.get("used_in_grid", ""),
                   "uad_condition": r.get("uad_condition", ""), "uad_quality": r.get("uad_quality", ""),
                   "subject_prior_sale": "Y" if prior else "N"}
            if firm:
                row.update({"bbl": r.get("bbl", ""), "address": r.get("address", ""), "apt": r.get("apt", ""),
                            "distance_mi": r.get("distance_mi", ""), "sale_date": r.get("sale_date", ""),
                            "sale_price": r.get("sale_price", ""), "source": r.get("source", ""),
                            "acris_link": r.get("acris_link", ""), "beds": r.get("beds", ""), "baths": r.get("baths", ""),
                            "listed_or_measured_sqft": r.get("listed_or_measured_sqft", "")})
                if prior:   # the subject's own earlier sale would name the subject unit
                    row.update({"bbl": "", "address": "", "apt": "", "sale_date": month(r.get("sale_date")),
                                "sale_price": "", "acris_link": ""})
                    excluded["subject prior sale: address, unit, BBL, price, exact date masked"] += 1
            by_id[r.get("candidate_id", "")] = (r, prior)
            cand_rows.append(row)
        for e in read_csv(d / "errors.csv"):
            err_rows.append({"assignment": ref, "stage": e.get("stage", ""), "code": e.get("code", ""),
                             "detail": e.get("detail", ""), "version": e.get("version", ""),
                             "when": e.get("timestamp", "") if firm else month(e.get("timestamp"))})
        adj = read_csv(d / "data" / "adjustments.csv")
        if adj and not firm:
            excluded["adjustments (firm mode only)"] += len(adj)
        if firm and adj:
            tot = defaultdict(lambda: [0, 0])
            for x in adj:
                r, prior = by_id.get(x.get("candidate_id", ""), ({}, False))
                amt = num(x.get("amount")) or 0
                tot[x.get("candidate_id", "")][0] += amt; tot[x.get("candidate_id", "")][1] += abs(amt)
                adj_rows.append({"assignment": ref, "candidate_id": x.get("candidate_id", ""),
                                 "comp_address": "" if prior else r.get("address", ""),
                                 "comp_apt": "" if prior else r.get("apt", ""),
                                 "feature": x.get("feature", ""), "amount": x.get("amount", ""),
                                 "note": x.get("note", ""), "version": x.get("version", "")})
            for cid, (net, gross) in tot.items():
                r, prior = by_id.get(cid, ({}, False))
                price = None if prior else num(r.get("sale_price"))
                adjc_rows.append({"assignment": ref, "candidate_id": cid, "sale_price": round(price) if price else "",
                                  "net_adjustment": round(net), "gross_adjustment": round(gross),
                                  "net_pct": round(net / price * 100, 1) if price else "",
                                  "gross_pct": round(gross / price * 100, 1) if price else "",
                                  "adjusted_price": round(price + net) if price else "",
                                  "computed_by": "export_learning.py from the appraiser's entries"})
        time_rows += timing_rows(d, ref)
        for stem in plans:
            rooms = d / "plans" / f"{stem}_rooms.csv"
            pref = "plan_" + h(salt, d.name, stem)
            is_subj = "Y" if stem.lower().startswith("subject") else "N"
            for reading, f in (("first", rooms), ("review", d / "plans" / f"{stem}_rooms_review.csv")):
                for r in read_csv(f):
                    read_rows.append({"plan": pref, "is_subject": is_subj, "reading": reading,
                                      "room": r.get("room", ""), "dims": r.get("dims", ""), "subtract": r.get("subtract", ""),
                                      "flag": r.get("flag", ""), "overlaps": r.get("overlaps", "")})
            res = d / "plans" / f"{stem}_result.md"
            if res.exists():
                res_rows.append({"plan": pref, "assignment": ref, "is_subject": is_subj, **parse_result(res)})
        if subj.get("inspection_notes"):
            excluded["inspection notes (always excluded)"] += 1
        excluded["log.md text (structured error codes and timestamps only)"] += 1

    files = {}
    files["assignments.csv"] = asg_rows
    files["comp_candidates.csv"] = cand_rows
    files["timing.csv"] = time_rows
    files["plan_readings.csv"] = read_rows
    files["plan_results.csv"] = res_rows
    files["errors.csv"] = err_rows
    if firm:
        files["adjustments.csv"] = adj_rows
        files["adjusted_comps.csv"] = adjc_rows

    # --- shared learning files
    lines = []
    for r in read_csv(ROOT / "shared" / "buildings" / "lines.csv"):
        if r.get("is_subject") == "Y":
            excluded["subject line rows (location kept private)"] += 1
            continue
        r = dict(r); aref = r.pop("assignment_ref", "")
        if firm:
            r["assignment"] = ref_of.get(aref, "asg_unknown")   # comps linked to their assignment
        else:
            r["measured_on"] = month(r.get("measured_on"))
        lines.append(r)
    files["lines.csv"] = lines
    notes = []
    for p in sorted((ROOT / "shared" / "buildings").glob("*.md")):
        t = p.read_text()
        m = re.search(r"## Notes\s*\n(.*)", t, re.S)
        body = (m.group(1) if m else "")
        body = "\n".join(l for l in body.splitlines() if l.strip() and not l.startswith("(Combinations")).strip()
        if body:
            notes.append({"billing_bbl": p.stem, "notes": body})
    files["building_notes.csv"] = notes
    corr = []
    for r in read_csv(ROOT / "shared" / "corrections.csv"):
        r = dict(r)
        ref = ref_of.get(r.pop("assignment_ref", ""), "asg_unknown")
        if firm:
            r["assignment"] = ref
        else:
            # external: a row never carries both an assignment code and a building BBL
            r["assignment"] = "" if r.get("billing_bbl") else ref
            r["date"] = month(r.get("date"))
        corr.append(r)
    files["corrections.csv"] = corr
    files["evals_runs.csv"] = read_csv(ROOT / "shared" / "evals" / "runs.csv")
    cal = read_csv(ROOT / "shared" / "evals" / "calibration.csv")
    for r in cal:
        r.pop("plan_file", None)
    files["evals_calibration.csv"] = cal

    for name, rows in files.items():
        if name in drop:
            excluded[f"{name} (dropped by appraiser)"] += 1
            continue
        counts[name] = write_csv(out / name, rows)
    if "lessons.md" not in drop and (ROOT / "shared" / "lessons.md").exists():
        shutil.copyfile(ROOT / "shared" / "lessons.md", out / "lessons.md")
        counts["lessons.md"] = sum(1 for l in (out / "lessons.md").read_text().splitlines() if l.startswith("-"))

    # --- leak scan
    blocks, warns = [], []
    lenient = {"lines.csv", "building_notes.csv", "corrections.csv"}   # comps in the subject's building legitimately name it
    if firm:
        lenient |= {"comp_candidates.csv", "adjustments.csv", "adjusted_comps.csv"}
    for f in sorted(out.glob("*")):
        t = f.read_text()
        if firm and f.name in ACRIS_OK_FIRM:
            t = ACRIS_RE.sub("", t)
        T = norm(t)
        for rx, label in BLOCK_RE:
            if firm and label == "dollar amount" and f.name in MONEY_OK_FIRM:
                continue
            for m in rx.finditer(t):
                blocks.append(f"{f.name}: {label} '{m.group(0)[:40]}'")
        for text, label, strict in sensitive:
            if text and len(text.strip()) >= 4 and norm(text) in T:
                (warns if (not strict and f.name in lenient) else blocks).append(f"{f.name}: contains {label}")
    blocks = sorted(set(blocks)); warns = sorted(set(warns))

    man = [f"# Learning export {TODAY}", "",
           f"Contributor: {s['contributor']} | Mode: {'FIRM (confidential firm data)' if firm else 'external (de-identified)'} | "
           f"Product version: {VERSION} | Built: {datetime.now().isoformat(timespec='minutes')}",
           "", "Status: PENDING appraiser review. Nothing has been sent.", "", "| File | Rows |", "| --- | --- |"]
    man += [f"| {k} | {v} |" for k, v in counts.items()]
    man += ["", "## Left out", ""] + ([f"- {k}: {v}" for k, v in excluded.items()] or ["- nothing extra"])
    man += ["- Always: subject address, unit, BBL, exact effective date, inspection notes, log text, folder names, "
            "client/lender/AMC/order details, non-ACRIS links, plan images."
            + ("" if firm else " External mode also leaves out comp addresses, BBLs, prices, exact dates, ACRIS links, "
               "adjustments and values."),
            "", "## Leak scan", "", f"Result: {'BLOCKED' if blocks else 'PASS'}"]
    man += [f"- BLOCK {b}" for b in blocks] + [f"- CHECK {w} (the appraiser confirms this is a comp, not the subject)" for w in warns]
    (out / "MANIFEST.md").write_text("\n".join(man) + "\n")
    print("\n".join(man))
    print(f"\nPending export: {out.relative_to(ROOT)}")
    sys.exit(1 if blocks else 0)


def cmd_approve(a):
    s = settings()
    out = PENDING / TODAY
    man = out / "MANIFEST.md"
    if not man.exists():
        die("no pending export for today. Run build first")
    t = man.read_text()
    if "Result: PASS" not in t:
        die("the leak scan did not pass. Fix the source file (or drop the file) and rebuild")
    final = EXPORTS / f"{TODAY}_learning_{s['contributor']}"
    if final.exists():
        die(f"{final.name} already exists. One approved export per day")
    man.write_text(t.replace("Status: PENDING appraiser review. Nothing has been sent.",
                             f"Status: APPROVED by appraiser {datetime.now().isoformat(timespec='minutes')}. "
                             "Not sent; the appraiser sends it."))
    shutil.move(str(out), str(final))
    z = EXPORTS / f"{final.name}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(final.glob("*")):
            zf.write(f, f"{final.name}/{f.name}")
    log = EXPORTS / "export_log.csv"
    new = not log.exists()
    with open(log, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "file", "version", "files"])
        w.writerow([TODAY, z.name, VERSION, len(list(final.glob("*")))])
    print(f"Approved: {z.relative_to(ROOT)}. Send it yourself if you choose to; this tool does not send anything.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    x = sp.add_parser("init"); x.add_argument("--contributor", required=True)
    x.add_argument("--mode", required=True, choices=["firm", "external"]); x.set_defaults(f=cmd_init)
    x = sp.add_parser("mode"); x.add_argument("mode", choices=["firm", "external"]); x.set_defaults(f=cmd_mode)
    x = sp.add_parser("build"); x.add_argument("--drop", default="")
    x.add_argument("--no-selection", action="store_true", help="leave out which comps the appraiser kept or used")
    x.set_defaults(f=cmd_build)
    x = sp.add_parser("approve"); x.set_defaults(f=cmd_approve)
    a = p.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
