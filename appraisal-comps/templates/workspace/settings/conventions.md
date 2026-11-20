# Measurement conventions (the appraiser's settings)

This file belongs to the appraiser. Product updates never overwrite it. Every skill reads it before reporting figure (c) or building the grid. While a field still shows a placeholder in square brackets, ask the appraiser and don't assume.

## Size

- Measure to:
  - Co-ops and condos: the inside face of the perimeter walls, everything inside included, closets too.
  - Single-family (houses): ANSI Z765, exterior.
  - 2-4 family (houses): the exterior perimeter. There is no standard.
- Exclude: outside space (terraces, balconies), unfinished or unheated space, anything below grade. plan_area.py lists garage and porch floors as excluded (unfinished or outside space).
- Attics and sloped ceilings (houses): count only if more than 50% of the area has a ceiling of at least 7 ft, and then only the area with a ceiling of at least 5 ft. Heights come from the appraiser's inspection.
- Exterior walls not drawn on a house plan (houses): 9 to 12 in.
- Figure used in the grid: the outline total, (d), reported as (c) under the outline convention.
- plan_area.py --convention value: outline
- Rounding: nearest square foot. Always show listed vs measured.
- Calibration tolerance for evals: 3% pass; 3 to 5% "close".

## Comp search and selection

- Default lookback: 24 months from the effective date. Go further only when the pool is thin, and say why.
- Default radius: same block first, then up to 1 mile in the five boroughs.
- Radius in low-density areas: 3 to 5 miles (outside NYC, phase 3).
- Sales: closed sales only.
- Number of comps: at least 3, as many as it takes to bracket every feature of the subject.

## Ranking weights (optional; defaults are in the comp-pool skill)

The weights only set the order in which candidates are considered for bracketing; they are not a judgment of comparability. Leave as default to use the comp-pool skill's defaults. Example: Location 25, Sale date 20, Size 20, Bedrooms 15, Bathrooms 10, Condition 10.

- Weights: default
