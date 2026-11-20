# Appraisal Comps: why the workflow is built this way

Design rationale for the current version (`.claude-plugin/plugin.json`). The general checklist is `Design Principles.md`. This file says *why* each part exists; *how* it works, and every limit and threshold, live in the part itself (the script, its skill, `rules.md`). If this file disagrees with them, they win. Scope: what runs today in the Claude desktop app (shell, files, skills, subagents, NYC Open Data). No custom harness.

## 1. Design in one paragraph

One agent with a computer, held inside a fixed six-stage workflow (rungs 2 and 3), with subagents for reading only (rung 4). The steps are known, and USPAP needs every decision explained, so the stages stay fixed. The model only chooses within a stage (which comps to exclude, how to read a plan). The appraiser signs off at intake, the shortlist, the grid and close-out. The floor plan stage has no stop of its own: the blind reviewer checks it, and the appraiser sees every figure, flag and confidence rating in the grid. Subagents measure one plan each or review blind. Each writes only its own scratch files in `plans/` (`<unit>_rooms*.csv`, `<unit>_outline*.csv`, `<unit>_sketch*.csv`, `<unit>_attic*.csv`, `<unit>_result*.md`). The lead agent is the only writer of everything else. A multi-agent setup (rung 5) is ruled out: about 15x the tokens, and comp work is tightly linked.

## 2. Each part covers a specific weakness

| Part | Weakness it covers |
| --- | --- |
| `nyc_sales.py` | Wrong joins and BBL formats; raw rows flooding context; comparing deed amounts by eye |
| `plan_area.py` | Arithmetic slips, double counts, misread digits, plans not to scale, sketch sides that don't close |
| `bracket.py` | Comps that all sit on one side of the subject; a fixed shortlist size; unknown values quietly counted |
| Blind reviewer subagent (comp-review) | Kind self-grading |
| Appraiser checkpoints | Premature victory; USPAP responsibility |
| `ledger.py` | Notes that drift; no real timing; free text that breaks the CSVs |
| `export_learning.py` manifest + approval | Data leaving without review; summary tables rebuilt by hand differently each week |
| `progress.md` + append-only `log.md` | Losing the thread between sessions; repeating failed steps |
| `lines.csv` + one notes page per building | Re-measuring the same line every assignment |
| `tests/run_tests.py` | A fix to one script silently breaking another |
| Calibration set + scorer | Not knowing whether plan reading is good enough |
| SessionStart hook | Rules that drift out of date in the chat; an update he never hears is ready |
| PreToolUse guard | A protected-file or network rule that's only probabilistic; an injected listing talking the model past it |
| Read-tier browser | Needing his logged-in sessions on the work sites, in his own Chrome so the sites don't flag a separate automated browser as a bot, without Claude typing his credentials |

Re-test every part when the model changes, and remove one part at a time. On clean, fully dimensioned plans a stronger model may not need the blind reviewer, but only the calibration set can show that.

## 3. Context

What's always loaded is the top layer in §5. Each stage's detail is in its skill. Scripts run without their code entering context. Each rule has one home. The sales pull goes to `data/*.csv`, and only a 40-row summary returns. `progress.md` restates the goal at every stage. No dates or assignment facts go into instructions, so the prefix stays cacheable.

## 4. Checks the agent can run but not edit

Every stage has one. The limits are in the script (its constants and `--help`) or the skill that runs it; don't copy them here.

| Stage | Check | Where the limits live |
| --- | --- | --- |
| Pool | Deed vs DOF price and date | `nyc_sales.py acris` |
| Pool | Every checkable feature bracketed | `bracket.py propose` |
| Floor plan | Room, outline, sketch and attic checks; confidence ceiling | `plan_area.py` |
| Floor plan | Blind re-read agrees with the first reading | comp-review skill |
| Grid | Bracketing re-checked on the grid comps; prices and $/sq ft against the source | `bracket.py check`, comp-review skill |
| Release | Every script against fixed inputs | `tests/run_tests.py` |

The agent never edits `calibration.csv`, the tests or a script check (`rules.md`, Rules). A failed check is reported, never worked around.

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
| Untrusted input | Present: listing text, captions, pasted emails, and pages the browser reads. Treated as data |
| Sensitive data | Present: the subject, his adjustments, value, notes and plan images |
| Changes state / talks outside | Must be absent: writes stay in the folder, network use is read-only (`rules.md`, Rules) |

Each session already has two of the three, so the third is removed by limits, not rules: a rule can be talked past by an injected listing, a limit can't. The limits are the PreToolUse guard (`hooks/guard.py`), no send connectors in his project and a network allowlist if the app's admin settings offer one (Maintainer Checklist step 4), his own browser login (Claude never types a password), and his manual approval and sending of the export. What the guard can't see is in the current release's known limits in `CHANGELOG.md`; treat those as open risks.

## 7. Evals

1. **Plans.** He supplies 20 to 30 past plans with his own square footage (the Appraisal Comps folder's `shared/evals/plans/`, `calibration.csv` with a plan type). Run each 3 times in fresh sessions and log each run in `runs.csv`.
2. **Score.** `python3 -I ${CLAUDE_PLUGIN_ROOT}/evals/score_calibration.py --tol-pct <his tolerance>`, run from an Appraisal Comps folder, his or your dev workspace. Judge on pass^k: 75% per run is 42% three in a row. Read the transcript of every miss, starting with misses rated HIGH confidence.
3. **Decide by plan type.** Go live only on the plan types that pass, e.g. "fully dimensioned OK; schematic and combined units measured by hand."
4. **Comp pool.** For 5 past assignments, check that his chosen comps land in the ranking's top 10 and that its exclusions were right.
5. **Two suites.** Capability (hard cases that fail now) and regression (must stay near 100%). Re-run both, plus `run_tests.py`, before every release and on every model change.

Status: the calibration set is empty, and nothing has been measured on the current model yet. No live work until it has.
