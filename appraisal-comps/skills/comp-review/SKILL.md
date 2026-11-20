---
name: comp-review
description: Independent check of a finished floor plan measurement or comp grid by a fresh reviewer with no shared context. Re-reads the source plan blind, recomputes, and lists disagreements. Use on every floor plan measurement (stage 4) and on the comp grid before it goes to the appraiser.
---

# Independent review

Agents grade their own work too generously. Run this in a separate subagent that has NOT seen the first measurement.

## Blind re-measure (floor plans)

1. Input: the plan image path and the measurement convention only. Do not read `<unit>_result.md`, `<unit>_rooms.csv`, `lines.csv` or the building page yet.
2. Follow the floorplan-measure skill from "Reading the plan" on (skip "Before reading"). Write `<unit>_rooms_review.csv`, and re-read the outline corners or the sketch sides blind into `<unit>_outline_review.csv` or `<unit>_sketch_review.csv`. Run the script on the review files with `--out plans/<unit>_result_review.md`, so the first result is never overwritten.
3. Only now open the first result. Compare room by room.

Report:

| Room | First read | Review read | Agree? |
| --- | --- | --- | --- |

Then: labeled totals from both, the (d) outline figures from both, the differences in percent (computed in Python), and every disagreeing digit, corner or side. If labeled totals differ by more than 2%, the two (d) figures differ by more than 3%, or any digit or side disagrees, the measurement goes into the grid as LOW confidence with both readings shown, and the appraiser settles it there (stage 5). The reviewer does not pick a winner.

At close-out, the lead agent (not the reviewer) logs every disagreement the appraiser settled: `${CLAUDE_PLUGIN_ROOT}/tools/ledger.py correction --assignment <folder> --stage 4 --found-by reviewer --category digit|overlap|notch|room_missing|room_extra|outline|sketch --field "<room> <width|length>" --ai-value "<first read>" --corrected-value "<value the appraiser accepted>"` (add `--subject` for the subject plan, otherwise `--billing-bbl` and `--line`). Keep `<unit>_rooms_review.csv` in `plans/`; the export compares both readings.

## Comp grid check

For each comp row, check against the source files in `data/` and `plans/`:

- Sale price and date match the DOF row and, for condos and houses, the ACRIS deed.
- Price per measured sq ft is recomputed and matches.
- Every number has a source and a retrieval date.
- Bed and bath counts match their named source. Where the plan and the listing disagree, both values are shown.
- No cell holds a value that is not in a source (an invented size, bed count or date).

Output a list of problems only. "No problems found" is a valid answer. Do not edit the grid. Hand the list to the lead agent.
