---
name: comp-grid
description: Assemble the comp rows for appraiser review (stage 5), then after the appraiser finishes, close out the assignment by saving line measurements to the building wiki. Use after floor plans are measured and reviewed, and again when the appraiser says the assignment is done.
---

# Comp grid and close-out (stages 5 and 6)

`settings/conventions.md` says which figure goes in the grid.

## Build the grid

Log `${CLAUDE_PLUGIN_ROOT}/tools/ledger.py event --assignment <folder> --stage 5 --event start`.

Write `<assignment>/comp_grid.csv` (and show it as a table). The columns branch on `building_type` in `subject.json`.

Co-ops and condos:

| Comp | Address / unit | Floor | Line | Sale price | Sale date | Beds | Baths | Bed/bath source | Listed sq ft | Measured sq ft (figure used) | Diff | $/measured sq ft | Sources | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Single-family and 2-4 family (living area, lot, year built and units in place of floor and line):

| Comp | Address | Sale price | Sale date | Beds | Baths | Bed/bath source | Listed / MLS sq ft | Living area sq ft | Living area basis | DOF gross sq ft | Lot sq ft | Year built | Units | $/living area sq ft | Sources | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

- First row is the subject, from `subject.json`, so every comp can be read against it.
- Beds and baths are not in DOF or ACRIS. Take them from the floor plan, or from listing data or notes the appraiser supplied, and name the source (e.g. "plan", "appraiser paste 11/15"). If the floor plan and the listing disagree, show both (e.g. "2 (plan) / 3 (listing)"). If no source has it, write "not in source". Never infer a count from price or size.
- Baths use the half-bath convention (1.5 = one full plus one half).
- "Measured sq ft" states which figure it is: (a) labeled or (c) convention total.
- Houses: "Living area basis" is `sketch`, `plan inside face`, `MLS`, `listed` or `public record`; anything but the subject's sketch is **unverified**, and says so. A house plan traced in pixels measures the inside face of the walls, short of the ANSI exterior by the wall thickness (9-12 in, `settings/conventions.md`); label it that way. "DOF gross sq ft" is gross building area from DOF, its own column, never living area and never used for $/sq ft.
- Floor comes only from a source; if none has it, "not in source".
- $/sq ft is computed with Python from the CSV, not by hand.
- Sources: DOF dataset + retrieval date, ACRIS link for condos and houses, plan file name.
- Before the bracket check, write each size measured in stage 4 back into `data/ranking.csv` with Python. First copy the current `listed_or_measured_sqft` and `size_basis` into `listed_sqft` and `listed_basis`, so the listed or MLS figure is kept. Then put figure (c) from `plans/<unit>_result.md` into `listed_or_measured_sqft`, set `size_basis` to `measured` (apartment plan) or `plan inside face` (house plan), and recompute `size_diff_pct` against the subject. Skip a plan whose outline is **not measured**. The grid's "Listed sq ft" (apartments) or "Listed / MLS sq ft" (houses) column shows `listed_sqft`.
- Run the comp-review skill's grid check in a subagent. Show its problem list under the grid.
- Under the grid, run `python3 -I ${CLAUDE_PLUGIN_ROOT}/skills/comp-pool/scripts/bracket.py check --assignment <absolute folder> --comps <grid comp ids, e.g. c01,c03,c05>` and show its coverage table. It changes no file. Compare it with `data/bracket.md` from stage 3 and say when a feature lost its bracket after measuring (log `BRACKET_GAP`).

Then log `--stage 5 --event waiting` and STOP. The appraiser chooses comps and makes adjustments. Do not suggest a value, do not draft adjustments or the report unless asked.

## Close-out (only when the appraiser says the assignment is done)

Log `ledger.py event --assignment <folder> --stage 5 --event approved`, then `--stage 6 --event start`.

1. **Line measurements (co-ops and condos only).** For every line measured or reused in this assignment, run once:
   `ledger.py line --billing-bbl <BBL> --address "<building address>" --line <X> --floors <range> --labeled <a> --convention-sqft <c> --convention <labeled|outline> --listed <listed> --confidence <HIGH|MEDIUM|LOW> --plan-source-type "<broker plan|sponsor plan|offering plan|appraiser measured>" --plan-date <year> --assignment <folder>`
   Add `--subject` for the subject's own line and `--reused` if the figure came from an earlier row. This writes `shared/buildings/lines.csv`.
2. **Building notes.** Line figures live only in `lines.csv`; don't copy them into the page. Update `shared/buildings/<billing-BBL>.md` (create it from `${CLAUDE_PLUGIN_ROOT}/templates/building.md` if missing) with property facts only: combinations, alterations, plan quirks, listed sizes that run high, and any new reading that disagrees with an earlier `lines.csv` row (keep both rows; flag it here).
3. **Comp choices.** In `data/ranking.csv`, set `used_in_grid` to Y or N for every candidate, based on which comps the appraiser kept. Fill in any UAD condition/quality codes he gave for the comps.
4. **Adjustments and value (record only; never suggest).** If the appraiser gives his final adjustments and value, record them exactly as he states them:
   `ledger.py adjustment --assignment <folder> --candidate c01 --feature "<Size|Floor level|Condition|View|Location|Outdoor space|Other>" --amount=<signed dollars>` (one row per adjustment; write negatives as `--amount=-25000`)
   `ledger.py value --assignment <folder> --final-value <dollars> --approach sales_comparison|reconciled|other`
   Read them back to him as a table and ask him to confirm. If he doesn't give them, skip this step. Never propose, compute or round an adjustment or value yourself.
5. **Corrections.** For every value the appraiser changed (a misread digit, a wrong exclusion, a bed count, a sale detail), run `ledger.py correction --assignment <folder> --stage <stage where it happened> --found-by appraiser --category <category> --field "<field>" --ai-value "<ours>" --corrected-value "<his>"` (categories are listed in `ledger.py`), if it isn't already logged. Notes are property facts only.
6. **Lessons.** If a correction shows a repeatable mistake, add one line to `shared/lessons.md`: what happened and what to do next time.
7. Mark all stages done in `progress.md`, append the close-out to `log.md`, and log `--stage 6 --event done`.
8. Run `ledger.py check`. Then tell the appraiser the assignment is ready for the next learning export (the comp-export skill), without running the export unless he asks.
