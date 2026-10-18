"""Step 10: rank companies within each sector and write the outputs.
No AI usage.

Reads output/merged.json (from merge.py) and writes:
  output/results.csv     one row per company
  output/summary.md      readable summary per sector with the best quotes
  output/needs_human.csv everything the tool couldn't handle, with a reason

Rank 1 = highest final score in the sector (ties share a rank).
Sector percentile = % of companies in the sector with a final score less
than or equal to this one (the top company is 100).

Run:  python src/rank.py
"""

import csv
from collections import defaultdict

from ai import THEMES
from common import OUTPUT, read_json, read_needs_human

THEME_LABELS = {"creativity": "Creativity", "community": "Community",
                "cross_cultural": "Cross-cultural", "entrepreneurship": "Entrepreneurship"}
SECTION_LABELS = {"item1": "Item 1", "item7": "Item 7"}
FIELDS = ["ticker", "company_name", "sector", "filing_date", "sections_read",
          "creativity", "community", "cross_cultural", "entrepreneurship",
          "final_score", "sector_rank", "sector_percentile", "thin_filing",
          "score_source", "quote_check_status", "report_url"]

DISCLAIMER = (
    "> This is a **values filter only**. It helps choose among stocks that "
    "already fit the team's financial plan, and it never outweighs safely "
    "funding Laura's fixed $50,000 yearly payments for 2033-2042.")


def rank_sector(results):
    results.sort(key=lambda r: -r["final_score"])
    n = len(results)
    for r in results:
        r["sector_rank"] = 1 + sum(1 for o in results if o["final_score"] > r["final_score"])
        r["sector_percentile"] = round(
            100 * sum(1 for o in results if o["final_score"] <= r["final_score"]) / n)
    return results


def best_quotes(theme, k=2):
    """Verified quotes only; report quotes first if the report raised it."""
    qs = [q["quote"] for q in theme.get("report_quotes", []) if q.get("found")]
    qs += [q["quote"] for q in theme.get("quotes", []) if q.get("found") and q.get("quote")]
    return qs[:k]


def main():
    merged = read_json(OUTPUT / "merged.json")
    by_sector = defaultdict(list)
    for r in merged["results"].values():
        by_sector[r["sector"]].append(r)
    for s in by_sector:
        rank_sector(by_sector[s])

    with open(OUTPUT / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for sector in sorted(by_sector):
            for r in by_sector[sector]:
                w.writerow({
                    "ticker": r["ticker"], "company_name": r["company_name"],
                    "sector": sector, "filing_date": r["filing_date"],
                    "sections_read": " + ".join(SECTION_LABELS[s] for s in r["sections"]),
                    **{t: r["scores"][t] for t in THEMES},
                    "final_score": r["final_score"], "sector_rank": r["sector_rank"],
                    "sector_percentile": r["sector_percentile"],
                    "thin_filing": r["thin_filing"], "score_source": r["score_source"],
                    "quote_check_status": r["quote_check_status"],
                    "report_url": r["report_url"],
                })

    md = ["# Laura Gao values screener: summary", "", DISCLAIMER, "",
          f"Model: `{merged['model']}` · Sections read: "
          f"{' + '.join(SECTION_LABELS[s] for s in merged['sections'])} · "
          f"Final score = Creativity/5×35 + Community/5×30 + Cross-cultural/5×20 "
          f"+ Entrepreneurship/5×15", ""]
    for sector in sorted(by_sector):
        rows = by_sector[sector]
        md += [f"## {sector} ({len(rows)} companies)", "",
               "| Rank | Company | Final | Creativity | Community | Cross-cultural "
               "| Entrepreneurship | Flags |",
               "|---|---|---|---|---|---|---|---|"]
        for r in rows:
            flags = []
            if r["thin_filing"]:
                flags.append("thin filing")
            if r["quote_check_status"] == "failed":
                flags.append("quote check failed")
            if r["score_source"] != "10-K":
                flags.append("raised by report")
            md.append(f"| {r['sector_rank']} | {r['company_name']} ({r['ticker']}) | "
                      f"**{r['final_score']}** | " +
                      " | ".join(str(r["scores"][t]) for t in THEMES) +
                      f" | {', '.join(flags)} |")
        md.append("")
        for r in rows:
            md += [f"### {r['sector_rank']}. {r['company_name']} ({r['ticker']}): "
                   f"{r['final_score']}/100",
                   f"10-K filed {r['filing_date']} · source: {r['score_source']}"
                   + (f" · report: {r['report_url']}" if r["report_url"] else ""), ""]
            for t in THEMES:
                th = r["themes"][t]
                note = " (quote check failed → 0)" if th["quote_check"] == "failed" else ""
                note += (f" (raised from {th['score_from_10k']} by report)"
                         if th.get("raised_by_report") else "")
                md.append(f"- **{THEME_LABELS[t]}: {th['score']}/5**{note}: "
                          f"{th['level_reason']}")
                for q in best_quotes(th):
                    md.append(f"  - > \"{q}\"")
            md.append("")
    (OUTPUT / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    flags = read_needs_human()
    for t in merged.get("missing", []):
        flags.append({"ticker": t, "step": "merge", "reason": "no verified result",
                      "details": "", "when": "", "who": ""})
    with open(OUTPUT / "needs_human.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "step", "reason", "details", "when", "who"])
        w.writeheader()
        w.writerows(sorted(flags, key=lambda x: (x["ticker"], x["step"])))

    print(f"Wrote output/results.csv ({sum(len(v) for v in by_sector.values())} companies), "
          f"output/summary.md, output/needs_human.csv ({len(flags)} rows)")


if __name__ == "__main__":
    main()
