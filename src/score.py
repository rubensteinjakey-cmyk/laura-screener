"""Step 5: AI scoring. Uses Claude subscription usage.

For each company: send the rubric + the extracted section text to Claude
(via `claude -p`), get JSON back, and save it immediately to
data/scores/{TICKER}_{run_id}.json. If that file already exists the company
is skipped, so you can stop (Ctrl+C, usage limit...) and just run the same
command again to continue.

Run:  python src/score.py --start 1 --end 66
      python src/score.py --tickers JPM --run-id repeat1
      python src/score.py --sections item1,item7 --run-id test_item7
"""

import argparse
from datetime import datetime

from ai import THEMES, ClaudeError, check_themes, parse_json_reply, run_claude
from common import (ROOT, SCORES, SECTIONS, add_batch_args, clear_human_flags,
                    flag_human, load_config, log, read_json, select_companies,
                    write_json)

SECTION_NAMES = {"item1": "Item 1", "item7": "Item 7"}

PROMPT = """Score this company against the rubric below. Read the rubric carefully, including every trap.

<rubric>
{rubric}
</rubric>

Company: {company_name} (ticker {ticker}), sector: {sector}
Sections provided: {sections_list}

Instructions:
- Use ONLY the filing text below. Ignore anything you know about the company from elsewhere.
- Every quote must be copied exactly, character for character, from the filing text below: one continuous passage, no "..." and no changes. Keep each under about 40 words. Give 1-3 quotes for any score of 1 or higher, and the strongest evidence first.
- Use an empty quotes list for a score of 0.
- level_reason: one sentence naming the ladder level matched.
- Set "sections_read" to {sections_json}.
- Reply with ONE JSON object in exactly the rubric's output format, and nothing else (no explanation, no markdown).

{filing_text}
"""

RETRY_NOTE = """

IMPORTANT: your previous reply could not be used ({error}). Reply again with ONLY the JSON object in the exact format, nothing else."""


def build_prompt(company, sections, cfg):
    rubric = (ROOT / "rubric.md").read_text(encoding="utf-8")
    parts = []
    for s in sections:
        txt = (SECTIONS / company["ticker"] / f"{s}.txt").read_text(encoding="utf-8")
        parts.append(f'<filing_section name="{SECTION_NAMES[s]}">\n{txt.strip()}\n</filing_section>')
    filing_text = "\n\n".join(parts)
    names = [SECTION_NAMES[s] for s in sections]
    prompt = PROMPT.format(
        rubric=rubric, company_name=company["company_name"], ticker=company["ticker"],
        sector=company["sector"], sections_list=", ".join(names),
        sections_json=str(names).replace("'", '"'), filing_text=filing_text)
    return prompt, len(filing_text)


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
    empty = [s for s in sections if info["sections"][s]["words"] == 0]
    if empty:
        log("score", f"{t}: section(s) {empty} are empty - not scoring")
        flag_human(t, "score", "section text empty, not scored", ",".join(empty))
        return

    prompt, filing_chars = build_prompt(company, sections, cfg)
    log("score", f"{t}: sending {len(prompt):,} characters "
                 f"(~{len(prompt) // 4:,} tokens) to {cfg['model']}")
    clear_human_flags(t, "score")

    attempts, result, last_err = [], None, ""
    for attempt in (1, 2):  # first try + one retry if the JSON is invalid
        p = prompt if attempt == 1 else prompt + RETRY_NOTE.format(error=last_err)
        try:
            reply, run_info = run_claude(p, cfg)
        except ClaudeError as e:
            last_err = str(e)
            attempts.append({"attempt": attempt, "error": last_err})
            log("score", f"{t}: attempt {attempt} failed: {last_err}")
            if "limit" in last_err.lower():
                raise  # usage limit: stop the whole run, resume later
            continue
        attempts.append({"attempt": attempt, "raw_reply": reply, **run_info})
        try:
            result = parse_json_reply(reply)
            check_themes(result)
            break
        except (ValueError, KeyError) as e:
            last_err = f"invalid JSON: {e}"
            result = None
            log("score", f"{t}: attempt {attempt} gave {last_err}")

    if result is None:
        flag_human(t, "score", "AI reply failed or was invalid twice", last_err[:300])
        return

    record = {
        "ticker": t,
        "company_name": company["company_name"],
        "sector": company["sector"],
        "run_id": run_id,
        "model": cfg["model"],
        "effort": cfg.get("effort"),
        "sections": sections,
        "accession_number": info["accession_number"],
        "filing_date": info["filing_date"],
        "fiscal_year": info["fiscal_year"],
        "scored_at": datetime.now().isoformat(timespec="seconds"),
        "prompt_chars": len(prompt),
        "filing_chars": filing_chars,
        "approx_prompt_tokens": len(prompt) // 4,
        "ai": {
            "themes": {th: result["themes"][th] for th in THEMES},
            "thin_filing": bool(result.get("thin_filing", False)),
            "sections_read": result.get("sections_read"),
        },
        "attempts": attempts,
    }
    write_json(out_path, record)  # saved right away, before the next company
    s = record["ai"]["themes"]
    log("score", f"{t}: saved. creativity {s['creativity']['score']}, community "
                 f"{s['community']['score']}, cross_cultural {s['cross_cultural']['score']}, "
                 f"entrepreneurship {s['entrepreneurship']['score']}, "
                 f"thin_filing {record['ai']['thin_filing']}")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    add_batch_args(p, cfg)
    p.add_argument("--run-id", default=cfg["run_id"])
    p.add_argument("--sections", default=",".join(cfg["sections"]),
                   help="e.g. item1 or item1,item7")
    args = p.parse_args()
    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    bad = [s for s in sections if s not in SECTION_NAMES]
    if bad:
        raise SystemExit(f"Unknown section(s) {bad}. Use item1 and/or item7.")
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
