"""Step 9: second look when evidence is insufficient. Uses some Claude usage.

Trigger (the same for every company): the verified result has coverage
"insufficient" on Community or Cross-cultural.
  1. Find the latest sustainability / impact / ESG report. Claude (with web
     search turned on, nothing else) suggests PDF links; the CODE accepts one
     only if
       - its web address contains the company's brand name, taken from the
         main website the company lists in its own 10-K (e.g. wellsfargo.com
         -> "wellsfargo", so wellsfargomedia.com is accepted). Any report NOT
         on the main domain is listed in needs_human.csv for a quick check.
       - the PDF says 2024 or later on its first pages.
     A human can also put a link in data/reports/manual_urls.csv
     (columns: ticker,url) and it will be used instead of searching.
  2. Read only the relevant chapters (people, community, education,
     inclusion), found through the PDF's bookmarks or its table of contents.
  3. Rescore ONLY the insufficient theme(s) FROM SCRATCH, using the 10-K
     sections and the report together, with the same rubric. Each evidence
     record says which document it came from (source_doc "10-K" or
     "report"), and verify.py checks its quote against that document.
  4. Scores can go up, down or stay the same. verify.py keeps the original
     10-K scores too.
  5. If no valid report is found: keep the 10-K score, coverage stays
     insufficient, and the company goes in needs_human.csv ("no report found").

Run:  python src/second_look.py            (run_id from config)
"""

import argparse
import csv
import io
import re
from datetime import datetime
from urllib.parse import urlparse

import requests
from pypdf import PdfReader

from ai import ClaudeError, check_reply, parse_json_reply, run_claude
from common import (REPORTS, ROOT, SECTION_LABELS, SECTIONS, VERIFIED,
                    clear_human_flags, flag_human, load_config, log, read_json,
                    write_json)
from score import ask_claude
from verify import RUBRIC_VERSION

SECOND_LOOK_THEMES = ["community", "cross_cultural"]

MIN_YEAR = 2024
MAX_PDF_BYTES = 80_000_000
MAX_CHAPTER_WORDS = 40_000
MIN_REPORT_PAGES = 8  # shorter PDFs are usually link lists / indexes, not reports
CHAPTER_WORDS = re.compile(
    r"people|employee|workforce|talent|human capital|our team|colleague|"
    r"communit|education|learning|school|philanthrop|giving|volunteer|"
    r"inclusi|diversity|belonging|opportunit|accessib|social|culture|"
    r"engagement|well-?being|impact", re.I)

SEARCH_PROMPT = """Find the most recent sustainability, impact, ESG, or corporate responsibility report PDF published by {company_name} (stock ticker {ticker}) on the company's OWN website. It must be from {min_year} or later.

Use web search. Prefer direct links to PDF files on the company's own domain. Do not suggest third-party sites (ESG data aggregators, news sites, responsibilityreports.com, etc.).

Reply with ONLY this JSON, nothing else:
{{"candidates": [{{"url": "https://...", "title": "...", "year": 2025}}]}}
List up to 5 candidates, best first. If you find nothing, reply {{"candidates": []}}."""

RESCORE_PROMPT = """Below is the scoring rubric, then {company_name}'s ({ticker}) 10-K sections, then chapters from its {year} sustainability/impact report (only the chapters about people, community, education and inclusion).

<rubric>
{rubric}
</rubric>

The 10-K alone didn't give enough information to judge these theme(s): {themes_list}.
Rescore ONLY {themes_list} FROM SCRATCH, using the 10-K text and the report text together, with the same rubric (same ladders, rules, caps and traps). The new score can be higher, lower or the same as before. Use ONLY the text below.

Rules:
- List every qualifying activity for these theme(s) as an evidence record (ids E1, E2, ...).
- Each record must have "source_doc": "10-K" if its quote comes from the 10-K text, or "sustainability_report" if it comes from the report text.
- For each theme a record supports, give its "kind" (only the allowed values in the rubric) and the "criterion" it meets.
- Each "quote" must be copied exactly, character for character, from that document: one continuous passage, no "..." and no changes, about 40 words max. The report text has line breaks in the middle of sentences: quote the FULL sentence anyway (it may run across several lines), never a fragment cut at a line break.
- Every figure in "indicators" must appear in the same paragraph or table as the quote. Copy figures exactly as written; don't compute or convert them.
- A score of 1 or higher must list at least one evidence id. Don't list the same activity twice for one theme.
- Report figures describe the report's year; still only "current" activities raise a score.

Reply with ONE JSON object and nothing else:
{{"evidence": [{{"id": "E1", "activity": "", "quote": "", "source_doc": "sustainability_report", "section": "", "status": "current", "indicators": "", "themes": {{"community": {{"kind": "", "criterion": ""}}}}}}],
 "themes": {themes_skeleton},
 "notes": ""}}

<tenk_sections>
{tenk}
</tenk_sections>

<report_chapters>
{text}
</report_chapters>
"""

# ------------------------------------------------------------- own domains


def registered_domain(host):
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def company_domains(ticker):
    """Web domains the company itself mentions in its 10-K (e.g. in
    'Available Information'). These are trusted as the company's own."""
    text = (SECTIONS / ticker / "full_10k.txt").read_text(encoding="utf-8")
    hosts = re.findall(r"\b(?:https?://)?((?:www\.|investor\.|ir\.)?[a-z0-9\-]+"
                       r"(?:\.[a-z0-9\-]+)*\.(?:com|org|net|co|us|io|ai))\b", text, re.I)
    ignore = {"sec.gov", "xbrl.org", "fasb.org", "w3.org"}
    doms = {}
    for h in hosts:
        d = registered_domain(h)
        if d not in ignore:
            doms[d] = doms.get(d, 0) + 1
    return doms


def brand_check(url, domains):
    """Option (b): accept a link whose domain contains the company's brand
    name. The brand is the main domain the 10-K mentions most, minus its
    ending (jpmorganchase.com -> "jpmorganchase").
    Returns (accepted, on_main_domain, main_domain)."""
    if not domains:
        return False, False, ""
    main = max(domains, key=domains.get)
    brand = main.split(".")[0]
    reg = registered_domain(urlparse(url).hostname or "")
    if reg in domains:
        return True, reg == main, main
    return len(brand) >= 4 and brand in reg.split(".")[0], False, main


# ------------------------------------------------------------- PDF handling


def fetch_pdf(url, cfg):
    ua = cfg["user_agent"]
    r = requests.get(url, timeout=120, stream=True, headers={
        "User-Agent": f'Mozilla/5.0 (research screener; {ua["name"]} {ua["email"]})'})
    r.raise_for_status()
    data = b""
    for chunk in r.iter_content(1 << 16):
        data += chunk
        if len(data) > MAX_PDF_BYTES:
            raise ValueError("PDF too large")
    if not data.startswith(b"%PDF"):
        raise ValueError("link is not a PDF")
    return data


def pdf_year(reader):
    """Latest plausible year printed on the first 3 pages."""
    years = []
    for page in reader.pages[:3]:
        txt = page.extract_text() or ""
        years += [int(y) for y in re.findall(r"\b(20[1-3]\d)\b", txt)]
    years = [y for y in years if y <= datetime.now().year + 1]
    return max(years) if years else None


def chapters_from_outline(reader):
    """Use the PDF's bookmarks (its clickable table of contents)."""
    try:
        outline = reader.outline
    except Exception:
        return []
    top = []
    for item in outline:
        if isinstance(item, list):
            continue
        try:
            top.append((item.title, reader.get_destination_page_number(item)))
        except Exception:
            pass
    top.sort(key=lambda x: x[1])
    return _ranges(top, len(reader.pages))


def chapters_from_toc_page(reader):
    """Read a printed table of contents ('Our People ..... 34') from the
    first pages, and map printed page numbers to PDF pages."""
    entries = []
    for page in reader.pages[:6]:
        for line in (page.extract_text() or "").split("\n"):
            m = re.match(r"^\s*(\d{1,3})\s+([A-Za-z].{2,70}?)\s*$", line) or \
                re.match(r"^\s*([A-Za-z].{2,70}?)[\s\.]{2,}(\d{1,3})\s*$", line)
            if not m:
                continue
            a, b = m.groups()
            title, num = (b, a) if a.isdigit() else (a, b)
            entries.append((title.strip(), int(num)))
    if len(entries) < 3:
        return []
    # printed page N is usually PDF page N-1 (+ an offset); find the offset by
    # looking for each title on nearby pages
    n = len(reader.pages)
    texts = {}

    def page_text(i):
        if i not in texts:
            texts[i] = (reader.pages[i].extract_text() or "").lower() if 0 <= i < n else ""
        return texts[i]

    offsets = []
    for title, num in entries[:8]:
        for off in range(-2, 8):
            if title.lower()[:25] in page_text(num - 1 + off):
                offsets.append(off)
                break
    off = max(set(offsets), key=offsets.count) if offsets else 0
    pages = sorted({(t, min(max(num - 1 + off, 0), n - 1)) for t, num in entries},
                   key=lambda x: x[1])
    return _ranges(pages, n)


def _ranges(entries, n_pages):
    out = []
    for i, (title, start) in enumerate(entries):
        end = entries[i + 1][1] if i + 1 < len(entries) else n_pages
        out.append({"title": title, "start": start, "end": max(end, start + 1)})
    return out


def relevant_text(reader):
    """Text of the people/community/education/inclusion chapters only.
    Returns (text, chapters_used, method) or ("", [], reason)."""
    chapters = chapters_from_outline(reader)
    method = "pdf bookmarks"
    if not [c for c in chapters if CHAPTER_WORDS.search(c["title"])]:
        chapters = chapters_from_toc_page(reader)
        method = "printed table of contents"
    picked = [c for c in chapters if CHAPTER_WORDS.search(c["title"])]
    if not picked:
        return "", [], "no usable table of contents"
    pages = sorted({p for c in picked for p in range(c["start"], c["end"])})
    parts = []
    for p in pages:
        parts.append(f"[page {p + 1}]\n{reader.pages[p].extract_text() or ''}")
    text = "\n\n".join(parts)
    words = text.split()
    if len(words) > MAX_CHAPTER_WORDS:
        text = " ".join(words[:MAX_CHAPTER_WORDS])
    return text, [c["title"] for c in picked], method

# ------------------------------------------------------------- main flow


def manual_urls():
    path = REPORTS / "manual_urls.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["ticker"].strip().upper(): r["url"].strip()
                for r in csv.DictReader(f) if r.get("url")}


def find_report(company, cfg, folder, log_lines):
    t = company["ticker"]
    domains = company_domains(t)
    log_lines.append(f"company domains found in 10-K: {sorted(domains)}")
    manual = manual_urls().get(t)
    if manual:
        candidates = [{"url": manual, "title": "manual", "year": None}]
        log_lines.append("using manual URL from manual_urls.csv")
    else:
        reply, _ = run_claude(SEARCH_PROMPT.format(
            company_name=company["company_name"], ticker=t, min_year=MIN_YEAR),
            cfg, tools="WebSearch")
        try:
            candidates = parse_json_reply(reply).get("candidates", [])
        except ValueError:
            candidates = []
        if not candidates and re.search(r"permission|denied|not available|unable to (?:search|use)",
                                        reply, re.I):
            # the search itself didn't run: that's an error, not "no report"
            raise RuntimeError(f"web search did not run: {reply[:300]}")
        log_lines.append(f"search suggested {len(candidates)} candidate(s)")

    for c in candidates:
        url = str(c.get("url", ""))
        ok, on_main, main = brand_check(url, domains)
        if not manual and not ok:
            log_lines.append(f"rejected (domain doesn't contain the company's brand "
                             f"name from {main}): {url}")
            continue
        try:
            data = fetch_pdf(url, cfg)
            reader = PdfReader(io.BytesIO(data))
        except Exception as e:
            log_lines.append(f"rejected (download/read failed: {e}): {url}")
            continue
        # the report's own year: from its file name if it has one (a 2024
        # report often mentions next year's dates), else from its first pages
        url_years = [int(y) for y in re.findall(r"(?<!\d)(20[2-3]\d)(?!\d)",
                                                urlparse(url).path)]
        year = max(url_years) if url_years else pdf_year(reader)
        if not year or year < MIN_YEAR:
            log_lines.append(f"rejected (year {year} < {MIN_YEAR}): {url}")
            continue
        if len(reader.pages) < MIN_REPORT_PAGES:
            log_lines.append(f"rejected (only {len(reader.pages)} pages, not a report): {url}")
            continue
        text, chapters, method = relevant_text(reader)
        if not text:
            log_lines.append(f"rejected (no usable table of contents / chapters): {url}")
            continue
        pdf_path = folder / f"report_{year}.pdf"
        pdf_path.write_bytes(data)
        log_lines.append(f"accepted {url} (year {year}, {len(reader.pages)} pages)")
        note = "" if on_main else (
            f"report is not on the company's main domain ({main or 'unknown'}); "
            f"quick human check: {url}")
        return url, year, (text, chapters, method), pdf_path.name, note
    return None, None, None, None, ""


def second_look(company, cfg, run_id, themes, sections):
    t = company["ticker"]
    folder = REPORTS / t
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / f"second_look_{run_id}.json"
    if out_path.exists():
        log("second_look", f"{t}: already done for run '{run_id}', skipping")
        return
    clear_human_flags(t, f"second_look:{run_id}")
    lines = []
    record = {"ticker": t, "run_id": run_id, "model": cfg["model"],
              "done_at": datetime.now().isoformat(timespec="seconds")}

    record["themes_rescored"] = themes
    url, year, found, pdf_name, domain_note = find_report(company, cfg, folder, lines)
    if not url:
        record.update(status="no report found", log=lines)
        write_json(out_path, record)
        flag_human(t, f"second_look:{run_id}", "no report found", " | ".join(lines)[:500])
        log("second_look", f"{t}: no report found")
        return
    record.update(report_url=url, report_year=year, report_pdf=pdf_name,
                  domain_note=domain_note)
    if domain_note:
        flag_human(t, f"second_look:{run_id}", "report not on main domain - quick check", domain_note)

    text, chapters, method = found
    text_file = f"report_{year}_chapters.txt"
    (folder / text_file).write_text(text, encoding="utf-8")
    lines.append(f"chapters via {method}: {chapters}")

    rubric = (ROOT / "rubric.md").read_text(encoding="utf-8")
    tenk = "\n\n".join(
        f'<filing_section name="{SECTION_LABELS[sec]}">\n'
        + (SECTIONS / t / f"{sec}.txt").read_text(encoding="utf-8").strip()
        + "\n</filing_section>" for sec in sections)
    skeleton = "{" + ", ".join(
        f'"{th}": {{"score": 0, "coverage": "sufficient", "evidence_ids": [], "reason": ""}}'
        for th in themes) + "}"
    prompt = RESCORE_PROMPT.format(
        company_name=company["company_name"], ticker=t, year=year, rubric=rubric,
        themes_list=" and ".join(f'"{th}"' for th in themes), themes_skeleton=skeleton,
        tenk=tenk, text=text)
    log("second_look", f"{t}: rescoring {themes} from {url} "
                       f"({len(prompt):,} characters, ~{len(prompt) // 4:,} tokens)")
    result, attempts, err = ask_claude(
        prompt, cfg, lambda r: check_reply(r, themes, {"10-K", "sustainability_report"}), t, "second_look")
    if result is None:
        record.update(status="rescore failed", log=lines, error=err, attempts=attempts)
        write_json(out_path, record)
        flag_human(t, f"second_look:{run_id}", "rescore reply invalid twice", err[:300])
        return
    record.update(status="scored", report_text_file=text_file, chapters=chapters,
                  chapter_method=method, prompt_chars=len(prompt), sections=sections,
                  ai={"evidence": result["evidence"],
                      "themes": {th: result["themes"][th] for th in themes},
                      "notes": result.get("notes", "")},
                  attempts=attempts, log=lines)
    write_json(out_path, record)
    log("second_look", f"{t}: " + ", ".join(
        f"{th} {result['themes'][th]['score']}" for th in themes) + " (before checks)")

def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", default=cfg["run_id"])
    p.add_argument("--test-tickers", default="",
                   help="TESTING ONLY: also run for these tickers even if coverage is sufficient "
                        "(use with a separate --run-id so real results aren't affected)")
    args = p.parse_args()
    forced = {x.strip().upper() for x in args.test_tickers.split(",") if x.strip()}
    todo = []
    for f in sorted(VERIFIED.glob(f"*_{args.run_id}.json")):
        r = read_json(f)
        if r.get("run_id") != args.run_id or str(r.get("rubric_version")) != RUBRIC_VERSION:
            continue
        themes = [th for th in SECOND_LOOK_THEMES
                  if r["coverage"][th] == "insufficient"
                  and not r["themes"][th].get("rescored_from_report")]
        if r["ticker"] in forced and not themes:
            themes = list(SECOND_LOOK_THEMES)
        if themes:
            todo.append((r, themes))
    log("second_look", f"{len(todo)} company(ies) with insufficient coverage in "
                       f"run '{args.run_id}'")
    for r, themes in todo:
        try:
            second_look({"ticker": r["ticker"], "company_name": r["company_name"]},
                        cfg, args.run_id, themes, r["sections"])
        except ClaudeError as e:
            log("second_look", f"STOPPING: {e}. Run again later to resume.")
            break
        except Exception as e:
            log("second_look", f"{r['ticker']}: ERROR {e}")
            flag_human(r["ticker"], f"second_look:{args.run_id}", "second look error", str(e)[:300])


if __name__ == "__main__":
    main()
