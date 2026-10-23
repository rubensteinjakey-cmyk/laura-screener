"""Global step 1: get each company's source document. Uses a little Claude
usage (web search) for companies without a 20-F.

  - 20-F filers on SEC EDGAR (GMAB, ALC, TLK): the latest original 20-F,
    with the same EDGAR code and User-Agent as the US run.
  - A PDF a teammate saved as data/global/manual/{ticker}.pdf: used as the
    annual report (for sites that block automated downloads).
  - The official copy on the stock exchange's filing site (Oct 23 decision;
    counts the same as the company's own site): Nasdaq Copenhagen, MOPS,
    IDX, SGX; see exchange_filings.py. If that copy isn't in English, the
    company's own site is still searched for an English version.
  - Otherwise: the latest annual report (fiscal 2025, published 2026)
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
from urllib.parse import unquote, urljoin, urlparse

import requests
from pypdf import PdfReader

import exchange_filings
from ai import parse_json_reply, run_claude
from common import Sec, load_config, log, read_json, write_json
from download import ARCHIVE_URL, INDEX_URL, filings_table, parse_filing_index, ticker_to_cik
from global_common import (G_DOCS, G_MANUAL, add_global_args, gclear, gflag, manual_pdf,
                           load_global_companies, skipped)
from second_look import link_on_page, registered_domain

logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings are noise here

STEP = "global_fetch"
RETRY = False
CFG = None
MIN_FISCAL_YEAR = 2025
MIN_AR_PAGES = 30
MAX_AR_BYTES = 200_000_000
MAX_20F_AGE_MONTHS = 18

SEARCH_PROMPT = """Find the official ANNUAL REPORT for fiscal year 2025 (published in 2026) of {company_name} (stock ticker {ticker}, {country}) as a PDF on the company's OWN investor-relations website.

Use web search. Rules:
- Prefer the official ENGLISH version. If the company publishes only a local-language version, give that.
- The full annual report, not a summary, presentation, sustainability report, 20-F or financial statements alone. If the company publishes its annual report in several parts (e.g. "Business Report" / "Annual Review" + "Financial Report" + "Sustainability Report"), give the part that describes the business, strategy and people (the business report or annual review), NOT the financial report.
- Only links on the company's own website, or on an investor-relations file service (for example q4cdn.com) that the company's own website links to. No third-party sites (stock exchanges, data aggregators, news).
- "found_on": the company web page that links to the PDF. Always give it; it is REQUIRED when the PDF is not on the company's own domain (document hosts such as ipaper or a CDN).
- "website": the company's official website home page.
- "ir_page": the page on the company's own website that lists its annual reports.

Reply with ONLY this JSON, nothing else:
{{"website": "https://www.company.com", "ir_page": "https://www.company.com/investors/annual-reports", "candidates": [{{"url": "https://...", "title": "...", "fiscal_year": 2025, "language": "English", "found_on": "https://www.company.com/investors/..."}}]}}
List up to 5 candidates, best first. If you find nothing, reply {{"website": "", "ir_page": "", "candidates": []}}."""


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
    words = re.findall(r"[a-z0-9]+", company["company_name"].lower())
    initials = "".join(w[0] for w in words if w not in ("pt", "tbk", "the", "of", "and"))
    if any(t in label or label in t for t in tokens if len(label) >= 3) or \
            (len(initials) >= 3 and label.startswith(initials[:3])):
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
                        r"financial\s+year|[åa]a?rsrapport|laporan\s+tahunan|gesch[äa]ftsbericht)"
                        r"\s*(20[2-3]\d)|(20[2-3]\d)\s*(?:annual\s+report|business\s+year|"
                        r"[åa]a?rsrapport|laporan\s+tahunan|年報|年度報告)",
                        unquote(urlparse(url).path).replace("-", " ").replace("_", " ")
                        + " " + first, re.I)
    titled = [int(a or b) for a, b in titled]
    titled = [y for y in titled if y <= date.today().year]
    if titled:
        # the year named most often ("2025 Annual Report" on every page), not a
        # stray date that happens to sit before a heading ("Mar 23, 2026 ANNUAL REPORT")
        return max(set(titled), key=lambda y: (titled.count(y), y))
    # skip upload-date folders like /2026_04_16/ or /2026/04/ (when it was posted)
    years = [int(y) for y in re.findall(r"(?<!\d)(20[2-3]\d)(?![\d]|[-_/]\d{2}(?:[-_/]|$))",
                                        urlparse(url).path)]
    if years:
        return max(years)
    found = []
    for page in reader.pages[:3]:
        found += [int(y) for y in re.findall(r"\b(20[1-3]\d)\b", page.extract_text() or "")]
    found = [y for y in found if y <= date.today().year]
    return max(found) if found else None


def download_pdf(url, cfg):
    if url.startswith("https://doc.twse.com.tw/pdf/"):
        return exchange_filings.mops_download(url)  # MOPS: rate-limited
    if url.endswith(exchange_filings.ESEF_MARK):
        return exchange_filings.esef_pdf(url)  # official ESEF filing, printed to PDF
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


def pdf_links_on_page(page, cfg):
    """PDF links on a company web page that look like a 2025 annual report
    (the code reads the page itself, so a link found here is the company's)."""
    html = http_get(page, cfg, timeout=60)
    html.raise_for_status()
    out = []
    for m in re.finditer(r"""href\s*=\s*["']([^"'#]+)["'][^>]*>(.{0,200}?)</a>""", html.text,
                         re.I | re.S):
        href, label = m.group(1).replace("&amp;", "&"), re.sub(r"<[^>]+>", " ", m.group(2))
        url = urljoin(page, href)
        hay = unquote(url + " " + label).lower()
        if ".pdf" not in hay and "download" not in hay:
            continue
        if re.search(r"annual|integrated|ar[-_ ]?20?25|laporan tahunan|年報|arsrapport|årsrapport",
                     hay) and re.search(r"2025|(?<!\d)25(?!\d)", hay) and \
                not re.search(r"sustainab|esg|summary|presentation|20-?f|interim|quarter|"
                              r"half[- ]year|notice|proxy|minutes", hay):
            out.append(url)
    return list(dict.fromkeys(out))[:6]


def try_pdf(url, found_on, own, on_main, title, log_lines):
    """Download and check one candidate. Returns (data, info), or (None, "blocked"/"")."""
    try:
        data = download_pdf(url, CFG)
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        log_lines.append(f"download failed ({e}): {url}")
        return None, ("blocked" if re.search(r"40[13]|429|Forbidden|CERTIFICATE", str(e), re.I)
                      else "")
    year = report_year(url, reader)
    if not year or year < MIN_FISCAL_YEAR:
        log_lines.append(f"rejected (year {year} < {MIN_FISCAL_YEAR}): {url}")
        return None, ""
    if len(reader.pages) < MIN_AR_PAGES:
        log_lines.append(f"rejected (only {len(reader.pages)} pages): {url}")
        return None, ""
    sample = "\n".join((reader.pages[i].extract_text() or "")
                       for i in range(min(len(reader.pages), 12)))
    english = is_english(sample)
    log_lines.append(f"acceptable: {url} ({year}, {len(reader.pages)} pages, "
                     f"{'English' if english else 'non-English'})")
    return data, {"url": url, "year": year, "pages": len(reader.pages), "english": english,
                  "found_on": found_on, "on_main_domain": on_main, "own_domain": own,
                  "title": title}


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
    found = found if isinstance(found, dict) else {}
    cands = found.get("candidates", []) or []
    if not cands and re.search(r"permission|denied|not available|unable to (?:search|use)",
                               reply, re.I):
        raise RuntimeError(f"web search did not run: {reply[:300]}")
    website = str(found.get("website", ""))
    log_lines.append(f"search: website {website or '?'}, ir_page {found.get('ir_page') or '?'}, "
                     f"{len(cands)} candidate(s)")
    own = confirm_website(website, company, cfg, log_lines) if website else ""
    if not own:
        return None, {"reason": "company website not confirmed", "page": website}

    accepted, blocked, pages = [], [], []
    for c in cands:
        url, page = str(c.get("url", "")), str(c.get("found_on") or "")
        if page and domain_ok(page, own)[0]:
            pages.append(page)
        ok, on_main = domain_ok(url, own)
        found_on = ""
        if not ok:
            # Another host (an IR file service like q4cdn, or a document host
            # like ipaper / a company CDN): accepted ONLY if the code finds the
            # link on a page of the company's own website (always flagged for
            # a quick human check, since it isn't on the main domain).
            host = registered_domain(urlparse(url).hostname or "")
            if not page or not domain_ok(page, own)[0]:
                log_lines.append(f"rejected (on {host}, no company page given): {url}")
                continue
            try:
                if not link_on_page(page, url, cfg):
                    log_lines.append(f"rejected (link not found on company page {page}): {url}")
                    continue
            except Exception as e:
                log_lines.append(f"rejected (couldn't read company page {page}: {e}): {url}")
                if re.search(r"40[13]|429|Forbidden", str(e), re.I):
                    blocked.append(page)
                continue
            found_on = page
        data, info = try_pdf(url, found_on, own, on_main, str(c.get("title", "")), log_lines)
        if data is None:
            if info == "blocked":
                blocked.append(page or url)
            continue
        accepted.append((data, info))
        if info["english"]:
            break  # official English version found

    # The search's links can be stale or guessed: read the company's own IR
    # page(s) and take the annual-report PDF links found there.
    if not any(i["english"] for _, i in accepted):
        ir = str(found.get("ir_page") or "")
        if ir and domain_ok(ir, own)[0]:
            pages.insert(0, ir)
        for page in list(dict.fromkeys(pages))[:3]:
            try:
                links = pdf_links_on_page(page, cfg)
            except Exception as e:
                log_lines.append(f"couldn't read IR page {page}: {e}")
                if re.search(r"40[13]|429|Forbidden", str(e), re.I):
                    blocked.append(page)
                continue
            log_lines.append(f"IR page {page}: {len(links)} annual-report link(s)")
            for url in links:
                ok, on_main = domain_ok(url, own)
                data, info = try_pdf(url, "" if ok else page, own, on_main, "from IR page",
                                     log_lines)
                if data is None:
                    if info == "blocked":
                        blocked.append(page)
                    continue
                accepted.append((data, info))
                if info["english"]:
                    break
            if any(i["english"] for _, i in accepted):
                break
    if accepted:
        data, info = next(((d, i) for d, i in accepted if i["english"]), accepted[0])
        return data, info
    if blocked:
        return None, {"reason": "site blocked the download", "page": blocked[0]}
    return None, {"reason": "no acceptable annual report found", "page": website}


def find_exchange_report(company, cfg, log_lines):
    """The annual report on the stock exchange's filing site. Returns
    (pdf_bytes, info) or (None, {"site", "page", "blocked"})."""
    cands, site, page, blocked = exchange_filings.candidates(
        company, name_tokens(company["company_name"]), cfg, log_lines)
    accepted = []
    for c in cands:
        data, info = try_pdf(c["url"], "", "", True, c["title"], log_lines)
        if data is None:
            blocked = blocked or info == "blocked"
            continue
        info.update(source_site=site, exchange_page=c["page"],
                    exchange_name=c.get("exchange_name", ""))
        accepted.append((data, info))
        if info["english"]:
            break
    if accepted:
        return next(((d, i) for d, i in accepted if i["english"]), accepted[0])
    return None, {"site": site, "page": page, "blocked": blocked}


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
    status_path = folder / "fetch_status.json"
    prev = read_json(status_path) if status_path.exists() else {}
    retry = RETRY and "exchange" not in prev  # --retry: once, with the exchange route
    if prev and not manual_pdf(t) and not retry and (
            prev.get("reason") == "site blocked the download" or prev.get("attempts", 0) >= 2):
        # the site blocks downloads (or two searches found nothing): wait for a
        # teammate's PDF in data/global/manual/ instead of searching again
        log(STEP, f"{t}: earlier fetch failed ({prev.get('reason')}); waiting for "
                  f"data/global/manual/{t}.pdf"
                  + ("" if "exchange" in prev else " (use --retry to search again)"))
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
                        english=True, how="SEC EDGAR (latest 20-F)", source_site="SEC EDGAR")
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
                    source_site=f"manual PDF ({manual.name})",
                    fiscal_year=report_year("", reader), pages=len(reader.pages),
                    english=is_english(sample))
        write_json(meta_path, meta)
        log(STEP, f"{t}: using manual PDF {manual.name} ({len(reader.pages)} pages)")
        return

    # 3) the official copy on the stock exchange's filing site
    ex_data, ex = find_exchange_report(company, cfg, lines)
    if ex_data is not None and ex["english"]:
        save_report(folder, meta, ex_data, ex, lines, how=f"official exchange filing ({ex['source_site']})")
        return

    # 4) annual report from the company's own website (also when the exchange
    #    copy isn't in English, to look for an official English version)
    data, info = find_annual_report(company, cfg, lines)
    if data is not None and (info["english"] or ex_data is None):
        info["source_site"] = "company website"
        save_report(folder, meta, data, info, lines,
                    how="company website (web search, checked by code)")
        return
    if ex_data is not None:
        lines.append("no English version on the company's site: using the exchange copy")
        save_report(folder, meta, ex_data, ex, lines, how=f"official exchange filing ({ex['source_site']})")
        return
    reason = info["reason"]
    if ex.get("blocked") and reason != "site blocked the download":
        reason = "site blocked the download"
    pages = [p for p in (ex.get("page"), info.get("page")) if p]
    where = "; ".join(dict.fromkeys(pages)) or "unknown"
    gflag(t, STEP, reason,
          f"page: {where}; save the PDF as data/global/manual/{t}.pdf"
          f" | {' | '.join(lines)}"[:900])
    log(STEP, f"{t}: {reason} ({where})")
    (folder / "fetch_log.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(status_path, {"reason": reason, "page": where, "exchange": ex.get("site", ""),
                             "attempts": prev.get("attempts", 0) + 1})


def save_report(folder, meta, data, info, lines, how):
    t = meta["ticker"]
    (folder / "annual_report.pdf").write_bytes(data)
    exchange = how.startswith("official exchange")
    meta.update(kind="annual_report", form="Annual report", url=info["url"],
                fiscal_year=info["year"], pages=info["pages"], english=info["english"],
                found_on=info["found_on"], own_domain=info["own_domain"],
                on_main_domain=info["on_main_domain"], title=info["title"],
                source_site=info["source_site"], how=how, log=lines)
    if exchange:
        meta.update(exchange_page=info.get("exchange_page", ""),
                    exchange_name=info.get("exchange_name", ""))
    if not exchange and not info["on_main_domain"]:
        gflag(t, STEP, "report not on main domain - quick check",
              f"{info['url']} (company domain {info['own_domain']})"
              + (f"; link found on {info['found_on']}" if info["found_on"] else ""))
    write_json(folder / "meta.json", meta)
    log(STEP, f"{t}: annual report {info['year']} ({info['pages']} pages, "
              f"{'English' if info['english'] else 'non-English'}) from {meta['source_site']}: "
              f"{info['url']}")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    add_global_args(p)
    p.add_argument("--retry", action="store_true",
                   help="search again (exchange filing site first, then the company's site) "
                        "for companies that failed before the exchange route existed")
    args = p.parse_args()
    global RETRY, CFG
    RETRY = args.retry
    CFG = cfg
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
