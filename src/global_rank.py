"""Global step 6: Pass/Fail within each country-sector and the output files.
No AI usage.

Rank 1 = highest final score in the country-sector (ties share a rank).
Pass = top half of the country-sector by final score, ties included:
cutoff = ceil(n / 2) (same safe rounding as the US shortlist), n = scored
companies in the group. Everyone else Fails. Companies not scored (e.g.
global_skip.csv) get "Not scored" and don't count in n.
A group gets final Pass/Fail only once every company in it is scored or
formally excluded by the team (global_skip.csv). Until then every scored
company in it is "Provisional" (rank shown, no Pass/Fail), with "k of n
scored" in values_notes, so nobody passes because their group is mostly empty.
third_component is still UNDECIDED (TODO): left blank.

Writes output/global/: results_global.csv, evidence_global.csv,
summary_global.md, needs_human.csv (only the companies still missing, each with
the page to download from), review_notes_global.csv (quick checks on scored
companies).

Run:  python src/global_rank.py
"""

import argparse
import csv
import json
import math
import re
from collections import defaultdict

from ai import THEMES
from common import load_config, read_json
from global_common import GOUT, G_DOCS, G_VERIFIED, load_global_companies, read_gflags, skipped

THEME_LABELS = {"creativity": "Creativity", "community": "Community",
                "cross_cultural": "Cross-cultural", "entrepreneurship": "Entrepreneurship"}
FIRST = ["country", "sector", "ticker", "company_name", "filing_used", "alignment_score",
         "red_flag_score", "third_component", "final_score", "country_sector_rank",
         "values_result", "values_notes"]
EXTRA = (["source_site"] + THEMES + [f"{t}_coverage" for t in THEMES]
         + ["red_flags", "score_source", "words_sent", "non_english_source",
            "original_filing_scores", "full_report_scores", "quote_check_status", "flags"])
EVIDENCE_FIELDS = ["ticker", "id", "kind", "activity", "quote", "quote_en", "section",
                   "status", "indicators", "themes_or_flag", "source_doc", "verified",
                   "figures_removed"]
NEEDS_FIELDS = ["country", "sector", "ticker", "company_name", "reason", "download_page",
                "save_as", "details"]
NOTES_FIELDS = ["ticker", "step", "reason", "details", "when", "who"]


def download_pages(flag, ticker):
    """The page(s) a person can download the report from."""
    m = re.search(r"page: (.*?); save the PDF", flag["details"])
    if m:
        return m.group(1).replace("; ", " ; ")
    meta = G_DOCS / ticker / "meta.json"
    if meta.exists():
        m = read_json(meta)
        return m.get("exchange_page") or m.get("url") or ""
    return ""


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


def group_results(rows, total):
    """Rank the scored companies of one country-sector; Pass/Fail only when
    all `total` companies (excluding formally excluded ones) are scored,
    otherwise every one is "Provisional"."""
    pass_fail(rows)
    if len(rows) < total:
        for r in rows:
            r["result"] = "Provisional"
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


def source_site(t, r=None):
    """Where the document came from (logged at fetch): SEC EDGAR, an official
    exchange filing site, the company website, or a teammate's manual PDF."""
    if r and r.get("source_site"):
        return r["source_site"]
    meta = G_DOCS / t / "meta.json"
    if not meta.exists():
        return ""
    m = read_json(meta)
    how = m.get("how", "")
    return m.get("source_site") or ("SEC EDGAR" if "EDGAR" in how else "manual PDF"
                                    if how.startswith("manual") else "company website")


def fmt(x):
    return "" if x is None else (f"{x:g}" if isinstance(x, (int, float)) else x)


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", default=cfg.get("global_run_id", "global"))
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
    # Companies still waiting for a document (blocked download, unclear
    # chapters...) are "Not scored". A group with any of them is Provisional:
    # ranked among the scored companies, but no Pass/Fail yet.
    by_group = defaultdict(list)
    for c in companies:
        if c["ticker"].upper() not in skip:
            by_group[(c["country"], c["sector"])].append(c["ticker"])
    groups = defaultdict(list)
    for r in results.values():
        groups[(r["country"], r["sector"])].append(r)
    for key, g in groups.items():
        group_results(g, len(by_group[key]))

    tag = ""
    flags_by_t = defaultdict(list)
    for f in read_gflags():
        flags_by_t[f["ticker"]].append(f["reason"])
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
                why = skip.get(t.upper()) or "; ".join(dict.fromkeys(flags_by_t.get(t, []))) \
                    or "not scored yet"
                w.writerow({**base, "values_result": "Not scored", "values_notes": why})
                continue
            grp = by_group[(r["country"], r["sector"])]
            n_scored = sum(1 for x in grp if x in results)
            rf = ", ".join(r["red_flag_kinds"]) or "none"
            if r["result"] == "Provisional":
                head = (f"{n_scored} of {len(grp)} scored in {r['country']} {r['sector']}; "
                        f"Pass/Fail waits for the rest (rank {r['rank']} so far)")
            else:
                head = (f"{'Top' if r['result'] == 'Pass' else 'Not in top'} half of "
                        f"{r['country']} {r['sector']}")
            notes = (f"{head}; {best_theme_note(r)}; red flag: {rf}"
                     + ("; non-English source" if not r.get("english", True) else "")
                     + (f"; {r['internal_note']}" if r.get("internal_note") else ""))
            w.writerow({
                **base,
                "filing_used": f"{r['form']} {r.get('fiscal_year') or ''} ({source_site(t, r)}) "
                               f"{r.get('url', '')}".strip(),
                "source_site": source_site(t, r),
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
    # needs_human.csv: only what's still missing (not scored), one row per company
    needs = []
    for c in companies:
        t = c["ticker"]
        if t in results or t.upper() in skip:  # scored, or formally excluded by the team
            continue
        mine = [f for f in flags if f["ticker"] == t]
        main_flag = next((f for f in mine if f["step"] in ("global_fetch", "global_extract")),
                         mine[0] if mine else {"reason": "not scored yet", "details": ""})
        excluded = t.upper() in skip
        needs.append({"country": c["country"], "sector": c["sector"], "ticker": t,
                      "company_name": c["company_name"],
                      "reason": skip.get(t.upper()) or main_flag["reason"],
                      "download_page": "" if excluded else download_pages(main_flag, t),
                      # an extraction problem isn't fixed by saving the same PDF again
                      "save_as": "" if excluded or main_flag.get("step") == "global_extract"
                      else f"data/global/manual/{t}.pdf",
                      "details": ("formally excluded by the team (global_skip.csv)" if excluded
                                  else re.sub(r"^page: .*?; save the PDF as \S+ \| ", "",
                                              main_flag["details"])[:400])})
    with open(GOUT / f"needs_human{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=NEEDS_FIELDS)
        w.writeheader()
        w.writerows(needs)
    notes = [f for f in flags if f["ticker"] in results]
    with open(GOUT / f"review_notes_global{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=NOTES_FIELDS)
        w.writeheader()
        w.writerows(sorted(notes, key=lambda x: (x["ticker"], x["step"])))

    md = ["# Global sleeve values screen (Rooted Capital): summary", "",
          "> Values filter only. Pass = top half of the company's country-sector by final "
          "score (ties included). Final = alignment (Creativity/5×35 + Community/5×30 + "
          "Cross-cultural/5×20 + Entrepreneurship/5×15) − "
          f"{cfg.get('red_flag_penalty', 10)} per verified red flag. Third component: TBD.", ""]
    if missing:
        md += [f"**{len(missing)} companies not scored yet (waiting for a document; see "
               "needs_human.csv). Groups with unscored companies are Provisional (no Pass/Fail yet).**", ""]
    for (country, sector), rows in sorted(groups.items()):
        total = len(by_group[(country, sector)])
        md += [f"## {country} · {sector} ({len(rows)} of {total} scored"
               + ("; Provisional: no Pass/Fail until all are scored" if len(rows) < total else "")
               + ")", "",
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
          f"({len(needs)} rows), review notes ({len(notes)} rows)")


if __name__ == "__main__":
    main()
