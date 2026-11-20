# Appraisal Comps: why the workflow is built this way

Design rationale for 0.7.0. The general checklist is `Design Principles.md`. The rules the agent follows are in `rules.md` (loaded by the SessionStart hook), and the stage steps are in the skills. If this file disagrees with either, they win. Scope: what runs today in the Claude desktop app (shell, files, skills, subagents, NYC Open Data). No custom harness.

## 1. Design in one paragraph

One agent with a computer, held inside a fixed six-stage workflow (rungs 2 and 3), with subagents for reading only (rung 4). The steps are known, and USPAP needs every decision explained, so the stages stay fixed. The model only chooses within a stage (which comps to exclude, how to read a plan). The appraiser signs off at intake, the shortlist, the grid and close-out. The floor plan stage has no stop of its own: the blind reviewer checks it, and the appraiser sees every figure, flag and confidence rating in the grid. Subagents measure one plan each or review blind. Each writes only its own scratch files in `plans/` (`<unit>_rooms*.csv`, `<unit>_outline*.csv`, `<unit>_sketch*.csv`, `<unit>_attic*.csv`, `<unit>_result*.md`). The lead agent is the only writer of everything else. A multi-agent setup (rung 5) is ruled out: about 15x the tokens, and comp work is tightly linked.

## 2. Each part covers a specific weakness

| Part | Weakness it covers |
| --- | --- |
| `nyc_sales.py` (PLUTO + DOF + ACRIS in one call; `DEED CHECK` against DOF) | Wrong joins and BBL formats; raw rows flooding context; comparing deed amounts by eye |
| `plan_area.py` (area math, checks, scale check, outline and sketch walk, attic rule, confidence ceiling) | Arithmetic slips, double counts, missed notches, misread inches, plans not to scale, closets and hall jogs left out, sketch sides that don't close |
| `bracket.py` (proposes comps by bracketing, coverage table) | Picking comps that all sit on one side of the subject; a fixed shortlist size; unknown values quietly counted |
| Blind reviewer subagent | Kind self-grading |
| Appraiser checkpoints | Premature victory; USPAP responsibility |
| `ledger.py` | Notes that drift; no real timing; free text that leaks client details |
| `export_learning.py` leak scan + approval | Data leaving without review |
| `progress.md` + append-only `log.md` | Losing the thread between sessions; repeating failed steps |
| `lines.csv` + one notes page per building | Re-measuring the same line every assignment |
| `${CLAUDE_PLUGIN_ROOT}/tests/run_tests.py` | A fix to one script silently breaking another |
| Calibration set + scorer | Not knowing whether plan reading is good enough |
| SessionStart hook (rules, migrations, update check) | Rules that drift out of date in the chat; an update he never hears is ready |
| PreToolUse guard | A protected-file or network rule that's only probabilistic; an injected listing talking the model past it |
| Read-tier browser (Playwright MCP, dedicated profile) | Needing his logged-in sessions on the work sites without handing Claude his personal browser or credentials |

Re-test every part when the model changes, and remove one part at a time. On clean, fully dimensioned plans a stronger model may not need the blind reviewer, but only the calibration set can show that.

## 3. Context

What's always loaded is the top layer in §5. Each stage's detail is in its skill. Scripts run without their code entering context. Each rule has one home. The sales pull goes to `data/*.csv`, and only a 40-row summary returns. `progress.md` restates the goal at every stage. No dates or assignment facts go into instructions, so the prefix stays cacheable.

## 4. Checks the agent can run but not edit

| Stage | Check | Run by |
| --- | --- | --- |
| Pool | `DEED CHECK`: ACRIS deed vs DOF price and date (condos and houses); $0 and partial transfers flagged | `nyc_sales.py acris --expect-*` |
| Pool | Every checkable feature bracketed (at least 3 comps); gaps, mixed size bases and public-record sizes named; baths outside the half-bath convention refused | `bracket.py propose` |
| Floor plan | Inches of 12 or more, duplicate rooms, notch wider than the room, overlaps, listed off by more than 10%, feet per pixel off by more than 5% on 3+ walls | `plan_area.py` (the model supplies the pixel readings) |
| Floor plan | Outline: at least 4 corners, doesn't cross itself, measured only if the scale check passed, not smaller than the rooms, unlabeled space over 20% flagged | `plan_area.py --outline` |
| Floor plan | Sketch: each floor's walk closes within 0.5 ft, no angled or zero-length side, attic counted only when more than 50% is at least 7 ft | `plan_area.py --sketch --attic` |
| Floor plan | Blind re-read within 2% and the same on every digit; the two outline figures within 3% | Reviewer subagent |
| Grid | Bracketing re-checked on the grid comps after measuring | `bracket.py check` |
| Grid | Prices match the source; $/sq ft recomputed; no value without a source | Reviewer subagent |
| Release | Every script against fixed inputs, both export modes | `${CLAUDE_PLUGIN_ROOT}/tests/run_tests.py` |

The agent never edits `calibration.csv`, `${CLAUDE_PLUGIN_ROOT}/tests/`, a script check or the leak scan. A failed check is reported, never worked around.

## 5. Memory layers

| Layer | Files |
| --- | --- |
| Top, loaded every session (keep small) | `rules.md` (via the SessionStart hook), `CLAUDE.md`, `conventions.md`, `lessons.md` |
| Wiki, one entity per page | `shared/buildings/<billing-BBL>.md` (notes) and its rows in `lines.csv` (figures; the only copy) |
| Notes the agent writes | `progress.md` |
| Ground truth | `log.md`, `data/*.csv`, plan images, ledger CSVs |

Once a month, run the consolidate-memory skill over `lessons.md` and the building pages.

## 6. Rule of Two

| Property | In this workflow |
| --- | --- |
| Untrusted input | Present: listing text, captions, pasted emails, and page text the browser reads on the work sites. Treated as data |
| Sensitive data | Present: his adjustments and value, and in firm mode comps linked to assignments. Client and lender details are kept out |
| Changes state / talks outside | Must be absent, beyond what the PreToolUse guard enforces: writes stay in the folder, and network use is the read-only list in `rules.md` (Rules) |

Each session already has two of the three properties, so the third must be removed by a limit, not just a rule. A rule in `rules.md` is probabilistic: an injected listing can talk the model past it. So the setup call (Maintainer Checklist step 4) removes the means: no email, chat, CRM or upload connectors in his Claude project, and a network allowlist (`data.cityofnewyork.us`, `a836-acris.nyc.gov`) if the deployment's admin settings offer one. The PreToolUse guard is that limit for the tools it sees: it denies WebFetch and Bash to any host but the two data hosts, and denies WebSearch outright. What the guard can't see is listed under the current release's known limits in `CHANGELOG.md`; treat those as open risks. The export has its own human gate: leak scan, his approval, firm storage or encrypted transfer. Credentials stay with him: he logs in himself, in the browser window, on the profile it keeps; Claude never types a password.

## 7. Evals

1. **Plans.** He supplies 20 to 30 past plans with his own square footage (the Appraisal Comps folder's `shared/evals/plans/`, `calibration.csv` with a plan type). Run each 3 times in fresh sessions and log each run in `runs.csv`.
2. **Score.** `python3 -I ${CLAUDE_PLUGIN_ROOT}/evals/score_calibration.py --tol-pct <his tolerance>`, run from an Appraisal Comps folder, his or your dev workspace (it reads `shared/evals/calibration.csv` and `runs.csv`; the guard denies any command that names `calibration.csv`). Judge on pass^k: 75% per run is 42% three in a row. Read the transcript of every miss, starting with misses rated HIGH confidence.
3. **Decide by plan type.** Go live only on the plan types that pass, e.g. "fully dimensioned OK; schematic and combined units measured by hand."
4. **Comp pool.** For 5 past assignments, check that his chosen comps land in the ranking's top 10 and that its exclusions were right.
5. **Two suites.** Capability (hard cases that fail now) and regression (must stay near 100%). Re-run both, plus `run_tests.py`, before every release and on every model change.

Status at 0.4.0 through 0.7.0: script tests pass (`run_tests.py`). The calibration set is empty. Nothing has been measured on the current model yet. No live work until it has.
