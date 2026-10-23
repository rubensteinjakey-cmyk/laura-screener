"""Run the global sleeve values screen (companies_global.csv -> output/global/).

  python run_global.py --country Denmark        one country at a time
  python run_global.py --tickers GMAB,SIKA.SW   or named companies
  python run_global.py --rank                   Pass/Fail + outputs, once all are scored

Steps per company: fetch the 20-F / annual report -> extract sections ->
score (AI) -> verify -> second look (only thin coverage) -> verify again.
Every step saves after each company and skips work already done, so after a
usage-limit stop just run the same command again. The US run is not touched.
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def step(name, *args):
    print(f"\n========== {name} ==========", flush=True)
    r = subprocess.run([sys.executable, str(SRC / name), *args], cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f"Step {name} failed (exit {r.returncode}); finished work is kept.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--country", default="")
    p.add_argument("--tickers", default="")
    p.add_argument("--rank", action="store_true", help="only rank and write the outputs")
    a = p.parse_args()
    if a.rank:
        step("global_rank.py")
        return
    sel = (["--country", a.country] if a.country else []) + \
          (["--tickers", a.tickers] if a.tickers else [])
    if not sel:
        sys.exit("Give --country or --tickers (one country at a time), or --rank.")
    step("global_fetch.py", *sel)
    step("global_extract.py", *sel)
    step("global_score.py", *sel)
    tick = []
    if a.tickers:
        tick = ["--tickers", a.tickers]
    step("global_verify.py", *tick)
    step("global_second_look.py", *tick)
    step("global_verify.py", *tick)
    print("\nDone for this batch. When every country is done: python run_global.py --rank")


if __name__ == "__main__":
    main()
