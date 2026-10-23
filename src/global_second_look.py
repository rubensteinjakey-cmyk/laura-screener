"""Global step 5: second look, same trigger and rules as the US run
(second_look.py). Uses some Claude usage.

Trigger: after global_verify.py, Community or Cross-cultural coverage is
"insufficient". Then a STANDALONE sustainability / impact / ESG report (not
the annual report already read) is looked for on the company's own website
(same domain and file-service rules), only its people / community /
education / inclusion chapters are read, and the insufficient theme(s) are
rescored from scratch. global_verify.py then counts the report at half
weight. No standalone report: the annual-report score stands (no penalty,
no bonus) and the company is listed in needs_human.csv.

Run:  python src/global_second_look.py [--run-id global]
      then python src/global_verify.py again
"""

import argparse
import io
import logging
import re
from datetime import datetime
from urllib.parse import urlparse

from pypdf import PdfReader

from ai import ClaudeError, check_reply, parse_json_reply, run_claude
from common import ROOT, load_config, log, read_json, write_json
from global_common import (G_DOCS, G_REPORTS, G_SECTIONS, G_VERIFIED, gclear, gflag,
                           load_global_companies)
from score import ask_claude
from second_look import (IR_FILE_HOSTS, MIN_REPORT_PAGES, SECOND_LOOK_THEMES, fetch_pdf,
                         link_on_page, registered_domain, relevant_text)
from verify import RUBRIC_VERSION

logging.getLogger("pypdf").setLevel(logging.ERROR)
STEP = "global_second_look"
MIN_YEAR = 2024

SEARCH_PROMPT = """Find the most recent STANDALONE sustainability, impact, ESG, or corporate responsibility report PDF published by {company_name} (stock ticker {ticker}, {country}) on the company's OWN website ({domain}). It must be from {min_year} or later, and it must be a separate report, NOT the annual report.

Use web search. Prefer the official English version. Prefer direct links to PDF files on the company's own domain. No third-party sites.
A report hosted on an investor-relations file service (for example q4cdn.com) is only usable if a page of the company's own website links to it: give that page in "found_on".

Reply with ONLY this JSON, nothing else:
{{"candidates": [{{"url": "https://...", "title": "...", "year": 2025, "found_on": "https://..."}}]}}
List up to 5 candidates, best first. If there is no separate report, reply {{"candidates": []}}."""

RESCORE_PROMPT = """Below is the scoring rubric, then {company_name}'s ({ticker}) {source_desc} sections, then chapters from its separate {year} sustainability/impact report (only the chapters about people, community, education and inclusion).

<rubric>
{rubric}
</rubric>

The {source_desc} alone didn't give enough information to judge these theme(s): {themes_list}.
Rescore ONLY {themes_list} FROM SCRATCH, using both texts together, with the same rubric (same ladders, rules, caps and traps). The new score can be higher, lower or the same as before. Use ONLY the text below.

Rules:
- List every qualifying activity for these theme(s) as an evidence record (ids E1, E2, ...).
- Each record must have "source_doc": "filing" if its quote comes from the {source_desc} text, or "sustainability_report" if it comes from the report text.
- For each theme a record supports, give its "kind" (only the allowed values in the rubric) and the "criterion" it meets.
- Each "quote" must be copied exactly, character for character, from that document, in its original language: one continuous passage, no "..." and no changes, about 40 words max. The report text has line breaks in the middle of sentences: quote the FULL sentence anyway. If the text is not English, add an English translation in "quote_en".
- Every figure in "indicators" must appear in the same paragraph or table as the quote. Copy figures exactly as written.
- A score of 1 or higher must list at least one evidence id. Don't list the same activity twice for one theme.

Reply with ONE JSON object and nothing else:
{{"evidence": [{{"id": "E1", "activity": "", "quote": "", "source_doc": "sustainability_report", "section": "", "status": "current", "indicators": "", "themes": {{"community": {{"kind": "", "criterion": ""}}}}}}],
 "themes": {themes_skeleton},
 "notes": ""}}

<filing_sections>
{filing}
</filing_sections>

<report_chapters>
{text}
</report_chapters>
"""


def own_domain(t):
    meta = read_json(G_DOCS / t / "meta.json")
    if meta.get("own_domain"):
        return meta["own_domain"]
    # 20-F: the web address the company gives most often in its own filing
    full = (G_SECTIONS / t / "full.txt").read_text(encoding="utf-8")
    hosts = re.findall(r"\b(?:https?://)?((?:www\.|investors?\.|ir\.)?[a-z0-9\-]+"
                       r"(?:\.[a-z0-9\-]+)*\.(?:com|org|net|co|ch|dk|id|sg|tw))\b", full, re.I)
    counts = {}
    for h in hosts:
        d = registered_domain(h)
        if d not in ("sec.gov", "xbrl.org", "fasb.org", "w3.org"):
            counts[d] = counts.get(d, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def find_report(company, cfg, lines):
    t = company["ticker"]
    dom = own_domain(t)
    lines.append(f"company domain: {dom or 'unknown'}")
    if not dom:
        return None
    ar_url = read_json(G_DOCS / t / "meta.json").get("url", "")
    reply, _ = run_claude(SEARCH_PROMPT.format(company_name=company["company_name"], ticker=t,
                                               country=company["country"], domain=dom,
                                               min_year=MIN_YEAR), cfg, tools="WebSearch")
    try:
        cands = parse_json_reply(reply).get("candidates", [])
    except ValueError:
        cands = []
    if not cands and re.search(r"permission|denied|not available|unable to (?:search|use)",
                               reply, re.I):
        raise RuntimeError(f"web search did not run: {reply[:300]}")
    lines.append(f"search suggested {len(cands)} candidate(s)")
    brand = dom.split(".")[0]
    for c in cands:
        url = str(c.get("url", ""))
        if url == ar_url or re.search(r"annual[-_ ]?report", url, re.I) and \
                not re.search(r"sustainab|esg|impact|responsib", url, re.I):
            lines.append(f"rejected (this is the annual report, not a standalone report): {url}")
            continue
        reg = registered_domain(urlparse(url).hostname or "")
        on_main = reg == dom
        if not (on_main or (len(brand) >= 3 and brand in reg.split(".")[0])):
            page = str(c.get("found_on") or "")
            preg = registered_domain(urlparse(page).hostname or "")
            if reg not in IR_FILE_HOSTS or not page or not (preg == dom or brand in preg):
                lines.append(f"rejected (not on {dom} or a brand-name domain): {url}")
                continue
            try:
                if not link_on_page(page, url, cfg):
                    lines.append(f"rejected (link not found on company page {page}): {url}")
                    continue
            except Exception as e:
                lines.append(f"rejected (couldn't read {page}: {e}): {url}")
                continue
        try:
            data = fetch_pdf(url, cfg)
            reader = PdfReader(io.BytesIO(data))
        except Exception as e:
            lines.append(f"rejected (download/read failed: {e}): {url}")
            continue
        years = [int(y) for y in re.findall(r"(?<!\d)(20[2-3]\d)(?!\d)", urlparse(url).path)]
        year = max(years) if years else (c.get("year") if isinstance(c.get("year"), int) else None)
        if not year or year < MIN_YEAR or len(reader.pages) < MIN_REPORT_PAGES:
            lines.append(f"rejected (year {year}, {len(reader.pages)} pages): {url}")
            continue
        text, chapters, method = relevant_text(reader)
        if not text:
            lines.append(f"rejected (no usable table of contents): {url}")
            continue
        folder = G_REPORTS / t
        (folder / f"report_{year}.pdf").write_bytes(data)
        lines.append(f"accepted {url} ({year}, {len(reader.pages)} pages)")
        return url, year, text, chapters, method, on_main
    return None


def second_look(company, cfg, run_id, themes):
    t = company["ticker"]
    folder = G_REPORTS / t
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / f"second_look_{run_id}.json"
    if out_path.exists():
        log(STEP, f"{t}: already done, skipping")
        return
    step = f"{STEP}:{run_id}"
    gclear(t, step)
    lines = []
    record = {"ticker": t, "run_id": run_id, "model": cfg["model"], "themes_rescored": themes,
              "done_at": datetime.now().isoformat(timespec="seconds")}
    found = find_report(company, cfg, lines)
    if not found:
        record.update(status="no standalone report found", log=lines)
        write_json(out_path, record)
        gflag(t, step, "no standalone sustainability report found (scored on the annual "
                       "report alone)", " | ".join(lines)[:500])
        log(STEP, f"{t}: no standalone report found")
        return
    url, year, text, chapters, method, on_main = found
    if not on_main:
        gflag(t, step, "report not on main domain - quick check", url)
    text_file = f"report_{year}_chapters.txt"
    (folder / text_file).write_text(text, encoding="utf-8")
    info = read_json(G_SECTIONS / t / "info.json")
    desc = "20-F" if info["kind"] == "20-F" else "annual report"
    skeleton = "{" + ", ".join(
        f'"{th}": {{"score": 0, "coverage": "sufficient", "evidence_ids": [], "reason": ""}}'
        for th in themes) + "}"
    prompt = RESCORE_PROMPT.format(
        company_name=company["company_name"], ticker=t, source_desc=desc, year=year,
        rubric=(ROOT / "rubric.md").read_text(encoding="utf-8"),
        themes_list=" and ".join(f'"{th}"' for th in themes), themes_skeleton=skeleton,
        filing=(G_SECTIONS / t / "sent.txt").read_text(encoding="utf-8"), text=text)
    log(STEP, f"{t}: rescoring {themes} from {url} (~{len(prompt) // 4:,} tokens)")
    result, attempts, err = ask_claude(
        prompt, cfg, lambda r: check_reply(r, themes, {"filing", "sustainability_report"}),
        t, STEP)
    if result is None:
        record.update(status="rescore failed", log=lines, error=err)
        write_json(out_path, record)
        gflag(t, step, "rescore reply invalid twice", err[:300])
        return
    record.update(status="scored", report_url=url, report_year=year, report_text_file=text_file,
                  chapters=chapters, chapter_method=method, prompt_chars=len(prompt),
                  ai={"evidence": result["evidence"],
                      "themes": {th: result["themes"][th] for th in themes},
                      "notes": result.get("notes", "")},
                  attempts=attempts, log=lines)
    write_json(out_path, record)
    log(STEP, f"{t}: " + ", ".join(f"{th} {result['themes'][th]['score']}" for th in themes)
              + " from the report (before checks and half weight)")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", default=cfg.get("global_run_id", "global"))
    p.add_argument("--tickers", default="")
    args = p.parse_args()
    want = {x.strip().upper() for x in args.tickers.split(",") if x.strip()}
    companies = {c["ticker"]: c for c in load_global_companies()}
    for f in sorted(G_VERIFIED.glob(f"*_{args.run_id}.json")):
        r = read_json(f)
        if str(r.get("rubric_version")) != RUBRIC_VERSION or (want and r["ticker"] not in want):
            continue
        themes = [th for th in SECOND_LOOK_THEMES if r["coverage"][th] == "insufficient"
                  and not r["themes"][th].get("rescored_from_report")]
        if not themes:
            continue
        try:
            second_look(companies[r["ticker"]], cfg, args.run_id, themes)
        except ClaudeError as e:
            log(STEP, f"STOPPING: {e}. Run again later to resume.")
            break
        except Exception as e:
            log(STEP, f"{r['ticker']}: ERROR {e}")
            gflag(r["ticker"], f"{STEP}:{args.run_id}", "second look error", str(e)[:300])


if __name__ == "__main__":
    main()
