"""Compare scores for the same companies across two runs: two teammates, or
the same person scoring twice. No AI usage.

PASS for a company = final scores within 5 points AND no theme more than
1 point apart.

Run:  python src/overlap_check.py --a jakey --b maya
      python src/overlap_check.py --a v1 --b repeat1

--a / --b are run_ids (the name at the end of data/verified/{TICKER}_{run_id}.json).
Writes output/overlap_{a}_vs_{b}.csv
"""

import argparse
import csv

from ai import THEMES
from common import OUTPUT, VERIFIED, read_json

MAX_FINAL_DIFF = 5
MAX_THEME_DIFF = 1


def load(run_id):
    out = {}
    for f in VERIFIED.glob(f"*_{run_id}.json"):
        r = read_json(f)
        if r["run_id"] == run_id:
            out[r["ticker"]] = r
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a", required=True, help="first run_id")
    p.add_argument("--b", required=True, help="second run_id")
    args = p.parse_args()
    A, B = load(args.a), load(args.b)
    common_t = sorted(set(A) & set(B))
    if not common_t:
        raise SystemExit(f"No companies scored in both '{args.a}' and '{args.b}'.")

    rows, passed = [], 0
    print(f"{'Ticker':<8}{'Final A':>8}{'Final B':>8}{'Diff':>7}  Theme scores A -> B"
          f"{'':<20}Result")
    for t in common_t:
        a, b = A[t], B[t]
        fd = round(abs(a["final_score"] - b["final_score"]), 1)
        tdiff = {th: abs(a["scores"][th] - b["scores"][th]) for th in THEMES}
        ok = fd <= MAX_FINAL_DIFF and max(tdiff.values()) <= MAX_THEME_DIFF
        passed += ok
        problems = [f"{th} differs by {d}" for th, d in tdiff.items() if d > MAX_THEME_DIFF]
        if fd > MAX_FINAL_DIFF:
            problems.insert(0, f"final differs by {fd}")
        themes_txt = " ".join(f"{th[:4]} {a['scores'][th]}->{b['scores'][th]}" for th in THEMES)
        print(f"{t:<8}{a['final_score']:>8}{b['final_score']:>8}{fd:>7}  {themes_txt:<38}"
              f"{'PASS' if ok else 'FAIL: ' + '; '.join(problems)}")
        rows.append({"ticker": t, "final_a": a["final_score"], "final_b": b["final_score"],
                     "final_diff": fd,
                     **{f"{th}_a": a["scores"][th] for th in THEMES},
                     **{f"{th}_b": b["scores"][th] for th in THEMES},
                     "result": "PASS" if ok else "FAIL", "problems": "; ".join(problems)})

    only = sorted(set(A) ^ set(B))
    print(f"\n{passed}/{len(common_t)} companies PASS.")
    if only:
        print(f"Not compared (only in one run): {', '.join(only)}")
    path = OUTPUT / f"overlap_{args.a}_vs_{args.b}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {path.relative_to(OUTPUT.parent)}")


if __name__ == "__main__":
    main()
