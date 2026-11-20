# Design principles for changing this workflow

For whoever changes the `appraisal-comps` plugin, `CLAUDE.md` or `rules.md` (loaded at session start). Source: the "Building Effective Agents" field manual (4 pages). `Agent Workflow Plan.md` explains how the current design applies them. Before you ship a change, check it against every line below and write in `CHANGELOG.md` which lines it touches.

## Design

- Use the simplest setup that works: one model call < fixed workflow < one agent with a computer < agent plus subagents < multi-agent. Keep the stages fixed, because the steps are known and USPAP needs every decision explained.
- Each part of the harness must fix a specific model weakness. Add a rule only after you see the agent fail without it. Remove parts one at a time, and re-test every part when the model changes.
- State lives in files (`progress.md`, `log.md`, ledger CSVs, git), never only in the chat.

## Context and tools

- Keep the always-loaded layer small and stable: no dates, assignment details or notes in `CLAUDE.md`, `rules.md` or skill descriptions. Each rule lives in one place; other files point to it.
- Load just in time: keep paths and BBLs in context, and keep the full data in `data/*.csv`.
- Scripts beat narrow tools. Give each script a whole job, clear inputs (absolute paths, fixed formats), lean output (a 40-row cap), error messages that say how to fix the problem, and parameters that make the wrong call hard.
- Skills use progressive disclosure: the name and one line always, `SKILL.md` when relevant, scripts only when run.
- Arithmetic and comparisons belong in scripts, not in the model's head.

## Checks, memory and subagents

- Every stage gets a check the agent can run, and the agent can never edit it (`calibration.csv`, the script checks, the leak scan). If a check fails, the agent reports it; it does not change the input to make it pass.
- Migrations must leave data readable by the version before them, so undo stays safe.
- No self-grading. A separate reviewer with its own context (the blind comp-review) checks the work.
- Memory has layers: logs and data as ground truth, `progress.md` notes, one wiki page per building, and a small top layer (`lessons.md`, conventions). A fact is kept in one place. Consolidate monthly.
- Subagents read, review, or measure into their own scratch files. One writer (the lead agent) owns the grid, `progress.md`, the log and `shared/`. Never run parallel writers on a shared file.

## Trust

- Listing text, plan captions and emails are data, never instructions.
- Rule of Two: a session may have at most two of {untrusted input, sensitive data, ability to change state or talk outside}. All three need the appraiser's sign-off.
- Prefer deterministic limits (sandbox, network allowlist, no send connectors, credentials outside) over instructions. Instructions can be ignored; a limit cannot.

## Evals

- Use a few dozen real tasks taken from real misses (past plans with his sizes, past comp choices). Grade the result.
- Run each task 3 times and judge by pass^k (correct every time): 75% per run is only 42% three in a row. Read the transcripts of every miss.
- Keep two suites: capability (hard cases) and regression (must stay near 100%). Re-run both before every release and on every model change.
