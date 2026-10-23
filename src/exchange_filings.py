"""Official stock-exchange filing sites as a source (Oct 23 decision).

The copies companies are required to file with their stock exchange count the
same as the company's own website (like EDGAR in the US):

  Denmark      Nasdaq Copenhagen company announcements (Nasdaq's news API;
               the "Annual Financial Report" announcements and their PDFs).
               If no annual-report PDF is attached, the official ESEF filing
               (a zip holding the annual report as XHTML) is printed to PDF
               with the pre-installed Chromium, e.g. D/S Norden.
  Switzerland  SIX Swiss Exchange. SIX keeps no archive of issuers' annual
               reports to download, so this is logged; a Swiss company that
               also lists on SGX (e.g. Lonza) is looked up there.
  Taiwan       MOPS (Market Observation Post System), the shareholder-meeting
               annual report (股東會年報) by stock code; English version first
  Indonesia    IDX (idx.co.id) annual reports by stock code
  Singapore    SGX company announcements (links.sgx.com)

Each site returns candidate PDF links; global_fetch downloads and checks them
like any other report (fiscal 2025 or later, 30+ pages, English preferred).
The code confirms the company itself: by stock code (MOPS, IDX, SGX) or by
company name and the Copenhagen market (Nasdaq). Which site was used is
logged in meta.json (source_site) and in results_global.csv.
"""

import io
import os
import re
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from datetime import date
from urllib.parse import quote, unquote, urljoin

import requests

from ai import ClaudeError, parse_json_reply, run_claude

UA = None  # set by candidates(): "Mozilla/5.0 (research screener; name email)"

NASDAQ_API = ("https://api.news.eu.nasdaq.com/news/query.action?type=json&showAttachments=true"
              "&showCnsSpecific=true&showCompany=true&countResults=false&freeText={q}"
              "&company=&market=&cnscategory=Annual+Financial+Report&globalGroup=exchangeNotice"
              "&globalName=NordicAllMarkets&displayLanguage=en&language=&timeZone=CET"
              "&dateMask=yyyy-MM-dd+HH%3Amm%3Ass&limit=50&start=0&dir=DESC")
NASDAQ_PAGE = "https://www.nasdaq.com/european-market-activity/news/company-news"
MOPS_LIST = ("https://doc.twse.com.tw/server-java/t57sb01?step=1&colorchoice=1&co_id={co}"
             "&year={roc}&mtype=F&")
MOPS_POST = "https://doc.twse.com.tw/server-java/t57sb01"
MOPS_WAIT = 25  # seconds between MOPS requests; faster gets "FOR SECURITY REASONS"
IDX_API = ("https://www.idx.co.id/primary/ListedCompany/GetFinancialReport?indexFrom=0"
           "&pageSize=12&year={year}&reportType=rda&EmitenType=s&periode=audit"
           "&kodeEmiten={code}&SortColumn=KodeEmiten&SortOrder=asc")
IDX_PAGE = "https://www.idx.co.id/en/listed-companies/financial-statements-and-annual-report/"
SGX_PAGE = "https://www.sgx.com/securities/company-announcements"
SGX_ANN = re.compile(r"https?://links\.sgx\.com/1\.0\.0/corporate-announcements/([A-Z0-9]{8,})",
                     re.I)
SITE_NAMES = {"Denmark": "Nasdaq Copenhagen company announcements",
              "Switzerland": "SIX Swiss Exchange", "Taiwan": "MOPS",
              "Indonesia": "IDX", "Singapore": "SGX company announcements"}
SGX_CROSS = {"LONN.SW"}  # Swiss companies with a secondary SGX listing

AR_FILE = re.compile(r"annual[\s_-]*report|årsrapport|arsrapport|aarsrapport|laporan[\s_-]*tahunan|"
                     r"(?<![a-z])ar[\s_-]*(?:fy)?[\s_-]*20\d\d", re.I)  # "CICT AR 2025_Final.pdf"
NOT_AR = re.compile(r"sustainab|esg|risk|capital management|pillar|announcement|meddelelse|"
                    r"notice|proxy|circular|letter to|summary|presentation|market review|\.zip|\.xhtml|xbri",
                    re.I)

_last_mops = [0.0]


def _get(url, **kw):
    return requests.get(url, timeout=kw.pop("timeout", 90), headers={"User-Agent": UA}, **kw)


def fold(s):
    s = s.lower()
    for a, b in (("æ", "ae"), ("ø", "o"), ("å", "aa"), ("ü", "u"), ("ö", "o"), ("ä", "a"),
                 ("é", "e")):
        s = s.replace(a, b)
    return s


def code_of(ticker):
    return ticker.split(".")[0]


# ------------------------------------------------------------------ Nasdaq

def nasdaq(company, tokens, log_lines):
    """Annual Financial Report announcements on Nasdaq Copenhagen."""
    # every word of the name ("Jyske Bank" must not match "Jyske Invest")
    words = [w for w in re.findall(r"[a-z0-9æøå]+", fold(company["company_name"]))
             if len(w) >= 3] or [fold(t) for t in tokens]
    items = []
    for q in dict.fromkeys([" ".join(words), max(words, key=len)]):  # noqa: B007
        r = _get(NASDAQ_API.format(q=quote(q)))
        r.raise_for_status()
        items = (r.json().get("results") or {}).get("item") or []
        items = [it for it in items if "copenhagen" in str(it.get("market", "")).lower()
                 and all(w in fold(str(it.get("company", ""))) for w in words)]
        if items:
            break
    if not items:
        log_lines.append(f"Nasdaq Copenhagen: no annual-report announcement for {' '.join(words)}")
        return [], NASDAQ_PAGE
    page = items[0].get("messageUrl") or NASDAQ_PAGE
    out = []
    for it in items:
        if str(it.get("published", ""))[:4] < "2026":
            continue
        for a in it.get("attachment") or []:
            name = str(a.get("fileName", ""))
            if name.lower().endswith(".pdf") and AR_FILE.search(name) and not NOT_AR.search(name):
                out.append({"url": a.get("attachmentUrl", ""), "page": it.get("messageUrl", page),
                            "title": f"{it.get('company')}: {it.get('headline', '')} ({name})",
                            "english": it.get("language") == "en",
                            "exchange_name": it.get("company", "")})
    out.sort(key=lambda c: not c["english"])  # English announcement first
    esef = []
    for it in items:
        if str(it.get("published", ""))[:4] < "2026":
            continue
        for a in it.get("attachment") or []:
            name = str(a.get("fileName", ""))
            if re.search(r"20\d\d-\d\d-\d\d-[a-z]{2}\.zip$", name, re.I):
                esef.append({"url": a.get("attachmentUrl", "") + ESEF_MARK,
                             "page": it.get("messageUrl", page),
                             "title": f"{it.get('company')}: ESEF annual report ({name})",
                             "english": name.lower().endswith("-en.zip"),
                             "exchange_name": it.get("company", "")})
    esef.sort(key=lambda c: not c["english"])
    seen, uniq = set(), []
    for c in out:  # the same report attached twice under two names
        key = re.sub(r"[_\s-]*\d{8}", "", c["title"].lower())
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    log_lines.append(f"Nasdaq Copenhagen ({items[0].get('company')}): "
                     f"{len(uniq)} annual-report PDF(s) from 2026"
                     + (f", ESEF filing {esef[0]['title']} as a last resort" if esef else ""))
    return uniq[:3] + esef[:1], page


ESEF_MARK = "#esef"
CHROMIUM = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium")


def esef_pdf(url):
    """The official ESEF filing (zip with the annual report as XHTML),
    printed to PDF so it goes through the same checks and extraction."""
    r = _get(url.replace(ESEF_MARK, ""), timeout=300)
    r.raise_for_status()
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(r.content)).extractall(tmp)
        pages = sorted(Path(tmp).rglob("*.xhtml")) + sorted(Path(tmp).rglob("*.html"))
        if not pages:
            raise ValueError("ESEF zip has no XHTML report")
        report = max(pages, key=lambda p: p.stat().st_size)
        out = Path(tmp) / "report.pdf"
        subprocess.run([CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
                        "--no-pdf-header-footer", f"--print-to-pdf={out}",
                        report.as_uri()], capture_output=True, timeout=600)
        if not out.exists():
            raise ValueError("couldn't print the ESEF report to PDF")
        return out.read_bytes()


# -------------------------------------------------------------------- MOPS

def _mops(method, url, **kw):
    """MOPS refuses requests that come too fast ("FOR SECURITY REASONS"):
    space them out and retry."""
    for _ in range(6):
        wait = _last_mops[0] + MOPS_WAIT - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.request(method, url, timeout=180, headers={"User-Agent": UA}, **kw)
        except requests.ConnectionError:  # MOPS sometimes drops the connection
            _last_mops[0] = time.time() + MOPS_WAIT
            continue
        _last_mops[0] = time.time()
        if r.content[:4] == b"%PDF":
            return r
        r.encoding = "big5"
        if "SECURITY REASONS" not in r.text[:3000]:
            return r
    raise RuntimeError("MOPS kept refusing the request (rate limit or dropped connections)")


def mops(company, log_lines):
    co = code_of(company["ticker"])
    roc = date.today().year - 1911  # 2026 -> 115: reports filed this year (fiscal 2025)
    page = MOPS_LIST.format(co=co, roc=roc)
    r = _mops("GET", page)
    files = re.findall(r'readfile2\("F","(\d+)","([^"]+)"\)', r.text)
    name = re.search(r"公司名稱：\s*([^<\s]+)", r.text)
    name = name.group(1) if name else ""
    # F04 = annual report (股東會年報), FE4 = its English version; the file
    # name starts with the fiscal year
    ars = [f for c, f in files if c == co and re.search(r"F(?:E|0)4\.pdf$", f)
           and int(f[:4]) >= 2025]
    ars.sort(key=lambda f: ("FE4" not in f, f))  # English first
    log_lines.append(f"MOPS {co} ({name or '?'}): {len(ars)} annual report file(s) "
                     f"for fiscal 2025+: {', '.join(ars) or 'none'}")
    out = []
    for f in ars[:2]:
        r = _mops("POST", MOPS_POST, data={"step": "9", "kind": "F", "co_id": co, "filename": f})
        m = re.search(r"href='(/pdf/[^']+)'", r.text)
        if not m:
            log_lines.append(f"MOPS: no download link for {f}")
            continue
        out.append({"url": urljoin(MOPS_POST, m.group(1)), "page": page,
                    "title": f"MOPS {co} {name} annual report {f}",
                    "english": "FE4" in f, "exchange_name": name, "mops": True})
    return out, page


def mops_download(url):
    r = _mops("GET", url)
    if r.content[:4] != b"%PDF":
        raise ValueError("MOPS link is not a PDF")
    return r.content


# --------------------------------------------------------------------- IDX

def idx(company, log_lines):
    code = code_of(company["ticker"])
    out = []
    for year in (2025,):
        r = _get(IDX_API.format(year=year, code=code))
        if r.status_code == 403:
            log_lines.append("IDX: blocked (the site's bot check refuses automated requests)")
            return [], IDX_PAGE, True
        r.raise_for_status()
        for res in r.json().get("Results") or []:
            for a in res.get("Attachments") or []:
                name, path = str(a.get("File_Name", "")), str(a.get("File_Path", ""))
                if name.lower().endswith(".pdf") and (AR_FILE.search(name) or "rda" in path.lower()) \
                        and not NOT_AR.search(name):
                    out.append({"url": urljoin("https://www.idx.co.id", path), "page": IDX_PAGE,
                                "title": f"IDX {code} {name}", "english": False,
                                "exchange_name": res.get("NamaEmiten", "")})
    log_lines.append(f"IDX {code}: {len(out)} annual-report PDF(s)")
    return out, IDX_PAGE, False


# --------------------------------------------------------------------- SGX

SGX_PROMPT = """Find the SGX company announcement that contains the ANNUAL REPORT for fiscal year 2025 (or the fiscal year ending in 2026, e.g. FY2025/26) of {company_name} ({ticker}).

Use web search, e.g. site:links.sgx.com "{company_name}" annual report. I need links on links.sgx.com only: either the announcement page (https://links.sgx.com/1.0.0/corporate-announcements/XXXXXXXX) or the PDF attached to it.

Reply with ONLY this JSON, nothing else:
{{"links": ["https://links.sgx.com/1.0.0/corporate-announcements/..."]}}
Up to 5 links, best first. If you find nothing, reply {{"links": []}}."""


def sgx(company, tokens, cfg, log_lines):
    reply, _ = run_claude(SGX_PROMPT.format(company_name=company["company_name"],
                                            ticker=company["ticker"]), cfg, tools="WebSearch")
    try:
        links = parse_json_reply(reply).get("links", []) or []
    except (ValueError, AttributeError):
        links = []
    ids = list(dict.fromkeys(m.group(1).upper() for l in links
                             for m in [SGX_ANN.search(str(l))] if m))
    log_lines.append(f"SGX search: {len(ids)} announcement(s)")
    code = code_of(company["ticker"]) if company["ticker"].upper().endswith(".SI") else ""
    out = []
    for aid in ids[:5]:
        page = f"https://links.sgx.com/1.0.0/corporate-announcements/{aid}"
        try:
            html = _get(page).text
        except Exception as e:
            log_lines.append(f"SGX: couldn't read {page}: {e}")
            continue
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&amp;", "&")
        sec = re.search(r"Securities (.{0,300}?) Stapled Security", text)
        sec = sec.group(1) if sec else text[:1500]
        # the company itself: its SGX stock code ("... - SG1M51904654 - C38U"),
        # or for a foreign issuer, its name
        if code and not re.search(rf"-\s*{re.escape(code)}\b", sec, re.I):
            log_lines.append(f"SGX: {page} is not {code} ({sec[:80]})")
            continue
        if not code and not all(fold(t) in fold(sec) for t in tokens):
            log_lines.append(f"SGX: {page} is not {company['company_name']} ({sec[:80]})")
            continue
        if not re.search(r"annual report", text, re.I):
            log_lines.append(f"SGX: {page} is not an annual-report announcement")
            continue
        issuer = re.search(r"Issuer/ Manager (.{0,120}?) Securities", text)
        for href in re.findall(r'href="(/1\.0\.0/corporate-announcements/[^"]+\.pdf)"', html, re.I):
            name = unquote(href.rsplit("/", 1)[-1])
            if AR_FILE.search(name) and not NOT_AR.search(name):
                out.append({"url": urljoin(page, href), "page": page,
                            "title": f"SGX {name}", "english": True,
                            "exchange_name": issuer.group(1).strip() if issuer else ""})
    log_lines.append(f"SGX: {len(out)} annual-report PDF(s) confirmed for "
                     f"{code or company['company_name']}")
    return out, (out[0]["page"] if out else SGX_PAGE)


# -------------------------------------------------------------------- entry

def candidates(company, tokens, cfg, log_lines):
    """Returns (candidate PDFs, site name, page a person can download from,
    blocked?)."""
    global UA
    ua = cfg["user_agent"]
    UA = f'Mozilla/5.0 (research screener; {ua["name"]} {ua["email"]})'
    country, t = company["country"], company["ticker"].upper()
    site = SITE_NAMES.get(country, "")
    try:
        if country == "Denmark":
            c, page = nasdaq(company, tokens, log_lines)
            return c, site, page, False
        if country == "Taiwan":
            c, page = mops(company, log_lines)
            return c, site, page, False
        if country == "Indonesia":
            return (lambda r: (r[0], site, r[1], r[2]))(idx(company, log_lines))
        if country == "Singapore" or t in SGX_CROSS:
            if country == "Switzerland":
                log_lines.append("SIX Swiss Exchange: no annual-report filing archive; "
                                 "trying the company's secondary SGX listing")
                site = SITE_NAMES["Singapore"] + " (secondary listing)"
            c, page = sgx(company, tokens, cfg, log_lines)
            return c, site, page, False
        if country == "Switzerland":
            log_lines.append("SIX Swiss Exchange: no annual-report filing archive to download from")
            return [], site, "", False
    except ClaudeError:
        raise  # usage limit: stop the run, don't record a failure
    except Exception as e:
        log_lines.append(f"{site}: error {str(e)[:200]}")
        return [], site, "", bool(re.search(r"40[13]|429|Forbidden|rate limit", str(e), re.I))
    return [], "", "", False
