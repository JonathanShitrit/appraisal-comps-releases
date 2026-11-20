---
name: comp-export
description: Build the weekly learning export from closed-out assignments for the appraiser to review and approve. Use when the appraiser says "export" or "send learning data", or for the weekly export.
---

# Learning export

Purpose: give the product maintainer as much useful data as possible to improve the next version. The appraiser reviews every export. Nothing is sent: the appraiser sends the approved zip himself.

The script is `${CLAUDE_PLUGIN_ROOT}/tools/export_learning.py`.

## Modes (set once in `settings/export.json`)

- `firm`: the maintainer works inside the appraiser's firm. The export adds comp addresses, BBLs, units, exact sale dates, sale prices and ACRIS links, comps linked to their assignment, and the appraiser's adjustments and final value. Treat it as confidential firm data: transfer it only through firm-controlled storage or an encrypted file, never plain email.
- `external`: for appraisers outside the firm. Everything is de-identified, and there are no prices, adjustments, values or links.

Both modes always leave out the subject's address, unit and BBL, the exact effective date (month only), inspection notes, log text, folder names, client/borrower/lender/AMC/order details, non-ACRIS links and floor plan images. If a comp is the subject's own prior sale, its address, unit, BBL, price and exact date are masked.

## Steps

1. First time only: ask the appraiser for a contributor code (like `A-001`, not a name) and the mode, then run `export_learning.py init --contributor A-001 --mode firm`. To switch later, run `export_learning.py mode external`. Never put `settings/export.json` into an export or share it, because its salt keeps the assignment codes unreadable.
2. Run `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/ledger.py check`. Fix any column problem before exporting.
3. Run `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/export_learning.py build`. It writes `exports/_pending/<date>/` and prints `MANIFEST.md`.
4. Show the appraiser:
   - the mode, the MANIFEST table (files and row counts) and the "Left out" list;
   - 3 sample rows from `assignments.csv`, `comp_candidates.csv` and `corrections.csv` (and `adjusted_comps.csv` in firm mode);
   - every `BLOCK` and `CHECK` line from the leak scan.
5. If the scan says BLOCKED, do not approve. Find the source line (usually a note in `shared/corrections.csv`, `shared/lessons.md`, an adjustment note or a building page), show it to the appraiser, and fix it with his OK. Edit only that text (in a CSV, only that one cell; never the header), note the fix in the assignment's `log.md`, run `ledger.py check`, and rebuild. Never weaken the scanner or edit the export files to make it pass.
6. Ask the appraiser to choose: approve as is, drop files (`build --drop corrections.csv,lessons.md`), or leave out his comp choices (`build --no-selection`). Rebuild after any change.
7. Only after he says approve: `export_learning.py approve`. It is logged in `exports/export_log.csv`. Tell him the zip name in `exports/`, and that he decides whether and how to send it.

## What the export contains (tell the appraiser if asked)

| File | Both modes | Firm mode adds |
| --- | --- | --- |
| assignments.csv | Borough, type, beds, baths, size band, decade built, building size band, search settings, counts, subject UAD condition/quality, error count | Final value, approach, value per sq ft |
| comp_candidates.csv | Ranking features, score, rank, proposed, his keep/remove choice, comp UAD codes, distance band | Comp BBL, address, unit, exact distance, exact sale date, sale price, source, ACRIS link, beds, baths, size |
| adjustments.csv / adjusted_comps.csv | none | His adjustment lines, plus net, gross and adjusted price computed by the script |
| corrections.csv | AI value vs corrected value, month only, never both an assignment code and a building | Exact date, assignment code and building together |
| lines.csv | Comp line measurements, month only, no assignment link | Exact date and assignment code |
| errors.csv | Structured error codes per stage, month only | Exact timestamp |
| timing.csv | Minutes per stage, working vs waiting | none |
| plan_readings.csv / plan_results.csv | Room-by-room first and blind readings and totals, plan as a code | none |
| building_notes.csv, lessons.md, evals_*.csv | Building facts, one-line lessons, calibration results (plan file names dropped) | none |
