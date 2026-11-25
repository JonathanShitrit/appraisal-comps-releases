---
name: comp-pool
description: Build and rank the comparable sales pool for a NYC residential subject (co-op, condo, single-family or 2-4 family) from DOF sales, ACRIS and PLUTO. Runs one script that saves raw rows to CSV and returns a short summary, ranks the candidates, then proposes comps by bracketing the subject's features. Use for stages 2 and 3 after intake is confirmed.
---

# Candidate pool and ranking (stages 2 and 3)

Read `subject.json` in the assignment folder first, and `settings/conventions.md` for any weights, lookback or radius the appraiser set.

Log the start: `${CLAUDE_PLUGIN_ROOT}/tools/ledger.py event --assignment <folder> --stage 2-3 --event start`.

## 1. Pull sales with the script (do not hand-query the APIs)

```
python3 -I ${CLAUDE_SKILL_DIR}/scripts/nyc_sales.py sales --bbl <BBL> --since <YYYY-MM-DD> --type <apartment|house> --radius-mi <r> --out <assignment>/data
```

- `--type apartment` for a co-op or condo subject, `--type house` for single-family and 2-4 family (DOF one-, two- and three-family categories plus four-family walk-ups). Take it from `building_type` in `subject.json`.
- Search order: same block first (`--radius-mi 0`), then up to the radius in `settings/conventions.md`. `--since` is the effective date minus the lookback in `settings/conventions.md`. Go further only when the pool is too thin to bracket the subject, and say why (log `THIN_POOL`).
- The script prints at most 40 rows. The full set is in `data/sales_<bbl>_<date>.csv`. Filter that CSV with Python. Do not print the whole file into the conversation.
- Each row carries its source dataset and retrieval date. Keep them.

## 2. Confirm recorded closings

- Condo units and houses: run `nyc_sales.py acris --bbl <unit or house BBL> --expect-price <DOF price> --expect-date <DOF date>` on every condo or house candidate before you propose it (house sales are deeds too), and note the ACRIS link. The script prints `DEED CHECK:`. Anything other than MATCH (DATE_DIFF, MISMATCH, ZERO_DEED, PARTIAL) is flagged to the appraiser and logged as `ACRIS_MISMATCH`, not dropped silently. Don't compare amounts by eye.
- Co-ops: share sales are not deeds in ACRIS. DOF sales (categories 09/10) are the public record. Say so in the source column. Bed/bath/size for co-ops must come from the appraiser's own sources or materials they upload.
- Asking prices and listing pages (Zillow, StreetEasy, others) are context only, never the sale price. Read them with the `browser-read` skill; don't bulk-scrape.

## 3. Score and rank

Score each candidate 0 to 5 per factor, then weight, in Python from the sales CSV. Show weights so the appraiser can change them. Use the appraiser's weights from `settings/conventions.md` if set; otherwise the defaults below. The score only sets the order `bracket.py` considers candidates in; it is not a judgment of comparability.

Co-ops and condos:

| Factor | Weight | 5 = | 0 = |
| --- | --- | --- | --- |
| Location | 25% | same building | beyond radius |
| Sale date | 20% | within 6 months of effective date | older than lookback |
| Size | 20% | within 10% of subject | more than 40% off |
| Bedrooms | 15% | same count | 2+ off |
| Bathrooms | 10% | same count | 1.5+ off |
| Condition/known features | 10% | similar per appraiser notes | unknown or very different |

Single-family and 2-4 family:

| Factor | Weight | 5 = | 0 = |
| --- | --- | --- | --- |
| Location | 25% | same block | beyond radius |
| Sale date | 15% | within 6 months of effective date | older than lookback |
| Living area | 15% | within 10% of subject (any basis) | more than 40% off |
| Lot size | 15% | within 10% of subject | more than 50% off |
| Bedrooms | 10% | same count | 2+ off |
| Bathrooms | 10% | same count | 1.5+ off |
| Condition | 10% | same UAD code | 2+ codes off |

Unknown values score 2 and are marked "unknown". Never fill them with a guess.

Exclude, and say why: multi-unit same-date same-price sales (the script flags them), non-residential categories, sales under 100% interest, obvious non-arm's-length transfers, and anything the appraiser has ruled out.

## 4. Save the ranking (the export learns from this)

Write `<assignment>/data/ranking.csv` with Python, one row per candidate scored (included and excluded), with exactly these columns:

`candidate_id,bbl,address,apt,same_building,distance_mi,sale_date,sale_price,source,acris_link,months_before_effective,listed_or_measured_sqft,size_diff_pct,beds,bed_diff,baths,bath_diff,uad_condition,uad_quality,score,rank,proposed,appraiser_decision,exclusion_reason,used_in_grid,property_type,floor,lot_sqft,year_built,units,size_basis,listed_sqft,listed_basis`

- `candidate_id` is `c01`, `c02`, ... (never an address). `proposed` is Y or N. `exclusion_reason` is a short code: `package_sale`, `non_residential`, `partial_interest`, `non_arms_length`, `too_old`, `too_far`, `size`, `beds`, `appraiser_ruled_out`, `low_score` or `other`.
- `sale_price`, `sale_date` and `source` come straight from the DOF row. `acris_link` is the deed link from `nyc_sales.py acris` (condos and houses); leave it blank for co-ops.
- `property_type` is `co-op`, `condo`, `single-family` or `2-4 family`. `lot_sqft`, `year_built` and `units` come from the DOF row (`land_sqft`, `year_built`, `residential_units`) or PLUTO, with that source. `floor` only from a source (plan, listing the appraiser pasted), never from the apartment number.
- `size_basis` says where `listed_or_measured_sqft` came from: `measured`, `plan inside face`, `sketch`, `listed`, `MLS`, `public record` or `DOF gross`. DOF gross square feet (`gross_sqft`) is gross building area, labeled `DOF gross`, never living area.
- Write an unknown value as blank or "not in source". Never fill it to close a bracket.
- `uad_condition` (C1–C6) and `uad_quality` (Q1–Q6) come only from the appraiser or materials he supplies. Leave them blank otherwise.
- Leave `appraiser_decision`, `used_in_grid`, `listed_sqft` and `listed_basis` blank for now (comp-grid fills the last two when a measured size replaces the listed one).

## 5. Propose by bracketing (the script sets `proposed`)

```
python3 -I ${CLAUDE_SKILL_DIR}/scripts/bracket.py propose --assignment <absolute assignment folder>
```

It takes the top 3 eligible candidates by rank, then adds a lower-ranked one only when it closes a gap, until every feature that can be checked is bracketed (one comp at or above the subject, one at or below). It rewrites the `proposed` column, writes `data/bracket.md` and prints the coverage table. Show that table as printed. Don't change `proposed` by hand, and never fill a value to close a gap.

- Every feature marked `not bracketed` is logged: `ledger.py error --assignment <folder> --stage 2-3 --code BRACKET_GAP --detail "<feature>: nothing above|below"`. Suggest widening the radius or lookback; widen only when the appraiser agrees.
- `can't check` means the subject value or every candidate value is unknown. `mixed basis` and `bracketed (unverified)` mean the size bracket rests on different or public-record sizes. Say so; don't hide it.
- If it stops with `ERROR:` (a missing `building_type`, baths not in the half-bath convention), fix the input from a source or ask the appraiser, then re-run.
- House subject with no `gla_sqft` yet: size shows `can't check`. Say so with the table. When the sketch arrives, measure it as comp-intake describes, set `gla_sqft`, and re-run `bracket.py propose` before the appraiser approves the shortlist.

## 6. Output

1. Ranking table: rank, address/unit, sale date, price, distance, beds/baths (source), score, include/exclude with a one-line reason.
2. The proposed comps and the coverage table from `bracket.py`.
3. Before asking him to paste anything, run `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/sources.py lookup --job listing-details --area <subject's borough>` and read the proposed comps there with the `browser-read` skill. Ask the appraiser to paste MLS details (beds, baths, living area, condition) only for what that couldn't find, for the proposed comps and the next few by rank. Public data has none of these, so most of them start as `can't check`. Fill what he pastes into `ranking.csv` (sizes with `size_basis` `MLS`), then re-run `bracket.py propose` and show the new table.
4. Update `progress.md` (stage 2-3 done, proposed comps, open gaps), append to `log.md` (queries run, counts, exclusions).
5. Log `ledger.py event --assignment <folder> --stage 2-3 --event waiting --note "<n> candidates, <k> proposed"`. Stop and wait for the appraiser to approve the shortlist before any floor plan work.
6. When he answers, set `appraiser_decision` in `ranking.csv`: `approved` for kept, `removed` for ones he drops, `added` for comps he adds (add a row for each). Log `--event approved`. If he removed or added a comp, also log a correction: `ledger.py correction --assignment <folder> --stage 2-3 --found-by appraiser --category comp_rank --field "<candidate_id>" --ai-value "proposed rank <r>" --corrected-value "removed" --note "<his reason, property facts only>"`.
