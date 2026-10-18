"""Step 10: rank companies within each sector and write the outputs.
No AI usage.

Reads output/merged.json (from merge.py) and writes:
  output/results.csv     one row per company
  output/evidence.csv    one row per evidence record
  output/summary.md      readable summary per sector, with each sector's
                         shortlist and the strongest verified evidence
  output/needs_human.csv everything the tool couldn't handle, with a reason

Rank 1 = highest final score in the sector (ties share a rank).
Sector percentile = % of companies in the sector with a final score less
than or equal to this one (the top company is 100).
Shortlist = top shortlist_pct of each sector (config.yaml), rounded up,
at least shortlist_min_per_sector companies, plus anyone tied with the
last spot.

Run:  python src/rank.py
"""

import csv
import json
import math
from collections import defaultdict

from ai import THEMES
from common import (OUTPUT, SECTION_LABELS, load_config, read_json,
                    read_needs_human)

THEME_LABELS = {"creativity": "Creativity", "community": "Community",
                "cross_cultural": "Cross-cultural", "entrepreneurship": "Entrepreneurship"}
FIELDS = ["ticker", "company_name", "sector", "filing_date", "fiscal_year",
          "sections_read",
          "creativity", "community", "cross_cultural", "entrepreneurship",
          "creativity_coverage", "community_coverage", "cross_cultural_coverage",
          "entrepreneurship_coverage",
          "final_score", "sector_rank", "sector_percentile", "in_shortlist",
          "score_source", "original_10k_scores", "quote_check_status", "flags",
          "report_url"]
EVIDENCE_FIELDS = ["ticker", "id", "activity", "quote", "section", "status",
                   "indicators", "themes", "source_doc", "verified"]

DISCLAIMER = (
    "> This is a **values filter only**. It helps choose among stocks that "
    "already fit the team's financial plan. It never makes up for weak finances "
    "or high valuation, and it never outweighs safely funding Laura's fixed "
    "$50,000 yearly payments for 2033-2042.")


def rank_sector(results, pct, min_n):
    results.sort(key=lambda r: -r["final_score"])
    n = len(results)
    for r in results:
        r["sector_rank"] = 1 + sum(1 for o in results if o["final_score"] > r["final_score"])
        r["sector_percentile"] = round(
            100 * sum(1 for o in results if o["final_score"] <= r["final_score"]) / n)
    k = min(n, max(min_n, math.ceil(pct * n)))
    cutoff = results[k - 1]["final_score"] if results else 0
    for r in results:
        r["in_shortlist"] = r["final_score"] >= cutoff  # ties with the last spot are in
    return results


def sections_text(sections):
    return " + ".join(SECTION_LABELS[s] for s in sections)


def strongest_evidence(r, theme, k=2):
    """Up to k verified, current records the theme cites (in the AI's order)."""
    by_id = {e["id"]: e for e in r["evidence"]}
    out = []
    for i in r["themes"][theme]["valid_evidence_ids"]:
        if i in by_id and by_id[i] not in out:
            out.append(by_id[i])
    return out[:k]


def theme_cell(r, th):
    return f"{r['scores'][th]}{'*' if r['coverage'][th] == 'insufficient' else ''}"


def main():
    cfg = load_config()
    pct = float(cfg.get("shortlist_pct", 0.40))
    min_n = int(cfg.get("shortlist_min_per_sector", 2))
    merged = read_json(OUTPUT / "merged.json")
    by_sector = defaultdict(list)
    for r in merged["results"].values():
        by_sector[r["sector"]].append(r)
    for s in by_sector:
        rank_sector(by_sector[s], pct, min_n)

    with open(OUTPUT / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for sector in sorted(by_sector):
            for r in by_sector[sector]:
                w.writerow({
                    "ticker": r["ticker"], "company_name": r["company_name"],
                    "sector": sector, "filing_date": r["filing_date"],
                    "fiscal_year": r["fiscal_year"],
                    "sections_read": sections_text(r["sections"]),
                    **{t: r["scores"][t] for t in THEMES},
                    **{f"{t}_coverage": r["coverage"][t] for t in THEMES},
                    "final_score": r["final_score"], "sector_rank": r["sector_rank"],
                    "sector_percentile": r["sector_percentile"],
                    "in_shortlist": r["in_shortlist"],
                    "score_source": r["score_source"],
                    "original_10k_scores": json.dumps(r["original_10k_scores"])
                    if r.get("original_10k_scores") else "",
                    "quote_check_status": r["quote_check_status"],
                    "flags": " | ".join(r["flags"]),
                    "report_url": r["report_url"],
                })

    with open(OUTPUT / "evidence.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EVIDENCE_FIELDS)
        w.writeheader()
        for sector in sorted(by_sector):
            for r in by_sector[sector]:
                for e in r["evidence"]:
                    w.writerow({
                        "ticker": r["ticker"], "id": e["id"], "activity": e.get("activity", ""),
                        "quote": e.get("quote", ""), "section": e.get("section", ""),
                        "status": e.get("status", ""),
                        "indicators": e.get("indicators", ""),
                        "themes": "; ".join(f"{k}: {v}" for k, v in (e.get("themes") or {}).items()),
                        "source_doc": e.get("source_doc", "10-K"),
                        "verified": e.get("verified"),
                    })

    md = ["# Laura Gao values screener: summary", "", DISCLAIMER, "",
          f"Model: `{merged['model']}` · Sections read: {sections_text(merged['sections'])} · "
          f"Final score = Creativity/5×35 + Community/5×30 + Cross-cultural/5×20 "
          f"+ Entrepreneurship/5×15 · `*` = coverage insufficient (the filing says too "
          f"little to judge that theme) · Shortlist = top {pct:.0%} of each sector "
          f"(at least {min_n}, ties included)", ""]
    for sector in sorted(by_sector):
        rows = by_sector[sector]
        short = [r for r in rows if r["in_shortlist"]]
        md += [f"## {sector} ({len(rows)} companies)", "",
               f"**Shortlist ({len(short)}):** " +
               ", ".join(f"{r['company_name']} ({r['ticker']}, {r['final_score']})" for r in short),
               "",
               "| Rank | Company | Final | Creativity | Community | Cross-cultural "
               "| Entrepreneurship | Shortlist | Flags |",
               "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            flags = []
            if r["quote_check_status"] == "failed":
                flags.append("quote check failed")
            if r["score_source"] != "10-K":
                flags.append("rescored with report")
            if any(r["coverage"][t] == "insufficient" for t in THEMES):
                flags.append("insufficient coverage")
            if r["flags"] and r["quote_check_status"] != "failed":
                flags.append(f"{len(r['flags'])} note(s)")
            md.append(f"| {r['sector_rank']} | {r['company_name']} ({r['ticker']}) | "
                      f"**{r['final_score']}** | " +
                      " | ".join(theme_cell(r, t) for t in THEMES) +
                      f" | {'yes' if r['in_shortlist'] else ''} | {', '.join(flags)} |")
        md.append("")
        for r in rows:
            md += [f"### {r['sector_rank']}. {r['company_name']} ({r['ticker']}): "
                   f"{r['final_score']}/100" + (" · shortlist" if r["in_shortlist"] else ""),
                   f"10-K filed {r['filing_date']} (fiscal {r['fiscal_year']}) · "
                   f"source: {r['score_source']}"
                   + (f" · report: {r['report_url']}" if r["report_url"] else ""), ""]
            md.append("Scores: " + " · ".join(
                f"{THEME_LABELS[t]} {r['scores'][t]} ({r['coverage'][t]})" for t in THEMES)
                + f" → **{r['final_score']}**")
            if r.get("original_10k_scores"):
                md.append(f"10-K-only scores before the report: {r['original_10k_scores']} "
                          f"→ {r['original_10k_final']}")
            md.append("")
            for t in THEMES:
                th = r["themes"][t]
                extra = " (rescored with the sustainability report)" if th.get("rescored_from_report") else ""
                md.append(f"- **{THEME_LABELS[t]}: {th['score']}/5** "
                          f"({th['coverage']}){extra}: {th['reason']}")
                for e in strongest_evidence(r, t):
                    ind = e.get("indicators") or ""
                    md.append(f"  - {e['activity']}" + (f" [{ind}]" if ind and ind != "none disclosed" else "")
                              + f": \"{e['quote']}\" ({e.get('section', '')}"
                              + (", sustainability report" if e.get("source_doc") == "report" else "")
                              + ")")
            if r["flags"]:
                md.append("- Flags: " + "; ".join(r["flags"]))
            if r.get("notes"):
                md.append(f"- AI notes: {r['notes']}")
            md.append("")
    (OUTPUT / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # keep verify flags only from the runs being ranked (not test runs)
    ranked_runs = {r["run_id"] for r in merged["results"].values()}
    ranked = set(merged["results"])
    flags = [f for f in read_needs_human()
             if not f["step"].startswith("verify:") or
             (f["step"].split(":", 1)[1] in ranked_runs and f["ticker"] in ranked)]
    for t in merged.get("missing", []):
        flags.append({"ticker": t, "step": "merge", "reason": "no verified result",
                      "details": "", "when": "", "who": ""})
    with open(OUTPUT / "needs_human.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "step", "reason", "details", "when", "who"])
        w.writeheader()
        w.writerows(sorted(flags, key=lambda x: (x["ticker"], x["step"])))

    print(f"Wrote output/results.csv ({sum(len(v) for v in by_sector.values())} companies), "
          f"output/evidence.csv, output/summary.md, output/needs_human.csv ({len(flags)} rows)")


if __name__ == "__main__":
    main()
