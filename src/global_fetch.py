"""Global step 1: get each company's source document. Uses a little Claude
usage (web search) for companies without a 20-F.

  - 20-F filers on SEC EDGAR (GMAB, ALC, TLK): the latest original 20-F,
    with the same EDGAR code and User-Agent as the US run.
  - A PDF a teammate saved as data/global/manual/{ticker}.pdf: used as the
    annual report (for sites that block automated downloads).
  - Everyone else: the latest annual report (fiscal 2025, published 2026)
    from the company's own investor-relations website. Claude (web search
    only) suggests the company's website and PDF links; the CODE accepts a
    link only if
      - the website is confirmed (its domain contains a word of the company
        name, or its home page shows the company name), and
      - the PDF is on that domain or a brand-name domain (same rule as the
        US second look), or on an IR file service (q4cdn etc.) ONLY if the
        code finds the link on a page of the company's own website, and
      - it is a real report (30+ pages) for fiscal 2025 or later.
    The official English version is preferred; a local-language report is
    used only when no English one is accepted (marked "non-English source").
    Blocked downloads go to needs_human.csv with the page URL.
Everything is cached in data/global/docs/{ticker}/.

Run:  python src/global_fetch.py --tickers GMAB,SIKA.SW
      python src/global_fetch.py --country Denmark
"""

import argparse
import io
import logging
import re
import shutil
from datetime import date
from urllib.parse import unquote, urlparse

import requests
from pypdf import PdfReader

from ai import parse_json_reply, run_claude
from common import Sec, load_config, log, write_json
from download import ARCHIVE_URL, INDEX_URL, filings_table, parse_filing_index, ticker_to_cik
from global_common import (G_DOCS, G_MANUAL, add_global_args, gclear, gflag, manual_pdf,
                           load_global_companies, skipped)
from second_look import link_on_page, registered_domain

logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings are noise here

STEP = "global_fetch"
RETRY = False
MIN_FISCAL_YEAR = 2025
MIN_AR_PAGES = 30
MAX_AR_BYTES = 200_000_000
MAX_20F_AGE_MONTHS = 18

SEARCH_PROMPT = """Find the official ANNUAL REPORT for fiscal year 2025 (published in 2026) of {company_name} (stock ticker {ticker}, {country}) as a PDF on the company's OWN investor-relations website.

Use web search. Rules:
- Prefer the official ENGLISH version. If the company publishes only a local-language version, give that.
- The full annual report, not a summary, presentation, sustainability report, 20-F or financial statements alone.
- Only links on the company's own website, or on an investor-relations file service (for example q4cdn.com) that the company's own website links to. No third-party sites (stock exchanges, data aggregators, news).
- "found_on": the company web page that links to the PDF. Always give it; it is REQUIRED when the PDF is not on the company's own domain (document hosts such as ipaper or a CDN).
- "website": the company's official website home page.

Reply with ONLY this JSON, nothing else:
{{"website": "https://www.company.com", "candidates": [{{"url": "https://...", "title": "...", "fiscal_year": 2025, "language": "English", "found_on": "https://www.company.com/investors/..."}}]}}
List up to 5 candidates, best first. If you find nothing, reply {{"website": "", "candidates": []}}."""


def http_get(url, cfg, **kw):
    ua = cfg["user_agent"]
    return requests.get(url, timeout=kw.pop("timeout", 90), headers={
        "User-Agent": f'Mozilla/5.0 (research screener; {ua["name"]} {ua["email"]})'}, **kw)


def name_tokens(company_name):
    stop = {"group", "holding", "holdings", "the", "inc", "corp", "ltd", "bank", "company",
            "financial", "industries", "systems", "technology", "trust", "international",
            "and", "of", "a/s", "ag", "plc", "tbk", "pt", "reit", "commercial", "capital"}
    words = re.findall(r"[a-z0-9]+", company_name.lower())
    return [w for w in words if w not in stop and len(w) >= 3]


def confirm_website(website, company, cfg, log_lines):
    """The company's own domain, or "" if it can't be confirmed."""
    host = urlparse(website).hostname or ""
    if not host:
        return ""
    reg = registered_domain(host)
    label = reg.split(".")[0]
    tokens = name_tokens(company["company_name"])
    if any(t in label or label in t for t in tokens if len(label) >= 3):
        log_lines.append(f"website {reg} confirmed (domain matches the company name)")
        return reg
    try:
        html = http_get(website, cfg, timeout=40).text.lower()
        if company["company_name"].lower() in html or (tokens and all(t in html for t in tokens)):
            log_lines.append(f"website {reg} confirmed (home page shows the company name)")
            return reg
    except Exception as e:
        log_lines.append(f"website {reg}: couldn't read home page ({e})")
    log_lines.append(f"website {reg} NOT confirmed")
    return ""


def domain_ok(url, own):
    """Own domain or a brand-name domain (same rule as the US second look)."""
    reg = registered_domain(urlparse(url).hostname or "")
    brand = own.split(".")[0]
    return reg == own or (len(brand) >= 3 and brand in reg.split(".")[0]), reg == own


def is_english(text):
    words = re.findall(r"[a-z]+", text.lower())
    if len(words) < 200:
        return False
    common = {"the", "and", "of", "to", "in", "our", "for", "with", "is", "we", "on", "by"}
    return sum(w in common for w in words) / len(words) > 0.12


def report_year(url, reader):
    """Fiscal year the report covers: "Annual Report 2025" / "Business Year 2025" /
    "full-year 2025" in the file name or
    on the first pages wins; else the latest year in the file name or there."""
    first = " ".join((reader.pages[i].extract_text() or "")
                     for i in range(min(3, len(reader.pages))))
    titled = re.findall(r"(?:annual\s+report|business\s+year|full[- ]year|fiscal\s+year|"
                        r"financial\s+year)\s*(20[2-3]\d)|(20[2-3]\d)\s*(?:annual\s+report|"
                        r"business\s+year)",
                        unquote(urlparse(url).path).replace("-", " ").replace("_", " ")
                        + " " + first, re.I)
    titled = [int(a or b) for a, b in titled]
    if titled:
        return max(y for y in titled if y <= date.today().year)
    years = [int(y) for y in re.findall(r"(?<!\d)(20[2-3]\d)(?!\d)", urlparse(url).path)]
    if years:
        return max(years)
    found = []
    for page in reader.pages[:3]:
        found += [int(y) for y in re.findall(r"\b(20[1-3]\d)\b", page.extract_text() or "")]
    found = [y for y in found if y <= date.today().year]
    return max(found) if found else None


def download_pdf(url, cfg):
    r = http_get(url, cfg, timeout=180, stream=True)
    r.raise_for_status()
    data = b""
    for chunk in r.iter_content(1 << 16):
        data += chunk
        if len(data) > MAX_AR_BYTES:
            raise ValueError("PDF too large")
    if not data.startswith(b"%PDF"):
        raise ValueError("link is not a PDF")
    return data


def find_annual_report(company, cfg, log_lines):
    """Returns (pdf_bytes, info) or (None, info with the reason)."""
    t = company["ticker"]
    reply, _ = run_claude(SEARCH_PROMPT.format(company_name=company["company_name"],
                                               ticker=t, country=company["country"]),
                          cfg, tools="WebSearch")
    try:
        found = parse_json_reply(reply)
    except ValueError:
        found = {}
    cands = found.get("candidates", []) if isinstance(found, dict) else []
    if not cands and re.search(r"permission|denied|not available|unable to (?:search|use)",
                               reply, re.I):
        raise RuntimeError(f"web search did not run: {reply[:300]}")
    website = str(found.get("website", "")) if isinstance(found, dict) else ""
    log_lines.append(f"search: website {website or '?'}, {len(cands)} candidate(s)")
    own = confirm_website(website, company, cfg, log_lines) if website else ""
    if not own:
        return None, {"reason": "company website not confirmed", "page": website}

    accepted, blocked = [], []
    for c in cands:
        url, page = str(c.get("url", "")), str(c.get("found_on") or "")
        ok, on_main = domain_ok(url, own)
        found_on = ""
        if not ok:
            # Another host (an IR file service like q4cdn, or a document host
            # like ipaper / a company CDN): accepted ONLY if the code finds the
            # link on a page of the company's own website (always flagged for
            # a quick human check, since it isn't on the main domain).
            host = registered_domain(urlparse(url).hostname or "")
            if not page or not domain_ok(page, own)[0]:
                log_lines.append(f"rejected (on file service {host}, no company page given): {url}")
                continue
            try:
                if not link_on_page(page, url, cfg):
                    log_lines.append(f"rejected (link not found on company page {page}): {url}")
                    continue
            except Exception as e:
                log_lines.append(f"rejected (couldn't read company page {page}: {e}): {url}")
                continue
            found_on = page
        try:
            data = download_pdf(url, cfg)
            reader = PdfReader(io.BytesIO(data))
        except Exception as e:
            log_lines.append(f"download failed ({e}): {url}")
            if re.search(r"40[13]|429|timed? ?out|Forbidden|not a PDF", str(e), re.I):
                blocked.append(page or url)
            continue
        year = report_year(url, reader)
        if not year or year < MIN_FISCAL_YEAR:
            log_lines.append(f"rejected (year {year} < {MIN_FISCAL_YEAR}): {url}")
            continue
        if len(reader.pages) < MIN_AR_PAGES:
            log_lines.append(f"rejected (only {len(reader.pages)} pages): {url}")
            continue
        sample = "\n".join((reader.pages[i].extract_text() or "")
                           for i in range(min(len(reader.pages), 12)))
        english = is_english(sample)
        info = {"url": url, "year": year, "pages": len(reader.pages), "english": english,
                "found_on": found_on, "on_main_domain": on_main, "own_domain": own,
                "title": str(c.get("title", ""))}
        log_lines.append(f"acceptable: {url} ({year}, {len(reader.pages)} pages, "
                         f"{'English' if english else 'non-English'})")
        accepted.append((data, info))
        if english:
            break  # official English version found
    if accepted:
        data, info = next(((d, i) for d, i in accepted if i["english"]), accepted[0])
        return data, info
    if blocked:
        return None, {"reason": "site blocked the download", "page": blocked[0]}
    return None, {"reason": "no acceptable annual report found", "page": website}


def pick_20f(table):
    rows = [r for r in table if r["form"] == "20-F"]
    if not rows:
        return None
    r = max(rows, key=lambda x: x["filingDate"])
    age = (date.today() - date.fromisoformat(r["filingDate"])).days / 30.44
    return r if age <= MAX_20F_AGE_MONTHS else None


def fetch_company(sec, cik_map, company, cfg):
    t = company["ticker"]
    folder = G_DOCS / t
    meta_path = folder / "meta.json"
    if meta_path.exists():
        log(STEP, f"{t}: already fetched, skipping")
        return
    if (folder / "fetch_log.txt").exists() and not manual_pdf(t) and not RETRY:
        # an earlier try failed (e.g. the site blocked the download): wait for
        # a teammate's PDF in data/global/manual/ instead of searching again
        log(STEP, f"{t}: earlier fetch failed; waiting for data/global/manual/{t}.pdf "
                  "(use --retry to search again)")
        return
    skip = skipped().get(t.upper())
    if skip:
        gflag(t, STEP, skip, "kept on the list, not scored (global_skip.csv)")
        log(STEP, f"{t}: not scored ({skip})")
        return
    gclear(t, STEP)
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"ticker": t, "company_name": company["company_name"],
            "country": company["country"], "sector": company["sector"]}

    # 1) 20-F on EDGAR (only tickers that trade in the US under that symbol)
    cik = cik_map.get(t) if "." not in t else None
    if cik:
        sub, table = filings_table(sec, cik)
        f = pick_20f(table)
        if f:
            acc, doc = f["accessionNumber"], f["primaryDocument"]
            nodash = acc.replace("-", "")
            html = sec.get(ARCHIVE_URL.format(cik=cik, acc=nodash, doc=doc)).content
            (folder / "primary.htm").write_bytes(html)
            index_html = sec.get(INDEX_URL.format(cik=cik, acc_nodash=nodash, acc=acc)).text
            rd = f.get("reportDate", "")
            meta.update(kind="20-F", form="20-F", cik=cik, sec_name=sub.get("name", ""),
                        accession_number=acc, filing_date=f["filingDate"],
                        fiscal_year=int(rd[:4]) if rd else None,
                        url=ARCHIVE_URL.format(cik=cik, acc=nodash, doc=doc),
                        documents=parse_filing_index(index_html, cik, nodash),
                        english=True, how="SEC EDGAR (latest 20-F)")
            write_json(meta_path, meta)
            log(STEP, f"{t}: 20-F filed {f['filingDate']} (FY {meta['fiscal_year']}), "
                      f"{len(html):,} bytes")
            return

    # 2) a PDF a teammate saved by hand
    manual = manual_pdf(t)
    lines = []
    if manual:
        shutil.copyfile(manual, folder / "annual_report.pdf")
        reader = PdfReader(str(manual))
        sample = "\n".join((reader.pages[i].extract_text() or "")
                           for i in range(min(len(reader.pages), 12)))
        meta.update(kind="annual_report", form="Annual report", url="", how="manual PDF",
                    fiscal_year=report_year("", reader), pages=len(reader.pages),
                    english=is_english(sample))
        write_json(meta_path, meta)
        log(STEP, f"{t}: using manual PDF {manual.name} ({len(reader.pages)} pages)")
        return

    # 3) annual report from the company's own website
    data, info = find_annual_report(company, cfg, lines)
    if data is None:
        gflag(t, STEP, info["reason"],
              f"page: {info.get('page') or 'unknown'}; save the PDF as data/global/manual/{t}.pdf"
              f" | {' | '.join(lines)}"[:700])
        log(STEP, f"{t}: {info['reason']} ({info.get('page', '')})")
        (folder / "fetch_log.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    (folder / "annual_report.pdf").write_bytes(data)
    meta.update(kind="annual_report", form="Annual report", url=info["url"],
                fiscal_year=info["year"], pages=info["pages"], english=info["english"],
                found_on=info["found_on"], own_domain=info["own_domain"],
                on_main_domain=info["on_main_domain"], title=info["title"],
                how="company website (web search, checked by code)", log=lines)
    if not info["on_main_domain"]:
        gflag(t, STEP, "report not on main domain - quick check",
              f"{info['url']} (company domain {info['own_domain']})"
              + (f"; link found on {info['found_on']}" if info["found_on"] else ""))
    write_json(meta_path, meta)
    log(STEP, f"{t}: annual report {info['year']} ({info['pages']} pages, "
              f"{'English' if info['english'] else 'non-English'}) from {info['url']}")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    add_global_args(p)
    p.add_argument("--retry", action="store_true", help="search again for companies that failed")
    args = p.parse_args()
    global RETRY
    RETRY = args.retry
    sec = Sec(cfg)
    cik_map = ticker_to_cik(sec)
    for c in load_global_companies(args.country, args.tickers):
        try:
            fetch_company(sec, cik_map, c, cfg)
        except Exception as e:
            log(STEP, f"{c['ticker']}: ERROR {e}")
            gflag(c["ticker"], STEP, "fetch error", str(e)[:300])
            if "limit" in str(e).lower():
                log(STEP, "STOPPING (usage limit). Run the same command again later.")
                break


if __name__ == "__main__":
    main()
