# Changelog

Release steps: `maintainer/Maintainer Checklist.md` step 10.

## 0.7.0 (2026-11-19)

0.6.0 was never released; everything below ships in 0.7.0 for the first time.

Principles touched (`docs/Design Principles.md`): deterministic limits, real checks, one rule in one place, simplest setup that works, scripts beat narrow tools. **The workflow ships as the `appraisal-comps` plugin, with phase 1 (0.5.0) inside it.**

What changed:
- The rules load from the plugin at every session start; nothing to save or paste by hand after an update. The session start also runs migrations on a new version and checks once a day for an update; Claude asks before you install one. There's no one-step undo; a bad release is fixed by the next one.
- A guard enforces protected files (`calibration.csv`, the plugin's own files), the data-host allowlist and the browser limits. It denies web search, and checks the desktop chat's own fetch tool (`mcp__workspace__web_fetch`) against the same data-host list as WebFetch.
- Read-tier browser (its own logged-in profile; needs Node): Claude reads pages on the work sites built into the plugin (OneKey MLS, Zillow, AppraisalPort, Reggora, ValueConnect, DwellingBlocks, Aivre), all on from the start. There's nothing to set up: say "Open Zillow" and log in once in the browser window. Any other site stays closed to the browser. The maintainer adds a site in a release.
- Plainer for the appraiser (desktop check C7). Claude talks to him in plain words without naming the plumbing, says session-start notes once, and starts each skill from everyday phrases ("Open Zillow", "Set up this folder"), listed in the README. Setup is two short messages.
- New setup skill. `score_calibration.py` reads `shared/evals/` when given no file names.
- The app installs the plugin read-only, and a plain copy keeps that. Setup and the new `tools/new_assignment.py` (used by comp-intake) copy file contents only, so the folder's files and each new assignment stay writable. The tests now pass from a read-only install too.
- Maintainer: `release.py` publishes the plugin to the releases repo as a marketplace.
- Docs: one home per topic. `docs/How to Update.md` is gone (Claude gives the install steps; the README's update loop covers the rest), and the network rule in `rules.md` is one bullet.

Rule of Two and known limits:
- The submit check matches the clicked element's text against a word list (Enter, or a click with no text, skips it). The limits are the guard's origin check on every address Claude opens, the separate profile and the denied upload and script tools. The app also asks before every browser action.
- The work-site list limits the addresses Claude asks for, not where a page goes next. A listed site that redirects, or a link Claude clicks, can land on another address and the page loads (desktop check C9: `auth.dwellingblocks.com` redirected to `dwellingblocks.com`). Playwright's `--allowed-origins` is passed too, but its docs say it isn't a security boundary and doesn't affect redirects. The browser stays read-only there, and its pages are data.
- The guard's Bash checks are text matching: it denies `curl`, `wget`, `scp`, `ssh`, `nc` and URLs to other hosts, but not a script that opens its own socket, globs, or symlinks.
- If `python3` is missing, the hooks can't run and the guard fails open; setup's test run catches it.

## 0.5.0 (draft)

Principles touched (`docs/Design Principles.md`): scripts beat narrow tools, arithmetic in scripts, deterministic limits, real checks, tests, one rule in one place. **Re-paste the app project instructions once.**

- New `product/tools/install_update.py`: checks the releases repo for a new version, downloads and verifies it, swaps in `product/`, `CLAUDE.md` and `README.md`, runs migrations, and can undo an install or recover one interrupted mid-swap. `CLAUDE.md` now checks for an update at the start of every session and offers to install, undo or recover.
- `ledger.py`: new `UPDATE_FAILED` error code. `check` now requires exactly the headers `append` requires, so step 9 catches what `append` would reject. `ranking.csv` gains `property_type, floor, lot_sqft, year_built, units, size_basis, listed_sqft, listed_basis`; new error codes `OUTLINE_NOT_MEASURED` and `BRACKET_GAP`; new correction categories `outline` and `sketch`.
- Every residential property type in the five boroughs: `building_type` is `co-op`, `condo`, `single-family` or `2-4 family`, and every stage branches on it. `subject.json` gains `floor`, `lot_sqft`, `units`, `gla_sqft` and `gla_source`.
- `plan_area.py` measures one closed outline, figure (d): pixel corners on a to-scale plan (`--outline`, measured only if the scale check passed) or dimensioned sides from a house sketch (`--sketch`, each floor's walk must close within 0.5 ft; angled or zero-length sides are refused). `--attic` applies the attic rule (more than 50% at 7 ft or more, then only the area at 5 ft or more). Checks: fewer than 4 corners or sides, an outline that crosses itself, an outline smaller than the rooms, unlabeled space over 20%. An outline that fails is **not measured**, and (c) falls back to the labeled total, capped at LOW. Conventions are `labeled | outline`; the guessed unlabeled range (`--unlabeled-low/high`, `labeled+unlabeled`) is gone. The rooms CSV is optional.
- New `comp-pool/scripts/bracket.py`: `propose` takes the top 3 by rank and adds a comp only when it closes a bracket gap; `check` shows the coverage for the grid comps without changing a file. The coverage table marks `bracketed`, `bracketed (unverified)`, `not bracketed`, `can't check` and `mixed basis`. It replaces the fixed 6 to 10 shortlist. Baths outside the half-bath convention and a missing `building_type` stop it.
- `nyc_sales.py sales --type apartment|house` (required). `house` keeps DOF categories 01, 02, 03 and class C3 in category 07, and each row carries `land_sqft`, `gross_sqft`, `residential_units` and `building_class`. DOF gross is labeled as gross building area, never living area. `subject` also keeps PLUTO lot and building frontage and depth.
- `score_calibration.py --close-pct` (default 5) lists runs outside the tolerance but within 5% as "close".
- Skills: comp-intake asks for the property type when PLUTO doesn't settle it and records house lot facts as public record; comp-pool adds the house scoring table, the bracketing step and the MLS paste at the shortlist stop; comp-grid branches its columns by type and re-checks the brackets under the grid; floorplan-measure adds "Reading the outline" and "Reading a house sketch"; comp-review re-reads the outline or sketch blind (within 3%).
- comp-intake measures a house subject's inspection sketch before the subject record is confirmed, when the sketch is in hand, so stage 3 can bracket living area. Without it, size shows `can't check` until the sketch is measured and `bracket.py propose` is re-run.
- `export_learning.py`: the size band uses `gla_sqft` when set (houses), plans measured from an outline or sketch count as measured, and `plan_results.csv` gains `outline_sqft` (figure (d)).
- Conformance fixes: measured sizes are written back into `ranking.csv` before the grid's bracket check (the listed or MLS figure is kept in `listed_sqft` / `listed_basis`, `size_diff_pct` is recomputed, and the house grid gains a "Listed / MLS sq ft" column); a `listing` source counts as the `listed` basis; `bracket.py check` accepts appraiser-added comps with no rank; a house subject's size is `gla_sqft` only; a source text like `sketch (plans/subject_result.md)` counts as its basis, so the table no longer reads mixed basis for one basis; a house plan traced in pixels has basis `plan inside face`, **unverified**; an outline that repeats its first corner is accepted; the grid check confirms ACRIS deeds for houses too.
- `run_tests.py`: 95 offline checks (61 new) for the outline, sketch walk and closure, attic rule, bracketing, house sales filtering, ledger columns, the close band and house exports.

Rule of Two: Allowing `github.com` gives a session that holds sensitive data and untrusted listing text a host it can reach. Downloads are verified, and Claude is told never to send anything. But a network allowlist can't tell a download from an upload. If injected text got Claude to run its own code with someone else's credentials, it could push data to GitHub. **This residual risk is instruction-limited, not deterministic.** Mitigations:
- only the installer script may contact GitHub;
- there are no GitHub credentials on his computer;
- no API host is allowed;
- `INJECTION_SEEN` logging stays as it is.

## 0.4.0 (2026-11-16)

Principles touched (`docs/Design Principles.md`): deterministic limits, real checks, tests, clear inputs, one rule in one place, short instructions. **Re-paste the app project instructions and re-save all six skills.**

- Rule of Two: `CLAUDE.md` now forbids email, chat, CRM and upload connectors and limits network use to Open Data and ACRIS reads. Install step 4 removes those connectors and sets a network allowlist where possible. `Agent Workflow Plan.md` §6 no longer claims the agent can't send. It says what limits that.
- `nyc_sales.py acris --expect-price --expect-date` prints `DEED CHECK: MATCH | DATE_DIFF | MISMATCH | ZERO_DEED | PARTIAL`. The model no longer compares deed amounts by eye.
- `plan_area.py`: optional `px_w,px_d` columns. With 3+ walls the script runs the scale check (more than 5% off fails). Without them it says the check was not run. Fixed a crash on an empty rooms CSV.
- New `tests/run_tests.py`: 34 offline tests of plan_area, nyc_sales, the scorer, ledger and both export modes (leak scan blocks and passes).
- Skills call scripts by their `product/skills/...` path, with no placeholders. Shared path and logging rules moved to `CLAUDE.md`.
- One place per fact: line figures live only in `lines.csv` (the building page holds notes); the app instructions are a pointer plus five backup rules; duplicate rules were removed from the skills; `README.md` maps where each topic lives; `Agent Workflow Plan.md` was cut to rationale and evals; the roadmap moved to `maintainer/`. New `docs/Design Principles.md`.
- comp-export skill description shortened (it loads every session).
- Fixes from a blind review of the docs: the reviewer writes `_result_review.md`, so it can't overwrite the first result, and skips the line library; stage 4 start and done are logged; the comp-grid correction command is complete; the ACRIS check runs before the shortlist; the blocked-export fix path is spelled out; `README.md` ships with updates; the `plan_area.py` help text is corrected.

## 0.3.1 (2026-11-16)

- `shared/lessons.md` is now read at the start of every session (`CLAUDE.md` and `docs/Project Instructions (app).md`). Before this, lessons were written but never loaded. **Re-paste the app project instructions.**
- `docs/Agent Workflow Plan.md` brought in line with `CLAUDE.md`: no appraiser stop at the floor plan stage (the blind review is its check), what subagents may write, ledger and export added, Rule of Two updated for firm-mode exports, rollout steps and folder map updated.
- Retired `docs/Project Instructions (short).md` and `docs/Appraisal Comp Workflow Project Instructions.md` (moved to `_archive/`). `docs/Project Instructions (app).md` is the only file to paste into the app.

## 0.3.0 (2026-11-16)

- Export modes in `settings/export.json`: `firm` (maintainer works inside the firm) and `external` (other appraisers; de-identified as in 0.2.0).
- Firm mode adds comp addresses, BBLs, units, exact sale dates, sale prices, ACRIS links, comps linked to their assignment, the appraiser's adjustments and final value (`adjustments.csv`, `adjusted_comps.csv`).
- Both modes add UAD condition/quality codes (subject and comps) and structured error codes (`errors.csv`) in place of free text.
- `ledger.py`: new `error`, `adjustment` and `value` commands; `ranking.csv` gains `sale_price, source, acris_link, uad_condition, uad_quality`.
- Leak scan: links other than ACRIS are always blocked; dollar amounts are allowed in firm data files only. A comp that is the subject's own prior sale is masked.
- Still never exported: subject address, unit, BBL, exact effective date, inspection notes, log text, folder names, client/lender details, plan images.

## 0.2.0 (2026-11-16)

- Folder split into `product/` (replaced on update), `settings/` (appraiser's), `shared/` (learning data that goes into exports), `assignments/` (never leaves this computer), `exports/`.
- Measurement conventions moved from CLAUDE.md to `settings/conventions.md`, so updates can't overwrite them.
- New `product/tools/ledger.py`: structured stage timing (`events.csv`), line measurements (`shared/buildings/lines.csv`), corrections (`shared/corrections.csv`), comp ranking (`data/ranking.csv`).
- New comp-export skill and `product/tools/export_learning.py`: builds a de-identified learning export for the appraiser to review and approve.

## 0.1.0 (2026-11-15)

- First version: comp-intake, comp-pool, floorplan-measure, comp-review, comp-grid; nyc_sales.py, plan_area.py, calibration scorer.
- Live test (2026-11-15, condo at 421/425 East 13th St): two street addresses treated as one building, 12 same-building sales since 2024, a two-penthouse package sale flagged, DOF price matched the ACRIS deed ($1,692,000).
