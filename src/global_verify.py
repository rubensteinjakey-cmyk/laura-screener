"""Global step 4: check every quote, apply the rubric's evidence rules and
caps, the red-flag penalty and the half-weight second look. No AI usage.

Same checks as the US run (verify.py): every quote must be found word for
word in the text the AI was given (for a non-English source, the
ORIGINAL-language quote is what's checked), figures must sit near their
quote, caps by kind, double counting, coverage backup (people discussion
under 150 words -> Community and Cross-cultural insufficient).

Red flags: each needs a verified quote; one without is removed (and logged).
  red_flag_score = red_flag_penalty (config, 10) x number of distinct red
  flags, final = max(0, alignment - red_flag_score).
Second look: a standalone sustainability report (global_second_look.py)
counts at half weight, exactly like the US run (report_weight in config).

Writes data/global/verified/{ticker}_{run_id}.json.

Run:  python src/global_verify.py [--run-id global]
"""

import argparse

from ai import THEMES
from common import load_config, log, read_json, write_json
from extract import PEOPLE_TERMS, word_count
from global_common import (G_REPORTS, G_SCORES, G_SECTIONS, G_VERIFIED, gclear, gflag)
from verify import (COVERAGE_BACKUP_THEMES, HUMAN_CAPITAL_MIN_WORDS, RUBRIC_VERSION, blend,
                    check_evidence, check_theme, final_score)

STEP = "global_verify"


OUTSIDE = {"court", "regulator", "outside_body"}


def check_red_flags(red_flags, sources):
    """Keep only red flags whose quote is found word for word in the source.
    Rule 4 (Oct 23): a rights finding counts only if a court, regulator or
    other outside body found it (or it's a fine/settlement); cases the
    company substantiated itself are set aside as "internal" (no points).
    Returns (kept, removed, internal): lists of red-flag records."""
    recs = [dict(r, source_doc=r.get("source_doc") or "filing") for r in red_flags]
    checked = check_evidence(recs, sources, default_doc="filing")
    kept, removed, internal = [], [], []
    for r in checked.values():
        if not r["verified"]:
            removed.append(r)
        elif r["flag"] == "rights_finding" and r.get("found_by") not in OUTSIDE:
            internal.append(r)
        else:
            kept.append(r)
    return kept, removed, internal


def internal_note(internal):
    """One line for values_notes about internally substantiated cases."""
    if not internal:
        return ""
    kinds = sorted({(r.get("case_type") or "").strip().lower() for r in internal}
                   - {"", "unspecified", "none", "n/a", "unknown"})
    what = " / ".join(kinds) if kinds else "discrimination, harassment or human-rights"
    return f"Discloses internally substantiated {what} cases; not a red flag under the rule."


def red_flag_result(kept, alignment, penalty):
    """Distinct flag types each cost `penalty` points; final floored at 0."""
    kinds = sorted({r["flag"] for r in kept})
    final = max(0.0, round(alignment - penalty * len(kinds), 1))
    return kinds, round(alignment - final, 1), final


def verify_record(rec, run_id, cfg):
    t = rec["ticker"]
    step = f"{STEP}:{run_id}"
    gclear(t, step)
    flags = []
    text = (G_SECTIONS / t / "verify.txt").read_text(encoding="utf-8")
    info = read_json(G_SECTIONS / t / "info.json")
    ev = check_evidence(rec["ai"]["evidence"], {"filing": text}, default_doc="filing")
    themes = {th: check_theme(th, rec["ai"]["themes"][th], ev) for th in THEMES}

    # coverage backup: the people discussion (people chapter or paragraphs)
    people_paras = [p for p in text.split("\n\n") if PEOPLE_TERMS.search(p)]
    people_words = max(info.get("people_words", 0), word_count("\n".join(people_paras)))
    for th in THEMES:
        ai_ins = themes[th]["coverage"] == "insufficient"
        code_ins = people_words < HUMAN_CAPITAL_MIN_WORDS and th in COVERAGE_BACKUP_THEMES
        themes[th]["coverage_trigger"] = ("ai+code" if ai_ins and code_ins else
                                          "ai" if ai_ins else "code" if code_ins else "")
        if code_ins:
            themes[th]["coverage"] = "insufficient"

    original = {th: themes[th]["score"] for th in THEMES}
    weight = float(cfg.get("report_weight", 0.5))
    source = f"{rec['form']} {rec.get('fiscal_year') or ''}".strip()
    score_source, full_report, second = source, None, None
    all_ev = list(ev.values())

    sl_path = G_REPORTS / t / f"second_look_{run_id}.json"
    if sl_path.exists():
        sl = read_json(sl_path)
        second = {"status": sl.get("status"), "report_url": sl.get("report_url", ""),
                  "report_year": sl.get("report_year"), "themes": sl.get("themes_rescored", [])}
        if sl.get("status") == "scored":
            report = (G_REPORTS / t / sl["report_text_file"]).read_text(encoding="utf-8")
            sl_ev = check_evidence(sl["ai"]["evidence"],
                                  {"filing": text, "sustainability_report": report})
            for r in sl_ev.values():
                r["id"] = "SL-" + r["id"]
            sl_ev = {r["id"]: r for r in sl_ev.values()}
            for th in sl["themes_rescored"]:
                th_in = dict(sl["ai"]["themes"][th])
                th_in["evidence_ids"] = ["SL-" + i for i in th_in.get("evidence_ids", [])]
                new = check_theme(th, th_in, sl_ev)
                new["coverage_trigger"] = themes[th]["coverage_trigger"]
                new["score_from_filing"] = original[th]
                new["score_from_report"] = new["score"]
                new["score"] = blend(original[th], new["score"], weight)
                new["report_weight"] = weight
                new["rescored_from_report"] = True
                themes[th] = new
            full_report = {th: themes[th].get("score_from_report", themes[th]["score"])
                           for th in THEMES}
            all_ev += list(sl_ev.values())
            score_source = (f"{source} + standalone sustainability report (half weight)"
                            if weight == 0.5 else
                            f"{source} + standalone sustainability report ({weight:g} weight)")

    # red flags
    kept, removed, internal = check_red_flags(rec["ai"].get("red_flags", []), {"filing": text})
    for r in removed:
        flags.append(f"red flag {r['id']} ({r['flag']}) removed: quote not found")
        gflag(t, step, "red flag quote failed (flag removed)",
              f"{r['id']} {r['flag']}: {str(r.get('quote'))[:250]}")

    for r in kept:
        if r["flag"] == "rights_finding":  # judgment call: always a quick human check
            gflag(t, step, "rights-finding red flag - quick check",
                  f"{r['id']}: {str(r.get('quote'))[:250]} | AI: {str(r.get('explanation'))[:250]}")
    failed_ev = [e for e in all_ev if not e["verified"]]
    for e in failed_ev:
        gflag(t, step, e["problem"], f"{e['id']} ({e['source_doc']}): {str(e.get('quote'))[:300]}")
    for e in all_ev:
        if e["verified"] and e["figures_removed"]:
            flags.append(f"{e['id']}: figure(s) not found near the quote, removed: "
                         f"{', '.join(e['figures_removed'])}")
    for th in THEMES:
        for f in themes[th]["flags"]:
            flags.append(f"{th}: {f}")
    if failed_ev:
        flags.insert(0, f"quote_check_failed on {len(failed_ev)} evidence record(s)")
    still = [th for th in COVERAGE_BACKUP_THEMES if themes[th]["coverage"] == "insufficient"
             and not themes[th].get("rescored_from_report")]
    if still and second and second["status"] != "scored":
        flags.append(f"coverage insufficient ({', '.join(still)}); second look: {second['status']}")
    notes_extra = list(info.get("notes", []))

    scores = {th: themes[th]["score"] for th in THEMES}
    alignment = final_score(scores)
    kinds, deducted, final = red_flag_result(kept, alignment, int(cfg.get("red_flag_penalty", 10)))
    out = {
        "ticker": t, "company_name": rec["company_name"], "country": rec["country"],
        "sector": rec["sector"], "run_id": run_id, "rubric_version": rec["rubric_version"],
        "model": rec["model"], "kind": rec["kind"], "form": rec["form"],
        "fiscal_year": rec.get("fiscal_year"), "url": rec.get("url", ""),
        "english": rec.get("english", True), "words_sent": rec["words_sent"],
        "themes": themes, "scores": scores,
        "coverage": {th: themes[th]["coverage"] for th in THEMES},
        "alignment_score": alignment, "red_flags": kept, "red_flags_removed": removed,
        "internal_findings": internal, "internal_note": internal_note(internal),
        "red_flag_kinds": kinds, "red_flag_score": deducted, "final_score": final,
        "original_filing_scores": original if full_report else None,
        "full_report_scores": full_report,
        "evidence": all_ev, "notes": rec["ai"].get("notes", ""), "extract_notes": notes_extra,
        "quote_check_status": "failed" if failed_ev else "passed", "flags": flags,
        "people_words": people_words, "score_source": score_source, "second_look": second,
    }
    write_json(G_VERIFIED / f"{t}_{run_id}.json", out)
    log(STEP, f"{t}: alignment {alignment} - red flags {deducted} ({', '.join(kinds) or 'none'}) "
              f"= final {final}; " + ", ".join(
                  f"{th} {scores[th]}{'*' if out['coverage'][th] == 'insufficient' else ''}"
                  for th in THEMES) + f"; quotes {out['quote_check_status']}; source {score_source}")
    return out


def verify_run(run_id, cfg, tickers=None):
    for path in sorted(G_SCORES.glob(f"*_{run_id}.json")):
        rec = read_json(path)
        if rec.get("run_id") != run_id or str(rec.get("rubric_version")) != RUBRIC_VERSION:
            continue
        if tickers and rec["ticker"].upper() not in tickers:
            continue
        verify_record(rec, run_id, cfg)


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", default=cfg.get("global_run_id", "global"))
    p.add_argument("--tickers", default="")
    args = p.parse_args()
    tick = {x.strip().upper() for x in args.tickers.split(",") if x.strip()}
    verify_run(args.run_id, cfg, tick or None)


if __name__ == "__main__":
    main()
