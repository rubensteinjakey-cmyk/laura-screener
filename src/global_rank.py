"""Global step 6: Pass/Fail within each country-sector and the output files.
No AI usage. Run only once every company is scored (it refuses otherwise,
unless --partial, which writes a clearly marked preview).

Rank 1 = highest final score in the country-sector (ties share a rank).
Pass = top half of the country-sector by final score, ties included:
cutoff = ceil(n / 2) (same safe rounding as the US shortlist), n = scored
companies in the group. Everyone else Fails. Companies not scored (e.g.
global_skip.csv) get "Not scored" and don't count in n.
third_component is still UNDECIDED (TODO): left blank.

Writes output/global/: results_global.csv, evidence_global.csv,
summary_global.md, needs_human.csv.

Run:  python src/global_rank.py [--partial]
"""

import argparse
import csv
import json
import math
from collections import defaultdict

from ai import THEMES
from common import load_config, read_json
from global_common import GOUT, G_VERIFIED, load_global_companies, read_gflags, skipped

THEME_LABELS = {"creativity": "Creativity", "community": "Community",
                "cross_cultural": "Cross-cultural", "entrepreneurship": "Entrepreneurship"}
FIRST = ["country", "sector", "ticker", "company_name", "filing_used", "alignment_score",
         "red_flag_score", "third_component", "final_score", "country_sector_rank",
         "values_result", "values_notes"]
EXTRA = (THEMES + [f"{t}_coverage" for t in THEMES]
         + ["red_flags", "score_source", "words_sent", "non_english_source",
            "original_filing_scores", "full_report_scores", "quote_check_status", "flags"])
EVIDENCE_FIELDS = ["ticker", "id", "kind", "activity", "quote", "quote_en", "section",
                   "status", "indicators", "themes_or_flag", "source_doc", "verified",
                   "figures_removed"]
NEEDS_FIELDS = ["ticker", "step", "reason", "details", "when", "who"]


def pass_fail(rows):
    """rows: scored companies of one country-sector (dicts with final_score)."""
    rows.sort(key=lambda r: -r["final_score"])
    n = len(rows)
    k = min(n, math.ceil(round(n / 2, 9)))
    cutoff = rows[k - 1]["final_score"] if rows else 0
    for r in rows:
        r["rank"] = 1 + sum(1 for o in rows if o["final_score"] > r["final_score"])
        r["result"] = "Pass" if r["final_score"] >= cutoff else "Fail"
    return rows


def best_theme_note(r):
    scored = [(r["scores"][t], t) for t in THEMES if r["scores"][t] >= 2]
    if not scored:
        return "no theme above 1"
    s, t = max(scored)
    ev = {e["id"]: e for e in r["evidence"]}
    ids = r["themes"][t].get("valid_evidence_ids", [])
    act = ev[ids[0]].get("activity", "") if ids and ids[0] in ev else ""
    return f"{THEME_LABELS[t]} {s:g}" + (f" from {act[:70].rstrip('.')}" if act else "")


def fmt(x):
    return "" if x is None else (f"{x:g}" if isinstance(x, (int, float)) else x)


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", default=cfg.get("global_run_id", "global"))
    p.add_argument("--partial", action="store_true",
                   help="preview before every company is scored (marked PREVIEW)")
    args = p.parse_args()
    companies = load_global_companies()
    skip = skipped()
    results, missing = {}, []
    for c in companies:
        path = G_VERIFIED / f"{c['ticker']}_{args.run_id}.json"
        if c["ticker"].upper() in skip:
            continue
        if path.exists():
            results[c["ticker"]] = read_json(path)
        else:
            missing.append(c["ticker"])
    if missing and not args.partial:
        raise SystemExit(f"{len(missing)} company(ies) not scored yet ({', '.join(missing)}). "
                         "Pass/Fail is ranked only once every company is scored "
                         "(or use --partial for a preview).")
    groups = defaultdict(list)
    for r in results.values():
        groups[(r["country"], r["sector"])].append(r)
    for g in groups.values():
        pass_fail(g)

    tag = "_PREVIEW" if missing else ""
    GOUT.mkdir(parents=True, exist_ok=True)
    with open(GOUT / f"results_global{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIRST + EXTRA)
        w.writeheader()
        for c in companies:
            t = c["ticker"]
            base = {"country": c["country"], "sector": c["sector"], "ticker": t,
                    "company_name": c["company_name"], "third_component": ""}
            r = results.get(t)
            if not r:
                why = skip.get(t.upper()) or "not scored yet"
                w.writerow({**base, "values_result": "Not scored", "values_notes": why})
                continue
            rf = ", ".join(r["red_flag_kinds"]) or "none"
            notes = (f"{'Top' if r['result'] == 'Pass' else 'Not in top'} half of "
                     f"{r['country']} {r['sector']}; {best_theme_note(r)}; red flag: {rf}"
                     + ("; non-English source" if not r.get("english", True) else "")
                     + (f"; {r['internal_note']}" if r.get("internal_note") else ""))
            w.writerow({
                **base,
                "filing_used": f"{r['form']} {r.get('fiscal_year') or ''} {r.get('url', '')}".strip(),
                "alignment_score": fmt(r["alignment_score"]),
                "red_flag_score": fmt(r["red_flag_score"]),
                "final_score": fmt(r["final_score"]),
                "country_sector_rank": r["rank"], "values_result": r["result"],
                "values_notes": notes,
                **{t2: fmt(r["scores"][t2]) for t2 in THEMES},
                **{f"{t2}_coverage": r["coverage"][t2] for t2 in THEMES},
                "red_flags": rf, "score_source": r["score_source"],
                "words_sent": r["words_sent"],
                "non_english_source": "" if r.get("english", True) else "yes",
                "original_filing_scores": json.dumps(r["original_filing_scores"])
                if r.get("original_filing_scores") else "",
                "full_report_scores": json.dumps(r["full_report_scores"])
                if r.get("full_report_scores") else "",
                "quote_check_status": r["quote_check_status"],
                "flags": " | ".join(r["flags"]),
            })

    with open(GOUT / f"evidence_global{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EVIDENCE_FIELDS)
        w.writeheader()
        for r in results.values():
            for e in r["evidence"]:
                w.writerow({"ticker": r["ticker"], "id": e["id"], "kind": "evidence",
                            "activity": e.get("activity", ""), "quote": e.get("quote", ""),
                            "quote_en": e.get("quote_en", ""), "section": e.get("section", ""),
                            "status": e.get("status", ""), "indicators": e.get("indicators", ""),
                            "themes_or_flag": "; ".join(
                                f"{k}: {v.get('kind', '')}" for k, v in (e.get("themes") or {}).items()
                                if isinstance(v, dict)),
                            "source_doc": e.get("source_doc", ""), "verified": e.get("verified"),
                            "figures_removed": ", ".join(e.get("figures_removed", []))})
            for e in r["red_flags"] + r["red_flags_removed"] + r.get("internal_findings", []):
                w.writerow({"ticker": r["ticker"], "id": e["id"], "kind": "red flag",
                            "activity": e.get("explanation", ""), "quote": e.get("quote", ""),
                            "quote_en": e.get("quote_en", ""), "section": e.get("section", ""),
                            "themes_or_flag": e.get("flag", "") + (
                                f" (found by: {e['found_by']})" if e.get("found_by") else ""),
                            "source_doc": e.get("source_doc"),
                            "verified": e.get("verified")})

    flags = [f for f in read_gflags() if any(f["ticker"] == c["ticker"] for c in companies)]
    with open(GOUT / f"needs_human{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=NEEDS_FIELDS)
        w.writeheader()
        w.writerows(sorted(flags, key=lambda x: (x["ticker"], x["step"])))

    md = ["# Global sleeve values screen (Rooted Capital): summary", "",
          "> Values filter only. Pass = top half of the company's country-sector by final "
          "score (ties included). Final = alignment (Creativity/5×35 + Community/5×30 + "
          "Cross-cultural/5×20 + Entrepreneurship/5×15) − "
          f"{cfg.get('red_flag_penalty', 10)} per verified red flag. Third component: TBD.", ""]
    if missing:
        md += [f"**PREVIEW: {len(missing)} companies not scored yet.**", ""]
    for (country, sector), rows in sorted(groups.items()):
        md += [f"## {country} · {sector} ({len(rows)} scored)", "",
               "| Rank | Company | Final | Align | Red flags | Creat. | Comm. | Cross-c. | Entr. | Result |",
               "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            md.append(f"| {r['rank']} | {r['company_name']} ({r['ticker']}) | **{fmt(r['final_score'])}** | "
                      f"{fmt(r['alignment_score'])} | {', '.join(r['red_flag_kinds']) or '-'} | "
                      + " | ".join(f"{fmt(r['scores'][t])}{'*' if r['coverage'][t] == 'insufficient' else ''}"
                                   for t in THEMES) + f" | {r['result']} |")
        md.append("")
        for r in rows:
            ev = {e["id"]: e for e in r["evidence"]}
            best = []
            for t in sorted(THEMES, key=lambda x: -r["scores"][x]):
                for i in r["themes"][t].get("valid_evidence_ids", []):
                    if i in ev and ev[i] not in best:
                        best.append(ev[i])
            md.append(f"- **{r['company_name']}**: {r['form']} {r.get('fiscal_year') or ''}"
                      + ("" if r.get("english", True) else " (non-English source)"))
            for e in best[:2]:
                md.append(f"  - {e.get('activity', '')}: \"{e['quote']}\""
                          + (f" (English: \"{e['quote_en']}\")" if e.get("quote_en") else ""))
            for e in r["red_flags"]:
                md.append(f"  - RED FLAG ({e['flag']}): \"{e['quote']}\"")
        md.append("")
    nots = [c for c in companies if c["ticker"] not in results]
    if nots:
        md += ["## Not scored", ""] + [f"- {c['company_name']} ({c['ticker']}): "
                                       f"{skip.get(c['ticker'].upper()) or 'not scored yet'}"
                                       for c in nots] + [""]
    (GOUT / f"summary_global{tag}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"Wrote output/global/results_global{tag}.csv ({len(results)} scored, "
          f"{len(companies) - len(results)} not scored), evidence, summary, needs_human "
          f"({len(flags)} rows)")


if __name__ == "__main__":
    main()
