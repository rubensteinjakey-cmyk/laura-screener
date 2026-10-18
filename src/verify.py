"""Step 6: check quotes and calculate final scores. No AI usage.

For every score file of a run:
  1. Quote check: each quote must appear word for word in the text the AI
     was given (after normalizing spaces, line breaks, curly/straight
     quotes, dashes and upper/lower case). If ANY quote for a theme fails,
     or a score of 1+ has no quotes, that theme becomes 0 and is flagged.
  2. Thin-filing check: true if the AI said so, OR the Human Capital
     subsection is missing / under 150 words (code check). We record which.
  3. If a second look (sustainability report) exists, its Community and
     Cross-cultural scores are checked against the report text, and can
     only RAISE the 10-K scores.
  4. Final score = creativity/5*35 + community/5*30 + cross_cultural/5*20
                   + entrepreneurship/5*15   (computed here, by code)

Output: data/verified/{TICKER}_{run_id}.json
This step is cheap, so it always recalculates everything.

Run:  python src/verify.py               (run_id from config)
      python src/verify.py --run-id repeat1
"""

import argparse
import re

from ai import THEMES
from common import (REPORTS, SCORES, SECTIONS, VERIFIED, clear_human_flags,
                    flag_human, load_config, log, read_json, write_json)

WEIGHTS = {"creativity": 35, "community": 30, "cross_cultural": 20,
           "entrepreneurship": 15}
SECOND_LOOK_THEMES = ["community", "cross_cultural"]
HUMAN_CAPITAL_MIN_WORDS = 150

TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "`": "'", "´": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-", "­": None,
    " ": " ", " ": " ", " ": " ", "​": None,
    "|": " ",  # table-cell separator added during extraction
})


def normalize(text):
    text = text.translate(TRANSLATE).lower()
    text = re.sub(r"\s*-\s*", "-", text)  # "long - term" == "long-term"
    return re.sub(r"\s+", " ", text).strip()


def final_score(scores):
    return round(sum(scores[t] / 5 * WEIGHTS[t] for t in THEMES), 1)


def check_quotes(theme_obj, source_norm):
    """Returns (passed, details list)."""
    quotes = theme_obj.get("quotes", []) or []
    details = []
    # PDF text breaks words across lines with a hyphen ("commu-nity"), so a
    # second try ignores hyphens on both sides.
    source_nohyphen = source_norm.replace("-", "")
    for q in quotes:
        nq = normalize(q).strip(' "\'')
        ok = bool(nq) and (nq in source_norm or nq.replace("-", "") in source_nohyphen)
        details.append({"quote": q, "found": ok})
    if theme_obj["score"] >= 1 and not quotes:
        return False, details + [{"quote": None, "found": False,
                                  "problem": "score of 1+ with no quotes"}]
    return all(d["found"] for d in details), details


def verify_themes(themes_in, source_text, themes):
    """Quote-check the given themes. Returns per-theme results."""
    src = normalize(source_text)
    out = {}
    for th in themes:
        t = themes_in[th]
        passed, details = check_quotes(t, src)
        out[th] = {
            "ai_score": t["score"],
            "score": t["score"] if passed else 0,
            "quote_check": "passed" if passed else "failed",
            "level_reason": t.get("level_reason", ""),
            "quotes": details,
        }
    return out


def verify_run(run_id):
    files = sorted(p for p in SCORES.glob(f"*_{run_id}.json"))
    if not files:
        log("verify", f"No score files found for run '{run_id}'.")
        return
    for path in files:
        rec = read_json(path)
        if rec.get("run_id") != run_id:
            continue
        t = rec["ticker"]
        clear_human_flags(t, f"verify:{run_id}")

        source = "\n\n".join(
            (SECTIONS / t / f"{s}.txt").read_text(encoding="utf-8")
            for s in rec["sections"])
        themes = verify_themes(rec["ai"]["themes"], source, THEMES)

        failed = [th for th in THEMES if themes[th]["quote_check"] == "failed"]
        for th in failed:
            bad = [d["quote"] for d in themes[th]["quotes"] if not d["found"]]
            flag_human(t, f"verify:{run_id}", f"quote_check_failed: {th}",
                       " || ".join(str(b) for b in bad)[:500])

        # ---- thin filing
        info = read_json(SECTIONS / t / "info.json")
        ai_thin = bool(rec["ai"].get("thin_filing"))
        code_thin = (not info.get("human_capital_found")
                     or info.get("human_capital_words", 0) < HUMAN_CAPITAL_MIN_WORDS)
        trigger = ("ai+code" if ai_thin and code_thin else
                   "ai" if ai_thin else "code" if code_thin else "")

        # ---- optional second look (sustainability report)
        score_source = "10-K"
        report_url = ""
        second = None
        sl_path = REPORTS / t / f"second_look_{run_id}.json"
        if sl_path.exists():
            sl = read_json(sl_path)
            second = {"status": sl.get("status"), "report_url": sl.get("report_url", ""),
                      "report_year": sl.get("report_year")}
            if sl.get("status") == "scored":
                report_text = (REPORTS / t / sl["report_text_file"]).read_text(encoding="utf-8")
                sl_themes = verify_themes(sl["ai"]["themes"], report_text, SECOND_LOOK_THEMES)
                second["themes"] = sl_themes
                raised = False
                for th in SECOND_LOOK_THEMES:
                    if sl_themes[th]["quote_check"] == "failed":
                        flag_human(t, f"verify:{run_id}",
                                   f"second look quote_check_failed: {th}", sl.get("report_url", ""))
                    # scores can only go up, never down
                    if sl_themes[th]["score"] > themes[th]["score"]:
                        themes[th]["score_from_10k"] = themes[th]["score"]
                        themes[th]["score"] = sl_themes[th]["score"]
                        themes[th]["raised_by_report"] = True
                        themes[th]["report_quotes"] = sl_themes[th]["quotes"]
                        raised = True
                if raised:
                    score_source = "10-K + sustainability report"
                report_url = sl.get("report_url", "")

        scores = {th: themes[th]["score"] for th in THEMES}
        any_failed = bool(failed) or (second and any(
            v["quote_check"] == "failed" for v in second.get("themes", {}).values()))
        out = {
            "ticker": t,
            "company_name": rec["company_name"],
            "sector": rec["sector"],
            "run_id": run_id,
            "model": rec["model"],
            "sections": rec["sections"],
            "filing_date": rec["filing_date"],
            "fiscal_year": rec["fiscal_year"],
            "accession_number": rec["accession_number"],
            "scored_at": rec["scored_at"],
            "themes": themes,
            "scores": scores,
            "final_score": final_score(scores),
            "quote_check_status": "failed" if any_failed else "passed",
            "thin_filing": ai_thin or code_thin,
            "thin_filing_trigger": trigger,
            "human_capital_words": info.get("human_capital_words", 0),
            "score_source": score_source,
            "report_url": report_url,
            "second_look": second,
        }
        write_json(VERIFIED / f"{t}_{run_id}.json", out)
        log("verify", f"{t}: final {out['final_score']} {scores} quotes "
                      f"{out['quote_check_status']}, thin_filing {out['thin_filing']}"
                      f"{' (' + trigger + ')' if trigger else ''}, source {score_source}")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", default=cfg["run_id"])
    args = p.parse_args()
    verify_run(args.run_id)


if __name__ == "__main__":
    main()
