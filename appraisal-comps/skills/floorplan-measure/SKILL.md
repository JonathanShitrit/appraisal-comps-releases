---
name: floorplan-measure
description: Measure one floor plan or house sketch and return a verified area with a confidence rating. Reads printed dimensions and the closed outline (pixel corners on a plan, dimensioned sides on a sketch), runs a script for all arithmetic and checks, and reports labeled total, outline total and convention total separately. Use for stage 4, one plan or sketch at a time, ideally in a subagent per plan.
---

# Floor plan measurement (stage 4)

One plan per run. The lead agent logs `${CLAUDE_PLUGIN_ROOT}/tools/ledger.py event --assignment <folder> --stage 4 --event start` before the first plan, starts one subagent per plan, and logs `--event done` after the last blind review. Each subagent reads its plan, this skill, `conventions.md` and the line library below. It writes only its own `plans/<unit>_rooms.csv`, `plans/<unit>_outline.csv` or `plans/<unit>_sketch.csv` (and `plans/<unit>_attic.csv`), and `plans/<unit>_result.md`, and returns only the result block (about one screen). The lead agent is the only writer of everything else.

## Before reading

1. Check the line library: the rows for this billing BBL in `shared/buildings/lines.csv` and the page `shared/buildings/<billing-BBL>.md` (in the Appraisal Comps folder). If the same line (same letter, stacked layout) is already measured and the plan matches, reuse it and say "reused from line <X>, measured <date>". Still compare the plan visually for combinations or alterations.
2. Read the measurement convention from `settings/conventions.md`. If it is still a placeholder, ask the appraiser before reporting figure (c).

## Which plans (by property type)

- Co-ops and condos: measure the subject's plan and every comp plan.
- Single-family and 2-4 family: measure the subject's sketch (the appraiser's inspection sketch). If it was already measured at intake (`plans/subject_result.md` exists and `gla_sqft` is set), reuse that result. Measure a comp only if it has a plan (rare). Comps without one keep their MLS, listed or public-record size, marked **unverified**; don't measure or estimate them.

## Reading the plan

- Write down each room's printed dimension exactly as printed. Note any scale bar.
- If a digit is hard to read (7'3" vs 7'8"), put it in the `flag` column and ask for a higher-resolution image. Do not pick one.
- If the plan has no dimensions, looks schematic, or is "not to scale", stop. Report it as unmeasurable from this document, low confidence.
- For L-shapes and alcoves, record the bounding rectangle and the notch to subtract.
- Living/dining and kitchen/dining often share floor. If one dimension string may cover another room, fill `overlaps`.
- Scale check: for at least 3 clearly dimensioned walls, read each drawn wall's length in pixels into the `px_w` / `px_d` columns. The script compares feet per pixel and fails the check above 5%. If you can't read pixel lengths, the script says the check was not run. Report that; don't claim the plan is to scale.

## Reading the outline (apartments, and a house plan drawn to scale)

- Trace the inside face of the perimeter walls, corner by corner in order around the unit. Closets, halls and interior walls are inside the outline; terraces and balconies are outside it.
- A house plan traced this way is short of the ANSI exterior by the wall thickness. Say so in the result block ("inside face; exterior walls not added"); its size basis is `plan inside face`, which stays **unverified**. Adding the wall thickness comes in a later release.
- Read each corner's pixel position from the image. Save them as `plans/<unit>_outline.csv` with columns `x,y`, one corner per row, in order.
- The script converts pixels to feet with the scale check's median, so the outline is measured only if the scale check ran (3 or more walls) and passed. If it didn't, the outline is **not measured**; report that, never an estimate.

## Reading a house sketch

- One `floor` at a time. Write its exterior sides in order around it, each with its printed length and its direction: `R`, `L`, `U` or `D`. Save as `plans/<unit>_sketch.csv` with columns `floor,area_type,side,length,direction,flag`.
- `area_type` for the whole floor: `finished`, `below_grade`, `unfinished`, `garage` or `porch`. Only finished above-grade floors count; the script lists the others as excluded.
- When a side is missing, unreadable or angled (a bay, a clipped corner), flag it and leave it out, or give the direction as written. Never fill it in. The script marks that floor **not measured** until the appraiser gives the missing dimensions.
- Attics and sloped ceilings: one row per area in `plans/<unit>_attic.csv` with columns `floor,dims,pct_7ft_plus,dims_5ft_plus`. The heights come only from the appraiser's inspection. If he hasn't given them, ask; never guess.

## Arithmetic (always the script, never mental math)

Write `<assignment>/plans/<unit>_rooms.csv` (for the subject's own plan, use `subject` as `<unit>`) with columns `room,dims,subtract,flag,overlaps,px_w,px_d` (use `10-6` style dims), then run from the Appraisal Comps folder, with the convention from `settings/conventions.md`:

```
python3 -I ${CLAUDE_SKILL_DIR}/scripts/plan_area.py <rooms.csv> --listed <listed sqft> \
  --outline <assignment>/plans/<unit>_outline.csv --convention <labeled|outline> \
  --out <assignment>/plans/<unit>_result.md
```

For a house sketch, the rooms CSV is optional:

```
python3 -I ${CLAUDE_SKILL_DIR}/scripts/plan_area.py --sketch <assignment>/plans/<unit>_sketch.csv \
  [--attic <assignment>/plans/<unit>_attic.csv] --listed <listed sqft> --convention outline \
  --out <assignment>/plans/<unit>_result.md
```

If the script reports a check (inches of 12 or more, duplicate room, notch wider than the room, overlap, scale, outline smaller than the rooms, unlabeled space over 20%, an outline that crosses itself, a sketch walk that doesn't close, listed off by more than 10%), deal with it or show it. Do not delete the check or edit the CSV to make it pass.

## Result block (return exactly this)

1. Room table from the script.
2. (a) labeled rooms total, (d) outline total (or **not measured** with the script's reason), the unlabeled space, and (c) total under the convention. For a sketch, each floor's line, the excluded floors and the attic lines. When (d) is **not measured**, (c) is the labeled total marked "labeled only, outline not measured", and the lead agent logs `ledger.py error --code OUTLINE_NOT_MEASURED`.
3. Listed vs measured difference.
4. Confidence: HIGH / MEDIUM / LOW with the reason. It may not be higher than the script's ceiling.
5. Flags: unreadable digits, schematic plan, scale mismatch, conflicts with listed size, apparent combination or alteration.
6. Line ID if known (building, line letter, floors covered).
7. Script checks raised (copy the "Checks that need attention" lines), so the lead agent can log real misreads with `${CLAUDE_PLUGIN_ROOT}/tools/ledger.py correction --found-by script`.
