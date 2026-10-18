"""Step 2: download each company's most recent 10-K from SEC EDGAR.

No AI usage. Everything is cached in data/filings/{TICKER}/, so a filing is
never downloaded twice.

Run:  python src/download.py            (all companies in the batch range)
      python src/download.py --tickers JPM,BAC
"""

import argparse

from bs4 import BeautifulSoup

from common import (FILINGS, ROOT, Sec, add_batch_args, clear_human_flags, flag_human,
                    load_config, log, read_json, select_companies, write_json)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{acc}-index.htm"


def ticker_to_cik(sec):
    """Map ticker -> CIK using the SEC's ticker file (cached for the run)."""
    path = FILINGS / "company_tickers.json"
    if not path.exists():
        write_json(path, sec.get(TICKER_MAP_URL).json())
    data = read_json(path)
    # The SEC writes class shares as BRK-B; people often type BRK.B.
    return {v["ticker"].upper().replace("-", "."): int(v["cik_str"])
            for v in data.values()}


def cik_overrides():
    """Optional cik_overrides.csv (ticker,cik,reason): for tickers whose SEC
    ID changed, e.g. a new holding company that hasn't filed a 10-K yet."""
    path = ROOT / "cik_overrides.csv"
    if not path.exists():
        return {}
    import csv
    with open(path, encoding="utf-8-sig") as f:
        return {r["ticker"].strip().upper(): int(r["cik"]) for r in csv.DictReader(f)
                if r.get("ticker") and r.get("cik")}


def filings_table(sec, cik):
    """All filings for a company as a list of dicts. The 'recent' block only
    holds the latest ~1000 filings; big banks file thousands of other forms,
    so we also read the older pages if no 10-K is found in 'recent'."""
    sub = sec.get(SUBMISSIONS_URL.format(cik=cik)).json()

    def rows(block):
        keys = list(block.keys())
        return [dict(zip(keys, vals)) for vals in zip(*block.values())]

    table = rows(sub["filings"]["recent"])
    if not any(r["form"] == "10-K" for r in table):
        for extra in sub["filings"].get("files", []):
            table += rows(sec.get(SUBMISSIONS_PAGE_URL.format(name=extra["name"])).json())
            if any(r["form"] == "10-K" for r in table):
                break
    return sub, table


def pick_10k(table):
    """Most recent original 10-K; a 10-K/A only if there is no original."""
    originals = [r for r in table if r["form"] == "10-K"]
    if originals:
        return max(originals, key=lambda r: r["filingDate"]), False
    amendments = [r for r in table if r["form"] == "10-K/A"]
    if amendments:
        return max(amendments, key=lambda r: r["filingDate"]), True
    return None, False


def parse_filing_index(html, cik, acc_nodash):
    """Read the table of documents from a filing's -index.htm page."""
    soup = BeautifulSoup(html, "lxml")
    docs = []
    for table in soup.find_all("table", class_="tableFile"):
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            link = tr.find("a")
            if len(cells) < 4 or not link:
                continue
            name = link.get_text(strip=True).split()[0]
            docs.append({
                "description": cells[1],
                "name": name,
                "type": cells[3],
                "url": ARCHIVE_URL.format(cik=cik, acc=acc_nodash, doc=name),
            })
    return docs


def download_company(sec, cik_map, company):
    t = company["ticker"]
    folder = FILINGS / t
    meta_path = folder / "meta.json"
    if meta_path.exists():
        log("download", f"{t}: already downloaded, skipping")
        return

    overrides = cik_overrides()
    cik = overrides.get(t) or cik_map.get(t.replace("-", "."))
    if t in overrides:
        log("download", f"{t}: using CIK {cik} from cik_overrides.csv")
    if not cik:
        log("download", f"{t}: ticker not found in SEC list")
        flag_human(t, "download", "ticker not found in SEC ticker list")
        return

    sub, table = filings_table(sec, cik)
    filing, is_amendment = pick_10k(table)
    if not filing:
        log("download", f"{t}: no 10-K found")
        flag_human(t, "download", "no 10-K found on EDGAR",
                   f"SEC name: {sub.get('name', '')}. If this is a new holding company, "
                   "add the predecessor's CIK to cik_overrides.csv")
        return
    if is_amendment:
        log("download", f"{t}: no original 10-K, using 10-K/A {filing['accessionNumber']}")
        flag_human(t, "download", "used 10-K/A (amendment); no original 10-K",
                   filing["accessionNumber"])

    acc = filing["accessionNumber"]
    acc_nodash = acc.replace("-", "")
    doc = filing["primaryDocument"]
    folder.mkdir(parents=True, exist_ok=True)

    html = sec.get(ARCHIVE_URL.format(cik=cik, acc=acc_nodash, doc=doc)).content
    (folder / "primary.htm").write_bytes(html)

    # The filing index page lists every document in the filing with its
    # type (10-K, EX-13, EX-21...). extract.py uses it to find Exhibit 13 when
    # content is "incorporated by reference".
    index_html = sec.get(INDEX_URL.format(cik=cik, acc_nodash=acc_nodash, acc=acc)).text
    documents = parse_filing_index(index_html, cik, acc_nodash)

    report_date = filing.get("reportDate", "")
    meta = {
        "ticker": t,
        "company_name": company["company_name"],
        "sector": company["sector"],
        "cik": cik,
        "sec_name": sub.get("name", ""),
        "form": filing["form"],
        "is_amendment": is_amendment,
        "accession_number": acc,
        "filing_date": filing["filingDate"],
        "period_of_report": report_date,
        "fiscal_year": int(report_date[:4]) if report_date else None,
        "primary_document": doc,
        "primary_url": ARCHIVE_URL.format(cik=cik, acc=acc_nodash, doc=doc),
        "documents": documents,
    }
    write_json(meta_path, meta)  # written last: its presence means "done"
    clear_human_flags(t, "download")
    if is_amendment:
        flag_human(t, "download", "used 10-K/A (amendment); no original 10-K", acc)
    log("download", f"{t}: {meta['form']} filed {meta['filing_date']} "
                    f"(FY {meta['fiscal_year']}), accession {acc}, {len(html):,} bytes")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    add_batch_args(p, cfg)
    args = p.parse_args()
    sec = Sec(cfg)
    cik_map = ticker_to_cik(sec)
    for c in select_companies(args):
        try:
            download_company(sec, cik_map, c)
        except Exception as e:  # keep going; log the problem for a human
            log("download", f"{c['ticker']}: ERROR {e}")
            flag_human(c["ticker"], "download", "download error", str(e)[:300])


if __name__ == "__main__":
    main()
