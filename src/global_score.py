"""Global step 3: AI scoring with Rubric v2.1 (same rubric, model setting and
retry rule as the US run). Uses Claude subscription usage.

Differences from the US prompt:
  - the source is a 20-F or an annual report (sections in data/global/sections);
  - sustainability content inside the official annual report counts at full
    weight, like any other part of the report;
  - a non-English source is quoted word for word in the ORIGINAL language,
    with an English translation in "quote_en";
  - the AI also lists RED FLAGS, each with a word-for-word quote (checked by
    code in global_verify.py; outside knowledge never counts).
Saved right away to data/global/scores/{ticker}_{run_id}.json (resumable).

Run:  python src/global_score.py --tickers GMAB [--run-id global]
"""

import argparse
from datetime import datetime

from ai import THEMES, ClaudeError, check_reply
from common import ROOT, load_config, log, read_json, write_json
from global_common import (G_SCORES, G_SECTIONS, add_global_args, gclear, gflag,
                           load_global_companies, skipped)
from score import ask_claude
from verify import RUBRIC_VERSION

STEP = "global_score"
RED_FLAGS = {
    "tobacco": "tobacco products as a core business",
    "thermal_coal": "thermal coal mining or coal-fired power as a significant business",
    "weapons": "weapons manufacturing",
    "rights_finding": ("a disclosed finding, fine or settlement during the reporting period "
                       "for discrimination, harassment, forced or child labor, or serious "
                       "human-rights abuses"),
}

PROMPT = """Score this company against the rubric below. Read the whole rubric carefully, including every trap, cap and rule.

<rubric>
{rubric}
</rubric>

Company: {company_name} (ticker {ticker}), {country}, sector: {sector}, fiscal year: {fiscal_year}
Source: {source_desc}
Sections provided: {sections_list}

Instructions:
- Use ONLY the source text below. Ignore anything you know about the company from elsewhere.
- Wherever the rubric says "10-K" or "filing", read it as this source document. Sustainability or ESG content that is part of this official report (including an EU-style sustainability statement) counts at full weight, like any other part of it.
- List every qualifying activity as an evidence record (ids E1, E2, ...), as the rubric describes. Set "source_doc" to "filing" for every record.
- For each theme a record supports, give its "kind" (only the allowed values in the rubric) and the "criterion" it meets.
- Each "quote" must be copied exactly, character for character, from the source text below: one continuous passage that is a complete thought (normally a full sentence), no "..." and no changes, about 40 words max. PDF text may break a sentence across lines: quote the full sentence anyway, never a fragment cut at a line break. Read the whole surrounding paragraph first so the excerpt doesn't mislead.{language_rule}
- Every figure in "indicators" must appear in the source text in the same paragraph or table as the quote. Copy figures exactly as written there; don't compute, convert or round them.
- "section": the section and nearest heading, e.g. "Annual report > Our people > Training".
- A score of 2 or higher must list at least one evidence id; a score of 1 may cite none if it rests only on general language, and the reason must say so. Don't list the same activity twice for one theme.
- Coverage: follow the rubric's strict definition of "insufficient" exactly. If you mark a theme insufficient, the reason must say which of the three conditions applies.
- "reason": one sentence naming the level matched.
- RED FLAGS: list in "red_flags" each of these that the source text ITSELF shows, with a word-for-word quote (same quote rules as evidence; ids R1, R2, ...). "flag" must be one of:
    "tobacco" = tobacco products as a core business;
    "thermal_coal" = thermal coal mining or coal-fired power as a significant business;
    "weapons" = weapons manufacturing;
    "rights_finding" = a disclosed finding, fine or settlement during the reporting period for discrimination, harassment, forced or child labor, or serious human-rights abuses.
  Only what the text states (a policy against child labor, an audit with no finding, or a small coal exposure in a loan book is NOT a red flag). If none apply, "red_flags": [].
- Set "sections_read" to {sections_json} and "fiscal_year" to {fiscal_year}.
- Reply with ONE JSON object in exactly the rubric's output format, plus the "red_flags" list, and nothing else (no explanation, no markdown):
  "red_flags": [{{"id": "R1", "flag": "tobacco", "quote": "", {quote_en_field}"section": "", "explanation": ""}}]

{source_text}
"""

LANGUAGE_RULE = (
    "\n- This source is NOT in English. Copy each \"quote\" word for word in the ORIGINAL "
    "language (that is what the code checks), and put your English translation in a "
    "separate field \"quote_en\" (same for red flags). Never translate inside \"quote\".")


def check_global_reply(obj):
    check_reply(obj, THEMES, {"filing"})
    rf = obj.get("red_flags", [])
    if not isinstance(rf, list):
        raise ValueError("'red_flags' must be a list")
    for i, r in enumerate(rf):
        if not isinstance(r, dict):
            raise ValueError(f"red_flags[{i}] is not an object")
        if r.get("flag") not in RED_FLAGS:
            raise ValueError(f"red_flags[{i}].flag must be one of {sorted(RED_FLAGS)}")
        for f in ("id", "quote"):
            if not isinstance(r.get(f), str) or not r[f].strip():
                raise ValueError(f"red_flags[{i}].{f} must be text")


def build_prompt(company, info):
    rubric = (ROOT / "rubric.md").read_text(encoding="utf-8")
    text = (G_SECTIONS / company["ticker"] / "sent.txt").read_text(encoding="utf-8")
    names = list(info["sections"])
    if info["kind"] == "20-F":
        desc = f"Form 20-F annual report filed with the SEC (fiscal {info['fiscal_year']})"
    else:
        desc = (f"the company's official annual report (fiscal {info['fiscal_year']})"
                + ("" if info.get("english", True) else ", NOT in English"))
    english = info.get("english", True)
    return PROMPT.format(
        rubric=rubric, company_name=company["company_name"], ticker=company["ticker"],
        country=company["country"], sector=company["sector"],
        fiscal_year=info.get("fiscal_year"), source_desc=desc,
        sections_list=", ".join(names), sections_json=str(names).replace("'", '"'),
        language_rule="" if english else LANGUAGE_RULE,
        quote_en_field="" if english else '"quote_en": "", ',
        source_text=text)


def score_company(company, cfg, run_id):
    t = company["ticker"]
    out_path = G_SCORES / f"{t}_{run_id}.json"
    if out_path.exists():
        log(STEP, f"{t}: already scored for run '{run_id}', skipping")
        return
    if t.upper() in skipped():
        return
    info_path = G_SECTIONS / t / "info.json"
    if not info_path.exists():
        log(STEP, f"{t}: not extracted yet (or not cleanly), skipping")
        return
    info = read_json(info_path)
    prompt = build_prompt(company, info)
    log(STEP, f"{t}: sending {info['words_sent']:,} words ({len(prompt):,} characters, "
              f"~{len(prompt) // 4:,} tokens incl. rubric) to {cfg['model']}")
    gclear(t, STEP)
    result, attempts, err = ask_claude(prompt, cfg, check_global_reply, t, STEP)
    if result is None:
        gflag(t, STEP, "AI reply failed or was invalid twice", err[:300])
        return
    record = {
        "ticker": t, "company_name": company["company_name"], "country": company["country"],
        "sector": company["sector"], "run_id": run_id, "rubric_version": RUBRIC_VERSION,
        "model": cfg["model"], "effort": cfg.get("effort"), "kind": info["kind"],
        "form": info["form"], "fiscal_year": info.get("fiscal_year"), "url": info.get("url"),
        "english": info.get("english", True), "words_sent": info["words_sent"],
        "prompt_chars": len(prompt), "approx_prompt_tokens": len(prompt) // 4,
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "ai": {"evidence": result["evidence"],
               "themes": {th: result["themes"][th] for th in THEMES},
               "red_flags": result.get("red_flags", []),
               "notes": result.get("notes", "")},
        "attempts": attempts,
    }
    write_json(out_path, record)  # saved right away
    s = record["ai"]["themes"]
    log(STEP, f"{t}: saved. " + ", ".join(
        f"{th} {s[th]['score']}{'*' if s[th]['coverage'] == 'insufficient' else ''}"
        for th in THEMES) + f"; {len(result['evidence'])} evidence, "
        f"{len(record['ai']['red_flags'])} red flag(s) (before checks)")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    add_global_args(p)
    p.add_argument("--run-id", default=cfg.get("global_run_id", "global"))
    args = p.parse_args()
    for c in load_global_companies(args.country, args.tickers):
        try:
            score_company(c, cfg, args.run_id)
        except ClaudeError as e:
            log(STEP, f"STOPPING: {e}. Run the same command again later to resume.")
            break
        except Exception as e:
            log(STEP, f"{c['ticker']}: ERROR {e}")
            gflag(c["ticker"], STEP, "scoring error", str(e)[:300])


if __name__ == "__main__":
    main()
