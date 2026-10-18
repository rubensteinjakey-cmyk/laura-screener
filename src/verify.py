"""Step 6: check evidence and quotes, then calculate final scores.
No AI usage, pure code. It never re-asks the AI.

For every score file of a run (Rubric v2.1):
  0. Figure check: every number in a record's "indicators" must appear
     within about 1,200 characters of its quote (same paragraph or table).
     Missing figures are removed and flagged; if the theme is level 3+ the
     theme is flagged for human review (never silently rescored).
  1. Quote check: each evidence record's quote must appear word for word in
     the text it came from (after normalizing spaces, line breaks,
     curly/straight quotes, dashes and upper/lower case). A record whose
     quote fails is REMOVED and flagged quote_check_failed.
  2. Evidence check, per theme:
     - a score of 2+ must cite at least one valid evidence record with
       status "current" (a score of 1 may rest on general language); if a
       cited record failed and no valid current evidence is left, the
       score becomes 0 (flagged)
     - caps by kind (Rubric v2.1): e.g. Community 3+ needs a cited
       external_education / community_investment / education_business
       record, Entrepreneurship 2+ needs an "enabling" record. A broken cap
       lowers the score to the cap and is flagged.
     - if some cited evidence was removed but valid current evidence
       remains, the score is kept (capped at what the AI gave) and flagged
       for human review
     - citing past/planned evidence, or the same activity twice in one
       theme (double counting), is flagged
  3. Coverage: the AI's coverage per theme is kept. As a backup, the code
     marks Community and Cross-cultural "insufficient" if the Human Capital
     subsection is missing or under 150 words. We record who triggered it.
  4. Second look: if data/reports/{TICKER}/second_look_{run_id}.json has
     rescored themes, their evidence is checked the same way (against the
     10-K or the report, whichever each record came from) and the rescored
     theme REPLACES the 10-K one (it can go up or down). The original 10-K
     scores are kept.
  5. Final score = creativity/5*35 + community/5*30 + cross_cultural/5*20
                   + entrepreneurship/5*15   (computed here, by code)

Output: data/verified/{TICKER}_{run_id}.json (always recalculated).

Run:  python src/verify.py               (run_id from config)
      python src/verify.py --run-id jakey_repeat
"""

import argparse
import re

from ai import THEMES
from common import (REPORTS, SCORES, SECTIONS, VERIFIED, VERIFY_AGAINST,
                    clear_human_flags, flag_human, load_config, log, read_json,
                    write_json)

RUBRIC_VERSION = "2.1"
WEIGHTS = {"creativity": 35, "community": 30, "cross_cultural": 20,
           "entrepreneurship": 15}
COVERAGE_BACKUP_THEMES = ["community", "cross_cultural"]
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


FIGURE_WINDOW = 1200  # characters either side of the quote ("same paragraph or table")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
NUMBER_WORDS_RE = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b")

# Rubric v2.1 caps, enforced by code: (score that needs it, kinds that
# allow it, score to cap at otherwise)
CAPS = {
    "creativity": (2, {"creates", "enables"}, 1),
    "community": (3, {"external_education", "community_investment", "education_business"}, 2),
    "cross_cultural": (2, {"internal_practice", "customer_access", "connecting_communities"}, 1),
    "entrepreneurship": (2, {"enabling"}, 1),
}
LEGACY_DOC_NAMES = {"report": "sustainability_report"}


def find_quote(quote, source_norm, source_nohyphen):
    """Position of the quote in the normalized source, or -1.
    PDF text breaks words across lines with a hyphen ("commu-nity"), so a
    second try ignores hyphens on both sides (position is then approximate)."""
    nq = normalize(quote or "").strip(' "\'')
    if len(nq) < 8:  # an empty or tiny "quote" proves nothing
        return -1
    i = source_norm.find(nq)
    if i >= 0:
        return i
    j = source_nohyphen.find(nq.replace("-", ""))
    return j if j < 0 else min(j, len(source_norm) - 1)


def figures_in(indicators):
    """Numbers in an indicators string, skipping years like 2025."""
    out = []
    for m in NUMBER.finditer(indicators or ""):
        n = m.group().rstrip(".,")
        if re.fullmatch(r"(19|20)\d\d", n):
            continue
        out.append(n)
    return out


def figure_near(num, window):
    """Is this number written in the window? Commas are ignored ("330,000" =
    "330000") and small number words count ("four million" = "4 million")."""
    w = NUMBER_WORDS_RE.sub(lambda m: NUMBER_WORDS[m.group()], window).replace(",", "")
    n = num.replace(",", "")
    return re.search(r"(?<![\d.])" + re.escape(n) + r"(?![\d])", w) is not None


def check_evidence(evidence, sources, default_doc="10-K"):
    """Check every evidence record's quote and figures.
    sources: {doc_name: full text}. Each record is checked against
    sources[record.source_doc].
    - quote not found -> record removed (verified False, quote_check_failed)
    - a figure in indicators not found within FIGURE_WINDOW characters of the
      quote -> that figure is removed from indicators and listed in
      figures_removed (the record itself stays)
    Returns {id: record + verified/problem/figures_removed}."""
    norm = {d: normalize(txt) for d, txt in sources.items()}
    nohy = {d: n.replace("-", "") for d, n in norm.items()}
    out = {}
    for e in evidence:
        r = dict(e)
        doc = r.get("source_doc") or default_doc
        doc = LEGACY_DOC_NAMES.get(doc, doc)
        r["source_doc"] = doc
        r["figures_removed"] = []
        r["warnings"] = []
        if r["id"] in out:
            r["verified"], r["problem"] = False, "duplicate evidence id"
        elif doc not in norm:
            r["verified"], r["problem"] = False, f"unknown source_doc {doc!r}"
        else:
            pos = find_quote(r.get("quote"), norm[doc], nohy[doc])
            if pos < 0:
                r["verified"], r["problem"] = False, "quote_check_failed"
            else:
                r["verified"], r["problem"] = True, ""
                qlen = len(normalize(r.get("quote", "")))
                window = norm[doc][max(0, pos - FIGURE_WINDOW): pos + qlen + FIGURE_WINDOW]
                ind = r.get("indicators") or ""
                for num in figures_in(ind):
                    if not figure_near(num, window):
                        r["figures_removed"].append(num)
                if r["figures_removed"]:
                    r["indicators_original"] = ind
                    for num in r["figures_removed"]:
                        ind = re.sub(r"(?<![\d.,])" + re.escape(num) + r"(?![\d])",
                                     "[figure removed]", ind)
                    r["indicators"] = ind
                if doc == "sustainability_report" and \
                        not re.search(r"[.!?:;\"”)]\s*$", (r.get("quote") or "").strip()):
                    r["warnings"].append("report quote may be a sentence fragment")
        out.setdefault(r["id"], r)
    return out


def check_theme(name, theme, ev):
    """Evidence rules and caps for one theme. ev = output of check_evidence.
    Returns the verified theme dict (the score may be lowered)."""
    cited = list(theme.get("evidence_ids", []))
    flags = []

    # double counting: same id twice, or two records for the same activity
    if len(set(cited)) < len(cited):
        flags.append("double_counting: same evidence id cited twice")
    uniq = list(dict.fromkeys(cited))
    seen = []
    for i in uniq:
        if i in ev:
            q = normalize(ev[i].get("quote", ""))
            a = normalize(ev[i].get("activity", ""))
            for j, (q2, a2) in seen:
                if q and q2 and (q in q2 or q2 in q) or (a and a == a2):
                    flags.append(f"double_counting: {j} and {i} are the same activity")
            seen.append((i, (q, a)))

    unknown = [i for i in uniq if i not in ev]
    removed = [i for i in uniq if i in ev and not ev[i]["verified"]]
    not_current = [i for i in uniq if i in ev and ev[i]["verified"]
                   and ev[i].get("status") != "current"]
    untagged = [i for i in uniq if i in ev and ev[i]["verified"]
                and ev[i].get("status") == "current" and name not in (ev[i].get("themes") or {})]
    valid_current = [i for i in uniq if i in ev and ev[i]["verified"]
                     and ev[i].get("status") == "current" and i not in untagged]
    if unknown:
        flags.append(f"cites unknown evidence id(s): {', '.join(unknown)}")
    if removed:
        flags.append(f"cited evidence removed by quote check: {', '.join(removed)}")
    if not_current:
        flags.append("cites non-current evidence (doesn't count): " + ", ".join(
            f"{i} ({ev[i]['status']})" for i in not_current))
    if untagged:
        flags.append(f"cites evidence not tagged for this theme (doesn't count): {', '.join(untagged)}")

    ai_score = theme["score"]
    score = ai_score
    if ai_score >= 1 and (removed or unknown) and not valid_current:
        score = 0
        flags.append("score set to 0: cited evidence failed and no valid current evidence is left")
    elif ai_score >= 2 and not valid_current:
        score = 0
        flags.append("score set to 0: a score of 2+ needs verified current evidence")
    elif ai_score >= 1 and (removed or unknown):
        flags.append("needs human review: score kept (capped at AI's) on remaining evidence")

    # caps by kind
    need, allowed, cap = CAPS[name]
    kinds = {ev[i]["themes"][name].get("kind") for i in valid_current}
    if score >= need and not kinds & allowed:
        flags.append(f"cap: {name} {score} needs a cited {'/'.join(sorted(allowed))} "
                     f"record; lowered to {cap}")
        score = cap

    # figures removed from evidence that supports a level 3+ score
    lost = [i for i in valid_current if ev[i]["figures_removed"]]
    if score >= 3 and lost:
        flags.append("needs human review: figure(s) removed from cited evidence "
                     f"{', '.join(lost)}; the level may be lower")

    return {"ai_score": ai_score, "score": score,
            "coverage": theme.get("coverage", "sufficient"),
            "evidence_ids": cited, "valid_evidence_ids": valid_current,
            "kinds": sorted(k for k in kinds if k),
            "reason": theme.get("reason", ""), "flags": flags}


def tenk_source(ticker, sections):
    """Full 10-K text the quotes are checked against (targeted MD&A quotes
    are checked against the full Item 7)."""
    docs = []
    for s in sections:
        d = VERIFY_AGAINST[s]
        if d not in docs:
            docs.append(d)
    return "\n\n".join((SECTIONS / ticker / f"{d}.txt").read_text(encoding="utf-8")
                       for d in docs)


def verify_record(rec, run_id):
    t = rec["ticker"]
    step = f"verify:{run_id}"
    clear_human_flags(t, step)
    flags = []

    tenk = tenk_source(t, rec["sections"])
    ev = check_evidence(rec["ai"]["evidence"], {"10-K": tenk})
    themes = {th: check_theme(th, rec["ai"]["themes"][th], ev) for th in THEMES}

    # ---- coverage backup from the Human Capital word count
    info = read_json(SECTIONS / t / "info.json")
    # the people discussion: the Human Capital section, or (if it has no
    # heading) the Item 1 paragraphs about employees / workforce / community
    people_words = max(info.get("human_capital_words", 0),
                       info.get("people_paragraph_words", 0))
    hc_thin = people_words < HUMAN_CAPITAL_MIN_WORDS
    for th in THEMES:
        ai_insuff = themes[th]["coverage"] == "insufficient"
        code_insuff = hc_thin and th in COVERAGE_BACKUP_THEMES
        themes[th]["coverage_trigger"] = ("ai+code" if ai_insuff and code_insuff else
                                          "ai" if ai_insuff else "code" if code_insuff else "")
        if code_insuff:
            themes[th]["coverage"] = "insufficient"
        if ai_insuff:
            themes[th]["coverage_reason_ai"] = themes[th]["reason"]
        if code_insuff:
            themes[th]["coverage_reason_code"] = (
                f"people discussion is {people_words} words (under {HUMAN_CAPITAL_MIN_WORDS})")

    original = {th: themes[th]["score"] for th in THEMES}
    score_source, report_url, second = "10-K", "", None
    all_ev = list(ev.values())

    # ---- second look (sustainability report)
    sl_path = REPORTS / t / f"second_look_{run_id}.json"
    if sl_path.exists():
        sl = read_json(sl_path)
        second = {"status": sl.get("status"), "report_url": sl.get("report_url", ""),
                  "report_year": sl.get("report_year"), "themes": sl.get("themes_rescored", [])}
        if sl.get("status") == "scored":
            report = (REPORTS / t / sl["report_text_file"]).read_text(encoding="utf-8")
            sl_ev = check_evidence(sl["ai"]["evidence"],
                                  {"10-K": tenk, "sustainability_report": report})
            for r in sl_ev.values():
                r["id"] = "SL-" + r["id"]  # keep ids unique next to the 10-K ones
            sl_ev = {r["id"]: r for r in sl_ev.values()}
            for th in sl["themes_rescored"]:
                th_in = dict(sl["ai"]["themes"][th])
                th_in["evidence_ids"] = ["SL-" + i for i in th_in.get("evidence_ids", [])]
                new = check_theme(th, th_in, sl_ev)
                new["coverage_trigger"] = themes[th]["coverage_trigger"]
                new["score_from_10k"] = original[th]
                new["rescored_from_report"] = True
                themes[th] = new
            all_ev += list(sl_ev.values())
            score_source = "10-K + sustainability report"
            report_url = sl.get("report_url", "")
            if sl.get("domain_note"):
                flags.append(sl["domain_note"])

    # ---- flags for humans
    failed_ev = [e for e in all_ev if not e["verified"]]
    for e in failed_ev:
        flag_human(t, step, e["problem"],
                   f"{e['id']} ({e['source_doc']}): {str(e.get('quote'))[:300]}")
    for e in all_ev:
        if e["verified"] and e["figures_removed"]:
            flags.append(f"{e['id']}: figure(s) not found near the quote, removed: "
                         f"{', '.join(e['figures_removed'])}")
            flag_human(t, step, "figure_check_failed",
                       f"{e['id']}: removed {', '.join(e['figures_removed'])} from "
                       f"'{e.get('indicators_original', '')[:200]}'")
        for w in e.get("warnings", []):
            flags.append(f"{e['id']}: {w}")
    for th in THEMES:
        for f in themes[th]["flags"]:
            if not f.startswith("cites non-current"):
                flag_human(t, step, f"{th}: {f.split(':')[0]}", f)
            flags.append(f"{th}: {f}")
    if failed_ev:
        flags.insert(0, f"quote_check_failed on {len(failed_ev)} evidence record(s)")
    still_insufficient = [th for th in COVERAGE_BACKUP_THEMES
                          if themes[th]["coverage"] == "insufficient"
                          and not themes[th].get("rescored_from_report")]
    if still_insufficient and second and second["status"] != "scored":
        flags.append(f"coverage insufficient ({', '.join(still_insufficient)}); "
                     f"second look: {second['status']}")

    scores = {th: themes[th]["score"] for th in THEMES}
    out = {
        "ticker": t,
        "company_name": rec["company_name"],
        "sector": rec["sector"],
        "run_id": run_id,
        "rubric_version": rec.get("rubric_version", 1),
        "model": rec["model"],
        "sections": rec["sections"],
        "filing_date": rec["filing_date"],
        "fiscal_year": rec["fiscal_year"],
        "accession_number": rec["accession_number"],
        "scored_at": rec["scored_at"],
        "themes": themes,
        "scores": scores,
        "coverage": {th: themes[th]["coverage"] for th in THEMES},
        "final_score": final_score(scores),
        "original_10k_scores": original if second and second["status"] == "scored" else None,
        "original_10k_final": final_score(original) if second and second["status"] == "scored" else None,
        "evidence": all_ev,
        "notes": rec["ai"].get("notes", ""),
        "quote_check_status": "failed" if failed_ev else "passed",
        "flags": flags,
        "human_capital_words": info.get("human_capital_words", 0),
        "score_source": score_source,
        "report_url": report_url,
        "second_look": second,
    }
    write_json(VERIFIED / f"{t}_{run_id}.json", out)
    log("verify", f"{t}: final {out['final_score']} " + ", ".join(
        f"{th} {scores[th]}{'*' if out['coverage'][th] == 'insufficient' else ''}"
        for th in THEMES) + f"; quotes {out['quote_check_status']}; {len(flags)} flag(s); "
        f"source {score_source}")
    return out


def verify_run(run_id):
    files = sorted(SCORES.glob(f"*_{run_id}.json"))
    done = 0
    for path in files:
        rec = read_json(path)
        if rec.get("run_id") != run_id:
            continue
        if str(rec.get("rubric_version", 1)) != RUBRIC_VERSION:
            log("verify", f"{path.name}: made with an older rubric "
                          f"(v{rec.get('rubric_version', 1)}), skipping")
            continue
        verify_record(rec, run_id)
        done += 1
    if not done:
        log("verify", f"No Rubric v{RUBRIC_VERSION} score files found for run '{run_id}'.")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", default=cfg["run_id"])
    args = p.parse_args()
    verify_run(args.run_id)


if __name__ == "__main__":
    main()
