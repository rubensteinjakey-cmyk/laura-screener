"""Step 9: second look at thin filings. Uses some Claude subscription usage.

Only for companies whose verified result has thin_filing = true:
  1. Find the latest sustainability / impact / ESG report. Claude (with web
     search turned on, nothing else) suggests PDF links; the CODE accepts one
     only if
       - its web address is on the company's own domain. "Own domain" means
         a website the company itself lists in its 10-K text (so we don't
         trust the AI on which site is official), and
       - the PDF says 2024 or later on its first pages.
     A human can also put a link in data/reports/manual_urls.csv
     (columns: ticker,url) and it will be used instead of searching.
  2. Read only the relevant chapters (people, community, education,
     inclusion), found through the PDF's bookmarks or its table of contents.
  3. Rescore only Community and Cross-cultural with the same rubric.
  4. verify.py then checks the quotes against the report text and keeps the
     higher score (scores can only go up).
  5. If no valid report is found: keep the 10-K score and add the company to
     needs_human.csv with the reason "no report found".

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

from ai import ClaudeError, check_themes, parse_json_reply, run_claude
from common import (REPORTS, ROOT, SECTIONS, VERIFIED, clear_human_flags,
                    flag_human, load_config, log, read_json, write_json)

MIN_YEAR = 2024
MAX_PDF_BYTES = 80_000_000
MAX_CHAPTER_WORDS = 40_000
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

RESCORE_PROMPT = """Below is the scoring rubric, then chapters from {company_name}'s ({ticker}) {year} sustainability/impact report (only the chapters about people, community, education and inclusion).

<rubric>
{rubric}
</rubric>

Rescore ONLY these two themes from this report text: "community" and "cross_cultural". Use the same ladders, rules and traps as for a 10-K. Use ONLY the text below.

Every quote must be copied exactly, character for character, from the report text below: one continuous passage, no "..." and no changes, each under about 40 words, 1-3 quotes for any score of 1 or higher, empty list for 0.

Reply with ONE JSON object and nothing else:
{{"themes": {{"community": {{"score": 0, "level_reason": "", "quotes": []}}, "cross_cultural": {{"score": 0, "level_reason": "", "quotes": []}}}}}}

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


def on_own_domain(url, domains):
    host = urlparse(url).hostname or ""
    return registered_domain(host) in domains

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
        log_lines.append(f"search suggested {len(candidates)} candidate(s)")

    for c in candidates:
        url = str(c.get("url", ""))
        if not manual and not on_own_domain(url, domains):
            log_lines.append(f"rejected (not company's own domain): {url}")
            continue
        try:
            data = fetch_pdf(url, cfg)
            reader = PdfReader(io.BytesIO(data))
        except Exception as e:
            log_lines.append(f"rejected (download/read failed: {e}): {url}")
            continue
        year = pdf_year(reader)
        if not year or year < MIN_YEAR:
            log_lines.append(f"rejected (year {year} < {MIN_YEAR}): {url}")
            continue
        pdf_path = folder / f"report_{year}.pdf"
        pdf_path.write_bytes(data)
        log_lines.append(f"accepted {url} (year {year})")
        return url, year, reader, pdf_path.name
    return None, None, None, None


def second_look(company, cfg, run_id):
    t = company["ticker"]
    folder = REPORTS / t
    folder.mkdir(parents=True, exist_ok=True)
    out_path = folder / f"second_look_{run_id}.json"
    if out_path.exists():
        log("second_look", f"{t}: already done for run '{run_id}', skipping")
        return
    clear_human_flags(t, "second_look")
    lines = []
    record = {"ticker": t, "run_id": run_id, "model": cfg["model"],
              "done_at": datetime.now().isoformat(timespec="seconds")}

    url, year, reader, pdf_name = find_report(company, cfg, folder, lines)
    if not url:
        record.update(status="no report found", log=lines)
        write_json(out_path, record)
        flag_human(t, "second_look", "no report found", " | ".join(lines)[:500])
        log("second_look", f"{t}: no report found")
        return
    record.update(report_url=url, report_year=year, report_pdf=pdf_name)

    text, chapters, method = relevant_text(reader)
    if not text:
        record.update(status="no usable table of contents", log=lines)
        write_json(out_path, record)
        flag_human(t, "second_look", "report found but chapters could not be located", url)
        log("second_look", f"{t}: report found but no usable table of contents")
        return
    text_file = f"report_{year}_chapters.txt"
    (folder / text_file).write_text(text, encoding="utf-8")
    lines.append(f"chapters via {method}: {chapters}")

    rubric = (ROOT / "rubric.md").read_text(encoding="utf-8")
    prompt = RESCORE_PROMPT.format(company_name=company["company_name"], ticker=t,
                                   year=year, rubric=rubric, text=text)
    log("second_look", f"{t}: rescoring from {url} ({len(prompt):,} characters)")
    result, err = None, ""
    for attempt in (1, 2):
        try:
            reply, info = run_claude(prompt if attempt == 1 else prompt +
                                     f"\n\nYour previous reply was unusable ({err}). JSON only.", cfg)
            result = parse_json_reply(reply)
            check_themes(result, ["community", "cross_cultural"])
            break
        except (ValueError, KeyError, ClaudeError) as e:
            err, result = str(e), None
    if result is None:
        record.update(status="rescore failed", log=lines, error=err)
        write_json(out_path, record)
        flag_human(t, "second_look", "rescore reply invalid twice", err[:300])
        return
    record.update(status="scored", report_text_file=text_file, chapters=chapters,
                  chapter_method=method, prompt_chars=len(prompt),
                  ai={"themes": result["themes"]}, log=lines)
    write_json(out_path, record)
    log("second_look", f"{t}: community {result['themes']['community']['score']}, "
                       f"cross_cultural {result['themes']['cross_cultural']['score']} "
                       f"(before quote check)")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-id", default=cfg["run_id"])
    p.add_argument("--test-tickers", default="",
                   help="TESTING ONLY: also run for these tickers even if not thin "
                        "(use with a separate --run-id so real results aren't affected)")
    args = p.parse_args()
    forced = {x.strip().upper() for x in args.test_tickers.split(",") if x.strip()}
    thin = [read_json(f) for f in sorted(VERIFIED.glob(f"*_{args.run_id}.json"))]
    thin = [r for r in thin if r.get("run_id") == args.run_id
            and (r.get("thin_filing") or r["ticker"] in forced)]
    log("second_look", f"{len(thin)} thin filing(s) in run '{args.run_id}'")
    for r in thin:
        try:
            second_look({"ticker": r["ticker"], "company_name": r["company_name"]},
                        cfg, args.run_id)
        except ClaudeError as e:
            log("second_look", f"STOPPING: {e}. Run again later to resume.")
            break
        except Exception as e:
            log("second_look", f"{r['ticker']}: ERROR {e}")
            flag_human(r["ticker"], "second_look", "second look error", str(e)[:300])


if __name__ == "__main__":
    main()
