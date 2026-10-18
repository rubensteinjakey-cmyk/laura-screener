"""Blind check: a teammate hand-scores companies with rubric.md WITHOUT
seeing the AI's scores, then we compare. No AI usage.

1) Make the blank sheet (it contains no AI scores):
     python src/blind_check.py make --tickers JPM,BAC,WFC
   -> output/blind_check_template.csv
   Copy it (e.g. to blind_check_maya.csv), read each company's 10-K
   sections in data/sections/{TICKER}/ (item1.txt, and item7_targeted.txt
   or item7.txt if the team scores MD&A), and fill in for each theme a score
   0-5 and coverage (sufficient / insufficient). Don't open output/ or
   data/scores/ or data/verified/ while scoring.

2) Compare the filled sheet with the AI's verified results:
     python src/blind_check.py compare blind_check_maya.csv --run-id jakey
   -> prints the comparison and saves output/blind_check_comparison.csv
   AGREE = final scores within 5 points and no theme more than 1 apart
   (the same rule as overlap_check.py).
"""

import argparse
import csv
from pathlib import Path

from ai import THEMES
from common import OUTPUT, VERIFIED, load_companies, read_json
from verify import final_score

MAX_FINAL_DIFF = 5
MAX_THEME_DIFF = 1
COLUMNS = (["ticker", "company_name", "sector", "scorer"]
           + [f"{t}_{x}" for t in THEMES for x in ("score", "coverage")]
           + ["notes"])


def make(tickers):
    rows = [c for c in load_companies() if not tickers or c["ticker"] in tickers]
    path = OUTPUT / "blind_check_template.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for c in rows:
            w.writerow({"ticker": c["ticker"], "company_name": c["company_name"],
                        "sector": c["sector"]})
    print(f"Wrote {path.relative_to(OUTPUT.parent)} with {len(rows)} companies (no AI scores in it).")


def compare(sheet, run_id):
    with open(sheet, encoding="utf-8-sig") as f:
        human = [r for r in csv.DictReader(f) if r.get("ticker")]
    out, agree, n = [], 0, 0
    print(f"{'Ticker':<7}{'Human':>7}{'AI':>7}{'Diff':>6}  Themes human/AI (cov: s=sufficient, i=insufficient)   Result")
    for h in human:
        t = h["ticker"].strip().upper()
        vpath = VERIFIED / f"{t}_{run_id}.json"
        if not vpath.exists():
            print(f"{t:<7} no AI result for run '{run_id}'")
            continue
        try:
            hs = {th: int(h[f"{th}_score"]) for th in THEMES}
        except (ValueError, KeyError):
            print(f"{t:<7} sheet row incomplete (every theme needs a 0-5 score)")
            continue
        if any(not 0 <= v <= 5 for v in hs.values()):
            print(f"{t:<7} scores must be 0-5")
            continue
        ai = read_json(vpath)
        hf, af = final_score(hs), ai["final_score"]
        fd = round(abs(hf - af), 1)
        td = {th: abs(hs[th] - ai["scores"][th]) for th in THEMES}
        ok = fd <= MAX_FINAL_DIFF and max(td.values()) <= MAX_THEME_DIFF
        n += 1
        agree += ok
        cov_diff = [th for th in THEMES
                    if (h.get(f"{th}_coverage") or "").strip().lower()[:1] not in ("", ai["coverage"][th][0])]
        problems = [f"{th} differs by {d}" for th, d in td.items() if d > MAX_THEME_DIFF]
        if fd > MAX_FINAL_DIFF:
            problems.insert(0, f"final differs by {fd}")
        cells = " ".join(
            f"{th[:4]} {hs[th]}/{ai['scores'][th]}"
            f"({(h.get(f'{th}_coverage') or '?').strip()[:1].lower()}/{ai['coverage'][th][0]})"
            for th in THEMES)
        print(f"{t:<7}{hf:>7}{af:>7}{fd:>6}  {cells}   {'AGREE' if ok else 'DISAGREE: ' + '; '.join(problems)}")
        out.append({"ticker": t, "scorer": h.get("scorer", ""), "human_final": hf, "ai_final": af,
                    "final_diff": fd,
                    **{f"{th}_human": hs[th] for th in THEMES},
                    **{f"{th}_ai": ai["scores"][th] for th in THEMES},
                    "coverage_disagrees_on": ", ".join(cov_diff),
                    "result": "AGREE" if ok else "DISAGREE", "problems": "; ".join(problems)})
    if not out:
        raise SystemExit("Nothing to compare.")
    print(f"\n{agree}/{n} companies AGREE.")
    path = OUTPUT / "blind_check_comparison.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"Saved {path.relative_to(OUTPUT.parent)}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="write the blank sheet")
    m.add_argument("--tickers", default="", help="e.g. JPM,BAC,WFC (default: all)")
    c = sub.add_parser("compare", help="compare a filled sheet with AI results")
    c.add_argument("sheet")
    c.add_argument("--run-id", required=True)
    a = p.parse_args()
    if a.cmd == "make":
        make({x.strip().upper() for x in a.tickers.split(",") if x.strip()})
    else:
        compare(Path(a.sheet), a.run_id)


if __name__ == "__main__":
    main()
