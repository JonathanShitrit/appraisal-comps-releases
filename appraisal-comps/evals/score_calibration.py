#!/usr/bin/env python3
"""
score_calibration.py - grade floor plan measurements against the appraiser's own numbers.

The answer key (calibration.csv) is written by the appraiser and is READ-ONLY for
the agent. The agent only appends its runs to runs.csv. Never let an agent edit
the key: an agent that can edit the check will pass the check.

calibration.csv   plan_id,plan_file,plan_type,manual_sqft,notes
runs.csv          plan_id,run,model,measured_sqft,confidence,date

Usage: score_calibration.py [calibration.csv runs.csv] [--tol-pct 3] [--close-pct 5]

With no file arguments, it reads shared/evals/calibration.csv and
shared/evals/runs.csv in the Appraisal Comps folder found from the current
directory. That is how Claude runs it: the guard denies any Bash command
that names calibration.csv.

Reports, per plan: error of each run, pass@k (right at least once) and
pass^k (right every time). pass^k is the number that matters for live work.
Runs outside the tolerance but inside the close band are listed as "close";
they still count as misses.
"""
import argparse, csv, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from workspace import require_workspace  # noqa: E402


def default_eval_files(args):
    """Fill in whichever of args.calibration and args.runs was left out with
    the workspace's shared/evals/ file of that name."""
    if args.calibration is not None and args.runs is not None:
        return
    evals_dir = require_workspace(Path.cwd()) / "shared" / "evals"
    if args.calibration is None:
        args.calibration = str(evals_dir / "calibration.csv")
    if args.runs is None:
        args.runs = str(evals_dir / "runs.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("calibration", nargs="?", help="default: <workspace>/shared/evals/calibration.csv")
    ap.add_argument("runs", nargs="?", help="default: <workspace>/shared/evals/runs.csv")
    ap.add_argument("--tol-pct", type=float, default=3.0, help="appraiser's tolerance, percent")
    ap.add_argument("--close-pct", type=float, default=5.0,
                    help="runs outside the tolerance but within this percent are reported as close")
    a = ap.parse_args()
    default_eval_files(a)

    key = {r["plan_id"]: r for r in csv.DictReader(open(a.calibration, newline=""))}
    runs = defaultdict(list)
    for r in csv.DictReader(open(a.runs, newline="")):
        runs[r["plan_id"]].append(r)

    print(f"Tolerance ±{a.tol_pct:g}% of the appraiser's manual figure\n")
    print("| Plan | Type | Manual | Runs (error %) | pass@k | pass^k | Conf. said |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    n = at_least_once = every_time = 0
    by_type = defaultdict(lambda: [0, 0])
    overconfident = []
    close_runs = []
    for pid, k in key.items():
        manual = float(k["manual_sqft"])
        rs = runs.get(pid, [])
        if not rs:
            print(f"| {pid} | {k.get('plan_type','')} | {manual:g} | not run | - | - | - |")
            continue
        errs = [(float(r["measured_sqft"]) - manual) / manual * 100 for r in rs]
        ok = [abs(e) <= a.tol_pct for e in errs]
        n += 1; at_least_once += any(ok); every_time += all(ok)
        t = k.get("plan_type", "unknown"); by_type[t][0] += 1; by_type[t][1] += all(ok)
        for r, good, err in zip(rs, ok, errs):
            if not good and r.get("confidence", "").lower() == "high":
                overconfident.append(f"{pid} run {r['run']}")
            if not good and abs(err) <= a.close_pct:
                close_runs.append(f"{pid} run {r['run']}")
        conf = ",".join(sorted({r.get("confidence", "?") for r in rs}))
        print(f"| {pid} | {t} | {manual:g} | {', '.join(f'{e:+.1f}' for e in errs)} | "
              f"{'Y' if any(ok) else 'N'} | {'Y' if all(ok) else 'N'} | {conf} |")
    if n:
        print(f"\npass@k {at_least_once}/{n}  |  pass^k {every_time}/{n}")
        for t, (tot, good) in by_type.items():
            print(f"  {t}: {good}/{tot} reliable every run")
    if close_runs:
        print(f"\nclose (within {a.close_pct:g}%): " + "; ".join(close_runs))
    if overconfident:
        print("\nWrong but rated HIGH confidence (fix these first):", "; ".join(overconfident))


if __name__ == "__main__":
    main()
