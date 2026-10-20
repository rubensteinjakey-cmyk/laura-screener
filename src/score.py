"""Step 5: AI scoring with Rubric v2.1. Uses Claude subscription usage.

For each company: send the rubric + the extracted section text to Claude
(via `claude -p`), get JSON back (evidence records + 4 theme scores with
coverage), and save it immediately to data/scores/{TICKER}_{run_id}.json.
If that file already exists the company is skipped, so you can stop
(Ctrl+C, usage limit...) and just run the same command again to continue.

Sections (config.yaml `sections`, or --sections):
  item1                  Item 1 (Business) only
  item1,item7_targeted   Item 1 + only the MD&A paragraphs with target terms
  item1,item7            Item 1 + the full MD&A

Run:  python src/score.py --start 1 --end 66
      python src/score.py --tickers JPM --run-id jakey_repeat
      python src/score.py --sections item1,item7_targeted --run-id test_targeted
"""

import argparse
from datetime import datetime

from ai import THEMES, ClaudeError, check_reply, parse_json_reply, run_claude
from common import (ROOT, S1_SECTION_LABELS, SCORES, SECTION_LABELS, SECTIONS, add_batch_args,
                    clear_human_flags, flag_human, load_config, log, read_json,
                    select_companies, write_json)

PROMPT = """Score this company against the rubric below. Read the whole rubric carefully, including every trap, cap and rule.

<rubric>
{rubric}
</rubric>

Company: {company_name} (ticker {ticker}), sector: {sector}, fiscal year: {fiscal_year}
Sections provided: {sections_list}{form_note}

Instructions:
- Use ONLY the filing text below. Ignore anything you know about the company from elsewhere.
- List every qualifying activity as an evidence record (ids E1, E2, ...), as the rubric describes. Set "source_doc" to "10-K" for every record.
- For each theme a record supports, give its "kind" (only the allowed values in the rubric) and the "criterion" it meets.
- Each "quote" must be copied exactly, character for character, from the filing text below: one continuous passage that is a complete thought (normally a full sentence), no "..." and no changes, about 40 words max. Read the whole surrounding paragraph first so the excerpt doesn't mislead.
- Every figure in "indicators" must appear in the filing text in the same paragraph or table as the quote. Copy figures exactly as written there; don't compute, convert or round them.
- "section": the item and nearest heading, e.g. "Item 1 > Human Capital > Developing employees".{targeted_note}
- A score of 2 or higher must list at least one evidence id; a score of 1 may cite none if it rests only on general language, and the reason must say so. Don't list the same activity twice for one theme.
- Coverage: follow the rubric's strict definition of "insufficient" exactly. If you mark a theme insufficient, the reason must say which of the three conditions applies.
- "reason": one sentence naming the level matched.
- Set "sections_read" to {sections_json} and "fiscal_year" to {fiscal_year}.
- Reply with ONE JSON object in exactly the rubric's output format, and nothing else (no explanation, no markdown).

{filing_text}
"""

RUBRIC_VERSION = "2.1"

S1_NOTE = (
    "\n\nThis company has not filed a 10-K yet, so the text below comes from its "
    "{form} registration statement (filed {filing_date}): its \"Business\" and "
    "\"Management's Discussion and Analysis\" sections. Treat it exactly as you would "
    "a 10-K. Wherever the rubric or these instructions say \"10-K\", read it as this "
    "filing; \"source_doc\": \"10-K\" means this filing. In \"section\", use e.g. "
    "\"S-1 Business > Human Capital Resources\".")
MIN_ITEM1_WORDS = 300  # below this, Item 1 extraction failed

TARGETED_NOTE = (
    "\n- The Item 7 text is NOT the full MD&A: it is only the MD&A paragraphs that "
    "mention terms such as foundation, community, education, small business, "
    "artists or accessibility. Treat it as excerpts.")

RETRY_NOTE = """

IMPORTANT: your previous reply could not be used ({error}). Reply again with ONLY the JSON object in the exact format, nothing else."""


def build_prompt(company, sections, fiscal_year, info=None):
    rubric = (ROOT / "rubric.md").read_text(encoding="utf-8")
    form = (info or {}).get("form", "10-K")
    labels = S1_SECTION_LABELS if form.startswith("S-1") else SECTION_LABELS
    parts = []
    for s in sections:
        txt = (SECTIONS / company["ticker"] / f"{s}.txt").read_text(encoding="utf-8")
        parts.append(f'<filing_section name="{labels[s]}">\n{txt.strip()}\n</filing_section>')
    filing_text = "\n\n".join(parts)
    names = [labels[s] for s in sections]
    form_note = (S1_NOTE.format(form=form, filing_date=info["filing_date"])
                 if form.startswith("S-1") else "")
    prompt = PROMPT.format(
        rubric=rubric, company_name=company["company_name"], ticker=company["ticker"],
        sector=company["sector"], fiscal_year=fiscal_year, sections_list=", ".join(names),
        sections_json=str(names).replace("'", '"'), filing_text=filing_text,
        targeted_note=TARGETED_NOTE if "item7_targeted" in sections else "",
        form_note=form_note)
    return prompt, len(filing_text)


def ask_claude(prompt, cfg, check, t, step):
    """Call Claude, retry once if the reply is unusable.
    Returns (result or None, attempts, last_error). Usage limits raise."""
    attempts, last_err = [], ""
    for attempt in (1, 2):
        p = prompt if attempt == 1 else prompt + RETRY_NOTE.format(error=last_err)
        try:
            reply, run_info = run_claude(p, cfg)
        except ClaudeError as e:
            last_err = str(e)
            attempts.append({"attempt": attempt, "error": last_err})
            log(step, f"{t}: attempt {attempt} failed: {last_err}")
            if "limit" in last_err.lower():
                raise  # usage limit: stop the whole run, resume later
            continue
        attempts.append({"attempt": attempt, "raw_reply": reply, **run_info})
        try:
            result = parse_json_reply(reply)
            check(result)
            return result, attempts, ""
        except (ValueError, KeyError) as e:
            last_err = f"invalid JSON: {e}"
            log(step, f"{t}: attempt {attempt} gave {last_err}")
    return None, attempts, last_err


def score_company(company, cfg, sections, run_id):
    t = company["ticker"]
    out_path = SCORES / f"{t}_{run_id}.json"
    if out_path.exists():
        log("score", f"{t}: already scored for run '{run_id}', skipping")
        return
    if not (SECTIONS / t / "info.json").exists():
        log("score", f"{t}: not extracted yet, skipping")
        return
    info = read_json(SECTIONS / t / "info.json")
    item1_words = info["sections"].get("item1", {}).get("words", 0)
    item1_failed = any(n.startswith("item1:") and "could not locate" in n
                       for n in info.get("notes", []))
    if item1_words < MIN_ITEM1_WORDS or item1_failed:
        # extraction failed (e.g. the 10-K only has a cross-reference index):
        # don't score on almost nothing
        log("score", f"{t}: Item 1 extraction failed ({item1_words} words"
                     f"{', only a cross-reference' if item1_failed else ''}) - not scoring")
        flag_human(t, "score", f"Item 1 extraction failed ({item1_words} words); not scored",
                   "; ".join(info.get("notes", []))[:300])
        return

    prompt, filing_chars = build_prompt(company, sections, info["fiscal_year"], info)
    log("score", f"{t}: sending {len(prompt):,} characters "
                 f"(~{len(prompt) // 4:,} tokens; filing text {filing_chars:,} chars) "
                 f"to {cfg['model']}")
    clear_human_flags(t, "score")

    result, attempts, last_err = ask_claude(prompt, cfg, check_reply, t, "score")
    if result is None:
        flag_human(t, "score", "AI reply failed or was invalid twice", last_err[:300])
        return

    record = {
        "ticker": t,
        "company_name": company["company_name"],
        "sector": company["sector"],
        "run_id": run_id,
        "rubric_version": RUBRIC_VERSION,
        "model": cfg["model"],
        "effort": cfg.get("effort"),
        "sections": sections,
        "accession_number": info["accession_number"],
        "form": info.get("form", "10-K"),
        "source_label": info.get("source_label", "10-K"),
        "source_note": info.get("source_note", ""),
        "filing_date": info["filing_date"],
        "fiscal_year": info["fiscal_year"],
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "prompt_chars": len(prompt),
        "filing_chars": filing_chars,
        "filing_words_sent": sum(info["sections"][s]["words"] for s in sections),
        "approx_prompt_tokens": len(prompt) // 4,
        "ai": {
            "evidence": result["evidence"],
            "themes": {th: result["themes"][th] for th in THEMES},
            "notes": result.get("notes", ""),
            "sections_read": result.get("sections_read"),
        },
        "attempts": attempts,
    }
    write_json(out_path, record)  # saved right away, before the next company
    s = record["ai"]["themes"]
    for th in THEMES:
        if s[th]["coverage"] == "insufficient":
            log("score", f"{t}: AI marked {th} INSUFFICIENT - reason: {s[th]['reason']}")
    log("score", f"{t}: saved. " + ", ".join(
        f"{th} {s[th]['score']}{'*' if s[th]['coverage'] == 'insufficient' else ''}"
        for th in THEMES) + f" ({len(result['evidence'])} evidence records; "
        "* = insufficient coverage)")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    add_batch_args(p, cfg)
    p.add_argument("--run-id", default=cfg["run_id"])
    p.add_argument("--sections", default=",".join(cfg["sections"]),
                   help="item1 | item1,item7_targeted | item1,item7")
    args = p.parse_args()
    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    bad = [s for s in sections if s not in SECTION_LABELS]
    if bad or "item1" not in sections or ("item7" in sections and "item7_targeted" in sections):
        raise SystemExit("Use --sections item1, item1,item7_targeted or item1,item7")
    companies = select_companies(args)
    log("score", f"Scoring {len(companies)} companies, run '{args.run_id}', "
                 f"sections {sections}, model {cfg['model']}")
    for c in companies:
        try:
            score_company(c, cfg, sections, args.run_id)
        except ClaudeError as e:
            log("score", f"STOPPING: {e}. Run the same command again later to resume.")
            break
        except Exception as e:
            log("score", f"{c['ticker']}: ERROR {e}")
            flag_human(c["ticker"], "score", "scoring error", str(e)[:300])


if __name__ == "__main__":
    main()
