---
name: comp-intake
description: Start a NYC appraisal comp assignment for any residential property type. Builds the subject record, creates the assignment folder with progress and log files, and pulls subject facts from PLUTO. Use when the appraiser gives a new subject property or says "new assignment".
---

# Comp intake (stage 1)

Goal: one subject record, saved to disk, that every later stage reads instead of re-asking.

## Steps

1. Read the appraiser's message. Fill what you can of the subject record below. Ask for everything missing in ONE short question. Do not guess a BBL, unit, or effective date.
2. Create the assignment folder: `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/new_assignment.py --name <today's date YYYY-MM-DD>_<street-number>-<street>_<unit>` (houses with no unit: drop `_<unit>`). Don't copy `templates/assignment/` by hand: the plugin is installed read-only, and a plain copy stays read-only. Use the property address only. Never put client, borrower, lender or AMC names in any file or folder name.
3. Log the start: `${CLAUDE_PLUGIN_ROOT}/tools/ledger.py event --assignment <folder> --stage 1 --event start`.
4. Pull subject facts:
   `python3 -I ${CLAUDE_PLUGIN_ROOT}/skills/comp-pool/scripts/nyc_sales.py subject --bbl <BBL>`
   Paste the facts into `subject.json` with source and retrieval date. If PLUTO and the appraiser disagree (year built, unit count), show both and let the appraiser decide.
   - **Property type.** Set `building_type` from the PLUTO building class only when it settles the type (A one family, B two family, C0 three family, C3 four family, C6 or D4 co-op building, R condo). If it doesn't settle it, put the property type in your one question. Every later stage branches on this field.
   - **Houses** (`single-family`, `2-4 family`): record PLUTO `lotarea`, `lotfront`, `lotdepth`, `bldgarea` and `numfloors` under `pluto` with source and date, and copy the lot area into `lot_sqft`. Mark each one `public record`, **unverified**: public records for houses are rarely accurate. `bldgarea` is gross building area, never living area; `gla_sqft` comes only from the appraiser's sketch (floorplan-measure) or a source he names, with that source in `gla_source`.
   - **House subject's sketch, measured now.** Bracketing in stage 3 needs the subject's living area. If the inspection is done, ask for the sketch in your one question. When you have it, save it as `plans/subject.<ext>` (so its files are `subject_sketch.csv` and `subject_result.md`) and measure it before the subject record is confirmed: the floorplan-measure skill in a subagent, then the comp-review blind re-read (log stage 4 `start` and `done` around them as usual). Set `gla_sqft` to figure (c) and `gla_source` to `sketch (plans/subject_result.md)`. If the inspection hasn't happened, leave `gla_sqft` blank and say that size will show `can't check` in stage 3 until the sketch is measured.
5. Check the building wiki: the rows for this billing BBL in `shared/buildings/lines.csv` (line measurements) and `shared/buildings/<billing-BBL>.md` (notes). Say what you found.
6. If any field in `settings/conventions.md` is still a [placeholder], include it in your one question.
7. Write the goal and stage checklist into `progress.md` (the template already has it). Append a first entry to `log.md`.
8. Log the checkpoint: `ledger.py event --assignment <folder> --stage 1 --event waiting`. Stop. Show the subject record as a two-column table and ask the appraiser to confirm before stage 2. When he confirms, log `--event approved`.

## Subject record (subject.json)

```json
{
  "address": "", "unit": "", "bbl": "", "billing_bbl": "",
  "building_type": "co-op | condo | single-family | 2-4 family", "beds": null, "baths": null,
  "interior_sqft_stated": null, "sqft_source": "",
  "floor": null, "lot_sqft": null, "units": null,
  "gla_sqft": null, "gla_source": "",
  "uad_condition": "C1-C6 or blank", "uad_quality": "Q1-Q6 or blank",
  "effective_date": "YYYY-MM-DD",
  "inspection_notes": "", "search": {"since": "YYYY-MM-DD", "radius_mi": null},
  "pluto": {}, "confirmed_by_appraiser": false
}
```

## Rules

- UAD condition and quality ratings come from the appraiser only (after his inspection). Never infer them from photos, listing text or price. Leave them blank until he gives them. The free-text `inspection_notes` stay on this computer and are never exported; the codes are what the product learns from.
- The record holds property facts only. If order paperwork is pasted, take the property facts and do not copy anything else.
- Default lookback and radius are only suggestions until the appraiser confirms them (see `settings/conventions.md`).
- `floor` is for co-ops and condos, and only from a source (plan, offering plan, the appraiser); never read it off the apartment number. `units` is for 2-4 family houses. `lot_sqft` is for houses.
- Write a value that is not in any source as "not in source", never a guess. Baths use the half-bath convention (2.5 = two full, one half).
- The subject's own floor plan, if any, is saved as `plans/subject.*` so its files are named `subject_rooms.csv` and `subject_result.md`.
