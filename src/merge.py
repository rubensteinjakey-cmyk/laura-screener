"""Step 8: combine everyone's verified results into one set. No AI usage.

Reads every data/verified/*.json (each teammate's files arrive there through
GitHub), keeps only results made with the team's model and sections from
config.yaml, and picks the LATEST valid result per company. Warns about
companies in companies.csv with no result, companies scored more than once,
and results for companies not in companies.csv.

Output: output/merged.json

Run:  python src/merge.py
      python src/merge.py --run-ids jakey,maya,sam   (only these runs)
      python src/merge.py --sections item1,item7_targeted
"""

import argparse
from collections import defaultdict

from verify import RUBRIC_VERSION
from common import (OUTPUT, VERIFIED, load_companies, load_config, log,
                    read_json, write_json)


def merge(run_ids=None, sections=None):
    cfg = load_config()
    want_sections = list(sections or cfg["sections"])
    by_ticker, skipped = defaultdict(list), []
    for f in sorted(VERIFIED.glob("*.json")):
        r = read_json(f)
        if run_ids and r["run_id"] not in run_ids:
            continue
        if str(r.get("rubric_version", 1)) != RUBRIC_VERSION:
            skipped.append(f"{f.name} (older rubric v{r.get('rubric_version', 1)})")
            continue
        if r["model"] != cfg["model"] or list(r["sections"]) != want_sections:
            skipped.append(f"{f.name} (model {r['model']}, sections {r['sections']})")
            continue
        by_ticker[r["ticker"]].append(r)

    if skipped:
        log("merge", f"Ignored {len(skipped)} result(s) made with a different "
                     f"model/sections than config.yaml ({cfg['model']}, {want_sections}):")
        for s in skipped:
            log("merge", f"   {s}")

    merged = {}
    for t, results in by_ticker.items():
        results.sort(key=lambda r: r["scored_at"])
        if len(results) > 1:
            log("merge", f"DUPLICATE {t}: {len(results)} results "
                         f"({', '.join(r['run_id'] for r in results)}); using the latest "
                         f"({results[-1]['run_id']}, {results[-1]['scored_at']}). "
                         f"Finals: {[r['final_score'] for r in results]}")
        merged[t] = results[-1]

    listed = {c["ticker"]: c for c in load_companies()}
    missing = [t for t in listed if t not in merged]
    extra = [t for t in merged if t not in listed]
    if missing:
        log("merge", f"MISSING results for {len(missing)} company(ies): {', '.join(missing)}")
    if extra:
        log("merge", f"Results for companies NOT in companies.csv (dropped): {', '.join(extra)}")
    for t in extra:
        merged.pop(t)
    # sector/company name always come from the current companies.csv
    for t, r in merged.items():
        r["sector"] = listed[t]["sector"]
        r["company_name"] = listed[t]["company_name"]

    write_json(OUTPUT / "merged.json", {"model": cfg["model"], "sections": want_sections,
                                        "missing": missing, "results": merged})
    log("merge", f"Merged {len(merged)} companies into output/merged.json")
    return merged, missing


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-ids", default="", help="comma list; default = all")
    p.add_argument("--sections", default="",
                   help="override config.yaml sections, e.g. item1,item7_targeted")
    args = p.parse_args()
    ids = {x.strip() for x in args.run_ids.split(",") if x.strip()} or None
    secs = [x.strip() for x in args.sections.split(",") if x.strip()] or None
    merge(ids, secs)


if __name__ == "__main__":
    main()
