"""Run every step, in order, for your batch.

  python run_all.py --start 1 --end 66

Steps: download -> extract -> score -> verify -> second look (thin filings
only) -> verify again -> merge -> rank. Every step skips work that's already
done, so if it stops (usage limit, closed laptop...) just run the same
command again.
"""

import argparse
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def step(name, *args):
    print(f"\n========== {name} ==========", flush=True)
    r = subprocess.run([sys.executable, str(SRC / name), *args], cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"Step {name} failed (exit {r.returncode}). Fix the problem above "
                 f"and run the same command again; finished work is kept.")


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", type=int, default=cfg["batch"]["start"])
    p.add_argument("--end", type=int, default=cfg["batch"]["end"])
    p.add_argument("--run-id", default=cfg["run_id"])
    p.add_argument("--skip-second-look", action="store_true")
    a = p.parse_args()
    batch = ["--start", str(a.start), "--end", str(a.end)]
    run = ["--run-id", a.run_id]

    step("download.py", *batch)
    step("extract.py", *batch)
    step("score.py", *batch, *run)
    step("verify.py", *run)
    if not a.skip_second_look:
        step("second_look.py", *run)
        step("verify.py", *run)
    step("merge.py")
    step("rank.py")
    print("\nDone. See output/results.csv, output/summary.md, output/needs_human.csv")


if __name__ == "__main__":
    main()
