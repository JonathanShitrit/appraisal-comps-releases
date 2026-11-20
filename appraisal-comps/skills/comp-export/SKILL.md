---
name: comp-export
description: Build the weekly learning export from closed-out assignments for the appraiser to review and approve. Use when the appraiser says "export" or "send learning data", or for the weekly export.
---

# Learning export

Purpose: give the product maintainer everything from the appraiser's closed assignments, so the next version can learn from how he worked. The appraiser reviews every export and approves it. Nothing is sent: he sends the approved zip to the maintainer himself.

The script is `${CLAUDE_PLUGIN_ROOT}/tools/export_learning.py`. The export goes under the name setup saved in `settings/export.json` (like `dcohen`).

## Steps

1. Run `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/ledger.py check`. Fix any column problem before exporting.
2. Run `python3 -I ${CLAUDE_PLUGIN_ROOT}/tools/export_learning.py build`. It writes `exports/_pending/<date>/` and prints `MANIFEST.md`. If it says no name is saved, or the saved name isn't valid, run setup's name step (`setup_workspace.py --into <this folder> --name <name>`) and build again.
3. Show the appraiser:
   - the MANIFEST: the tables and their row counts, the files copied per assignment, and how many open assignments were left out;
   - 3 sample rows each from `tables/assignments.csv`, `tables/comp_candidates.csv`, `tables/corrections.csv` and `tables/adjusted_comps.csv`.
4. Ask whether to approve. If he wants something changed, fix it at its source with his OK (only that text; in a CSV only that one cell, never the header), note the fix in the assignment's `log.md`, run `ledger.py check`, and build again.
5. Only after he says approve: `export_learning.py approve`. It zips the export to `exports/<date>_learning_<name>.zip` and logs it in `exports/export_log.csv`. Tell him the zip name, and that he sends it to the maintainer himself.

## What the export contains (tell the appraiser if asked)

Read it from the pending export: `README.md` describes every table, column and raw folder (the script generates it, so it always matches), and `MANIFEST.md` has the counts. Answer from those two files in plain words.
