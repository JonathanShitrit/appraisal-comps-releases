# Changelog

Release steps: `maintainer/Maintainer Runbook.md`, section 3.5.

## 0.11.0 (2026-11-25)

Principles touched (`docs/Design Principles.md`): simplest setup that works (a script call, not a subagent driving a browser); each part fixes a seen weakness (blocks in his Chrome, Q17 and the spike; page snapshots filling Claude's context); state lives in files (`data/cloud/` and `runs.csv`); scripts beat narrow tools (one `cloud_read.py` call per read); every stage has a check the agent can't edit (`quote_check.py` checks each value's quote against a page saved by code; `ledger.py check` on `runs.csv`); pages are data; open network with deterministic limits (a fixed task text, local-only hosts, query patterns, cost caps). Stage 2 (logged-in sites): each part fixes a seen weakness, and parts are removed one at a time (the shim and humanizer didn't clear Zillow's check and are removed; the element list exists because a coordinate click has no name); prefer deterministic limits (page scripts matched by hash, deny by omission, telemetry off by environment and by the guard); one rule in one place (the pin in `launch.py`; the scripts in `browser/scripts/`).

What changed:
- New `tools/cloud_read.py` with four commands. `read` reads one public listing or county page in browser-use Cloud and prints one line plus the values it accepted. `run` does up to five requests from request files in parallel. `show` lists the last reads. `check-key` tests the key and prints the credit left. Claude no longer drives a browser for public sites.
- Code saves the page the run ended on (address, text and PDF) over CDP, and `quote_check.py` accepts a value only when its quote is found in that saved text and the value is in the quote. The reader's own words are never evidence.
- The source list has a `reader` column: `cloud` or `chrome`. The shipped defaults mark OneKey MLS, Reggora, AppraisalPort, DwellingBlocks, ValueConnect and Aivre as `chrome` (local-only hosts: they can never be read in the cloud); everything else is `cloud`. `sources.py add --reader` sets it.
- What is saved in his assignment's `data/cloud/`: for listings, each read's record (`c-NNN-<site>.json`), page text and PDF; for property cards and tax maps, the record only (the page is deleted after validation, quotes blanked); `runs.csv` (one row per cloud run, validated by `ledger.py check`).
- The key lives in `settings/browser-use.key` (one line), added by the maintainer on the setup call. Setup's new step 5 runs `check-key`. The guard denies Edit and Write on that file name and any Bash command that names it.
- Three ledger codes: `CLOUD_BLOCKED`, `CLOUD_FAILED`, `CLOUD_BUDGET`.
- The learning export keeps `data/cloud/runs.csv` only; the pages, records and `open.json` stay in his folder.
- New skills `zillow` and `streeteasy` (outlines, written for the cloud reader; revised after watched runs). `browser-read` gains "Public sites"; `rules.md`, his README, the Design Principles and the Agent Workflow Plan name the cloud read.
- Only a property's address, public record numbers and the fixed rules are sent: the query must be an address, a BBL, an MLS number or a parcel id; `--match` takes only a sale date, apartment, MLS number or town; `--url` takes only a listed host. Runs are capped ($0.50 each, 300 s, 5 at a time).

- Stage 2: the browser server is Browser Harness 0.1.14 (`uvx`, pinned in `launch.py`) in his own Chrome, over CDP, in a tab of its own. One-time setup is `uv` and "Allow remote debugging" at `chrome://inspect/#remote-debugging`; Chrome asks him to click Allow once per session, and the Playwright Extension can be removed. Node is no longer needed.
- Pages are read with `browser_js` running `browser/scripts/read.js`, and what can be clicked is listed with `elements.js`. The guard allows `browser_js` only for those two scripts, matched by SHA-256 of their text with whitespace collapsed.
- The guard's read tier is Harness's tools: `new_tab`, `goto`, `page_info`, `click`, `type`, `fill`, `press`, `scroll`, `screenshot`, `current_tab`, `close_tab`, `ensure_real_tab`, `wait`, `wait_for_load`, `wait_for_element` and `js`. Everything else (raw CDP, `http_get`, tab listing and switching, uploads, the CAPTCHA solver, recordings, vendor feedback) is denied by omission, and a tool the pinned server lists that the guard doesn't know is denied too. `goto` and `new_tab` open only `http` and `https` addresses.
- A new PostToolUse hook, `hooks/post_tool.py`, saves each element list (names, selectors and boxes, never values) and the page's origin to `$CLAUDE_PLUGIN_DATA`. A click by coordinates is denied without a fresh list (under 120 seconds old, and no acting call since), asks when it lands on an element named like "Send", and is allowed otherwise. A fill asks the same way by selector. `email` and `share` join the ask words.
- The tool list the guard relies on is checked against `product/tests/fixtures/browser/harness-tools.json`, which `maintainer/dump_mcp_tools.py` makes from the pinned server.
- Install guide, setup step 4, his README and the checklist name `uv` and "Allow remote debugging" in place of Node and the extension.

Removed:
- `browser/runtime-shim.cjs`, `browser/humanize.cjs`, `browser/humanize-plan.cjs`, the vendored `humanjs-core` and the node tests; Node as a requirement; Playwright MCP and its extension.
- The guard's rule on browser file names, R14 (ask on `browser_type` with `submit`) and `browser_mouse_wheel` (Harness has no such tools).

Breaking:
- His `shared/sources.csv` gains a column the next time `sources.py add` writes it. The version before this one can't read the new file.

Known limits:
- browser-use sees every public page read, owner names on county pages included, and holds a record that this account looked up each address. Deleting a session there hides it but does not destroy the run's data; only an enterprise purge does.
- Zillow's and StreetEasy's terms forbid automated access. Using the service on them is the appraiser's choice (question H2).
- The key sits in a file. Hooks don't match the Read tool yet, so Claude is not stopped from opening it.
- A site that blocks the service is passed, not opened in his Chrome.
- Measured on 76 reads with `bu-4.0-mini` (spec, Results, "Stage 1"): Zillow 6% blocked, the NYC Property Information Portal 0%, and a page saved on every read. StreetEasy shows a login wall on 13% of reads and finds no page on half; it misses the bar and its default row is still open (H3). Westchester County GIS (an interactive ArcGIS map) timed out at 300 s on both runs and is not usable by the cloud reader yet. The quote check accepted 90% of Zillow's fields, 88% of the portal's and 56% of StreetEasy's against a 95% bar; most rejections are dates not in `YYYY-MM-DD`, a unit on `year_built`, and quotes not found in the page. The `bu-4.0` comparison, the PDF comparison and the desktop-app check (V22) are not done.
- The ledger's `cost_usd` is the agent's cost as the API reports it and leaves out browser and proxy charges. The account's credit fell about 3.3 times as much as the recorded sum; treat `cost_usd` as a lower bound.
- `check-key` can't show the key's monthly cap; the billing endpoint has no cap field. A 402 shows the cap and the amount spent.
- The reader's agent is a third party's model: the saved page comes from code, the reading does not. The quote check catches invented values, not a page that lies.

- Stage 2: remote debugging stays ticked in his everyday Chrome, so any program on his Mac can ask to connect; he must refuse a prompt he didn't expect.
- The ask on a click rests on a list the page itself supplies, so a hostile page can label a send button "Next", and a clickable `div` isn't listed at all. Each click needs its own `elements.js` call.
- The token saving over 0.10.0 was measured in two pairs of runs, not the three runs per page the plan asks for: Harness used 22% and 41% less input in total (cache reads included), and 18% and 2% less new input. A heavier site, such as an MLS results page, could differ. A read finished with Chrome behind other windows or minimized. No human check showed on AppraisalPort or the two Zillow pages; none was tried on an MLS. The click prompt on a button named like "Send" was not seen live, only in the unit tests. The page scripts were checked by hand on AppraisalPort (M3); the Node test runs them against a fake page only.
- If the desktop app gives hooks no PostToolUse, every click is denied (spec H5). Checked in the desktop app (V20): it does fire.
- Menu items that are not links or buttons (AppraisalPort's Orders, Announcements and notification entries) are not in the element list, so Claude can't click them. It can still open their address with `browser_goto` when it knows it; a subagent that didn't know it stopped. Where a site needs this, its site skill should give the address. Icon-only links have an empty name, so the ask words can't match them.
- `browser_screenshot` saves its image in Harness's own folder (`~/.config/browser-harness/tmp/`), which the desktop app won't let Claude read. Screenshots don't work there until the skill passes a path inside the workspace.
- A subagent opens a tab of its own, so a job that uses subagents can have more than one of Claude's tabs open (V21). They share the one Chrome connection and the one Allow prompt.

Cost: the ledger records about $0.02 a read (median $0.016 on Zillow and the portal, $0.020 on StreetEasy); the account's credit fell about 3.3 times as much, which is about $0.07 a read, or $1.40 to $1.75 for an assignment of 20 to 25 reads. The 3.3 is an upper bound from a mixed session, not one batch measured alone, so the estimate of $0.05 to $0.10 a read, $1 to $2.50 an assignment, still stands until it is.

## 0.10.0 (2026-11-20)

Principles touched (`docs/Design Principles.md`): Trust (the Rule of Two is removed; deterministic limits now cover protected files and the read-tier browser only; the read tier gains one tool that only scrolls, and the coordinate tools that click or drag at a spot stay denied). The appraiser wants Claude to find property data sources in any county. Design, rules come from real cases (the browser spike met a reCAPTCHA that the human-check phrases didn't name; Zillow showed Press & Hold to the stock browser in his Chrome, and stalled clicks in a background window); each part fixes a specific weakness (the shim hides one bot signal, the humanizer another).

What changed:
- No network limits. Claude may search the web, fetch any page, run network commands in Bash, and open any site in his Chrome. The guard no longer checks web addresses, and web search is no longer denied.
- The built-in work-site list (`browser/sites.py`) is gone. The work sites are still named in the browser skill, as the sites he's logged in to.
- Still enforced: the protected files (`calibration.csv`, the plugin's own files), read-tier browser tools only (no uploads, page scripts, drag or drop), plain browser file names, the ask on submit-like clicks, and failing closed. Claude still never types a password, and he still approves and sends the export himself.
- His folder's README says Claude can open other sites; an existing folder keeps its old copy.
- A source list, in two layers: the defaults shipped in the plugin (`product/sources/defaults.csv`) and his own list (`shared/sources.csv`). `tools/sources.py lookup --job <job> --area <area>` gives Claude the sites to try first, in order; `sources.py add` adds a site to his list, only after he says yes. His list goes out in the weekly export.
- Claude can scroll pages with the mouse wheel. The browser now starts with `--caps vision`, which adds Playwright's mouse tools, and the guard allows only `browser_mouse_wheel`, since scrolling only looks at the page. The other mouse tools (`browser_mouse_move_xy`, `browser_mouse_click_xy`, `browser_mouse_drag_xy`, `browser_mouse_down`, `browser_mouse_up`) click or drag at a spot, so they stay denied. A test fails if the launcher and the guard ever disagree about the wheel.
- The browser no longer saves a copy of each page it opens (`--snapshot-mode none`). Before, every page load and click wrote the page's text, client details included, to a `.playwright-mcp/` folder. Claude reads the page with `browser_find` or `browser_snapshot`, which work as before. Only the page's console errors reach Claude now (`--console-level error`).
- The browser hides two things bot checks look for: CDP's `Runtime.enable`, and machine-like input. In his Chrome, Zillow showed "Press & Hold" to the stock browser. Two modules from the humanized-browser spike (its results file was deleted later; it is in git history) now load with every browser start:
  - `browser/runtime-shim.cjs` (loaded through `NODE_OPTIONS`): Playwright never sends `Runtime.enable`, a well-known bot signal; the shim tells Playwright about each page's scripts itself. It also keeps each tab drawing while his Chrome window is behind other windows or minimized. Before, every click timed out after 5 s there.
  - `browser/humanize.cjs` (`--init-page`): clicks and hovers follow a curved mouse path and take 0.5 to 0.9 s, typing has a human rhythm, wheel scrolls come in many small steps, and each page load is followed by a 1 to 1.6 s pause. It uses `@humanjs/core` 0.8.0, copied into `browser/vendor/humanjs-core/` (MIT).
  - Each fails open: if one can't load, the browser runs stock and its log says `runtime-shim: disabled` or `humanize: disabled`. `APPRAISAL_COMPS_NO_RUNTIME_SHIM=1` and `APPRAISAL_COMPS_NO_HUMANIZE=1` turn them off.
  - Not humanized: going back, choosing from a list, single key presses, checkboxes in a form, and the Enter that submits typed text.
- Fewer, smaller browser calls. browser-read checks each page for a human check with one `browser_find`, not one per phrase or a full snapshot, and reads a big page's facts with `browser_find` before reaching for a full snapshot. It makes that human-check call on its own and waits for the result before any other browser call on the page: in a test, a scroll sent alongside it ran while Press & Hold was up.
- A checkbox named "I'm not a robot" (Google's reCAPTCHA) is now a human check too: Claude stops and asks him to clear it, as for "Press & Hold". The word "reCAPTCHA" alone, as in a site's footer, isn't one. His folder's `README.md` names it among the examples (new folders only).

Rule of Two and known limits:
- Every session now holds untrusted input, sensitive data and a way to talk outside. An injected page or listing could get Claude to send assignment data out inside a URL, by search, fetch, Bash or the browser. Only the rule that pages are data stands in the way.
- Nothing limits which sites Claude reads, so it can read sites whose terms forbid automated access; which sites to use stays the appraiser's call.

## 0.9.1 (2026-11-19)

Principles touched (`docs/Design Principles.md`): one rule in one place (the install steps live only in the install guide; his README points to it).

What changed:
- The update steps Claude gives him use the desktop app's current menu: Customize, then Plugins, then Discover, then Personal.
- His folder's `README.md` is rewritten for everyday use: what's in the folder, where Claude stops for him, the work sites in his own Chrome (connect page, logins, tab group, human checks), updates, and what to do when something goes wrong. Setup copies it only into a new folder; an existing folder keeps its old copy.
- The install steps moved to an install guide on the releases repo's page, so he can read them before setup.

## 0.9.0 (2026-11-19)

Principles touched (`docs/Design Principles.md`): deterministic limits, Rule of Two, each part fixes a seen weakness (the work sites flagged the plugin's own browser as a bot, and Zillow's human check couldn't be cleared in it).

What changed:
- Claude now reads the work sites in his own Chrome, where he's already logged in, instead of a separate browser the plugin opened. Sites see his everyday browser, not a separate automated one.
- One-time install: the Playwright Extension in his Chrome, in the profile where he's logged in to the work sites (https://chromewebstore.google.com/detail/mmlmfjhmonkocbjadbfplnigmagldckm). Setup asks for it. **If you're updating from 0.7.0, install the extension first;** without it the first browser call waits on a connect page that never connects.
- Each session, the first time Claude opens a site, Chrome shows a connect page; he approves it once. Claude works in its own tab group and says so.
- When a site shows a human check ("Press & Hold", "verify you are human"), Claude stops and asks him to clear it in Chrome himself, then carries on. Claude never clicks or presses keys on it. New ledger code `BROWSER_CHALLENGE`.
- Needs Google Chrome and Node. The old plugin browser folder is left alone; nothing uses it now.

Rule of Two and known limits:
- Which tabs Claude can reach rests on the extension (its tab group), not on the guard. Desktop check 2 records what it shows.
- `--allowed-origins` is gone, so the guard's check on the addresses Claude opens is the only address limit.
- Redirects and links Claude clicks are still not limited: a listed site can land on another address and the page loads (desktop check C9). The browser stays read-only there, and its pages are data.
- The 0.7.0 submit-check limits no longer include a separate profile: the browser is his own Chrome, with his logins. The guard's origin check and the denied upload and script tools remain.

## 0.8.0 (2026-11-19)

Principles touched (`docs/Design Principles.md`): real checks (the leak scan leaves the never-edit list), one rule in one place (the export's data dictionary is generated from its table definitions), simplest setup that works (one export, no modes).

What changed:
- The learning export carries everything from closed assignments. The zip `exports/<date>_learning_<name>.zip` holds `README.md` (every table and column, how they join), `MANIFEST.md`, `tables/` (summary tables with every column: the subject's address, unit and BBLs, exact dates and sizes, comp prices, his adjustments and value, inspection notes) and `raw/` (each closed assignment folder copied whole, plan images included, all of `shared/`, and `settings/conventions.md`). Open assignments stay out of `tables/` and `raw/assignments/` (the manifest counts them); `raw/shared/` is `shared/` whole.
- Gone: the `init` and `mode` commands, `--drop` and `--no-selection`, the modes, the salted codes, masking, month-only dates, size and distance bands and the leak scan. He still reviews the manifest and approves, and sends the zip to the maintainer himself.
- His name is saved at setup: `setup_workspace.py --into <folder> --name dcohen` (lowercase letters and digits, hyphens between). Setup asks one question. The export checks the saved name again with the same rule (`tools/export_name.py`), or a 0.7.0 code like `A-001`, and refuses anything else.
- A 0.7.0 `settings/export.json` (`contributor`, `mode`, `salt`) keeps working: the export goes under the contributor code, and `mode` and `salt` are ignored. No migration.
- `ledger.py` accepts any note. Notes are still one line, up to 200 characters.
- Docs: one home per fact (one rule in one place). `docs/Agent Workflow Plan.md` keeps only why each part exists and points to the script or skill for every limit; the comp-export skill reads the export's contents from its generated `README.md`; the setup steps live only in the setup skill. The current version is stated only in `plugin.json`.

Rule of Two and known limits:
- The export now holds the subject's identity, inspection notes, logs and plan images. The only gate is his review and approval, and he sends the zip himself; how it travels to the maintainer is out of scope.

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
