"""Shared paths and helpers for the global sleeve screen (run_global.py).

Kept apart from the US run: input companies_global.csv, data under
data/global/, outputs under output/global/. The US files are never touched.
"""

import csv

from common import (DATA, OUTPUT, ROOT, _read_flags, _who, _write_flags,
                    NEEDS_HUMAN_FIELDS)

GDATA = DATA / "global"
G_DOCS = GDATA / "docs"          # downloaded 20-F / annual report, per ticker
G_MANUAL = GDATA / "manual"      # PDFs a teammate saved by hand: {ticker}.pdf
G_SECTIONS = GDATA / "sections"  # extracted text sent to the AI
G_SCORES = GDATA / "scores"
G_VERIFIED = GDATA / "verified"
G_REPORTS = GDATA / "reports"    # standalone sustainability reports (second look)
G_FLAGS = GDATA / "needs_human"
GOUT = OUTPUT / "global"

for d in (G_DOCS, G_MANUAL, G_SECTIONS, G_SCORES, G_VERIFIED, G_REPORTS, G_FLAGS, GOUT):
    d.mkdir(parents=True, exist_ok=True)

COMPANIES_GLOBAL = ROOT / "companies_global.csv"
SKIP_FILE = ROOT / "global_skip.csv"


def load_global_companies(country=None, tickers=None):
    with open(COMPANIES_GLOBAL, encoding="utf-8-sig") as f:
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    if country:
        want = {c.strip().lower() for c in country.split(",")}
        rows = [r for r in rows if r["country"].lower() in want]
    if tickers:
        want = {t.strip().upper() for t in tickers.split(",")}
        rows = [r for r in rows if r["ticker"].upper() in want]
    return rows


def skipped():
    """global_skip.csv (ticker,reason): kept on the list but not scored."""
    if not SKIP_FILE.exists():
        return {}
    with open(SKIP_FILE, encoding="utf-8-sig") as f:
        return {r["ticker"].strip().upper(): r["reason"].strip() for r in csv.DictReader(f)}


def manual_pdf(ticker, suffix=""):
    """A PDF a teammate saved in data/global/manual/: {ticker}{suffix}.pdf, or
    without the exchange suffix (UNVR.JK -> UNVR.pdf, GMAB_sustainability.pdf)."""
    for name in (ticker, ticker.split(".")[0]):
        path = G_MANUAL / f"{name}{suffix}.pdf"
        if path.exists():
            return path
    return None


def add_global_args(p):
    p.add_argument("--country", default="", help="e.g. Denmark (comma-separated ok)")
    p.add_argument("--tickers", default="", help="e.g. GMAB,SIKA.SW")


# ---- needs-human list for the global run (separate from the US one)

def _flag_file():
    return G_FLAGS / f"{_who()}.csv"


def gflag(ticker, step, reason, details=""):
    from datetime import datetime
    rows = _read_flags(_flag_file())
    if any((r["ticker"], r["step"], r["reason"]) == (ticker, step, reason) for r in rows):
        return
    rows.append({"ticker": ticker, "step": step, "reason": reason, "details": details,
                 "when": f"{datetime.now():%Y-%m-%d %H:%M}", "who": _who()})
    _write_flags(_flag_file(), rows)


def gclear(ticker, step):
    rows = [r for r in _read_flags(_flag_file())
            if not (r["ticker"] == ticker and r["step"] == step)]
    _write_flags(_flag_file(), rows)


def read_gflags():
    rows = []
    for f in sorted(G_FLAGS.glob("*.csv")):
        rows += _read_flags(f)
    return rows


__all__ = ["GDATA", "G_DOCS", "G_MANUAL", "G_SECTIONS", "G_SCORES", "G_VERIFIED",
           "G_REPORTS", "GOUT", "load_global_companies", "skipped", "add_global_args",
           "gflag", "gclear", "read_gflags", "manual_pdf", "NEEDS_HUMAN_FIELDS"]
