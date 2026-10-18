"""Shared helpers used by every step: paths, config, company list, logging,
SEC requests and the needs_human list."""

import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FILINGS = DATA / "filings"
SECTIONS = DATA / "sections"
SCORES = DATA / "scores"
VERIFIED = DATA / "verified"
REPORTS = DATA / "reports"
OUTPUT = ROOT / "output"
LOGS = ROOT / "logs"

for d in (FILINGS, SECTIONS, SCORES, VERIFIED, REPORTS, OUTPUT, LOGS):
    d.mkdir(parents=True, exist_ok=True)


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_companies(start=None, end=None):
    """Read companies.csv. start/end are 1-based row numbers (inclusive)."""
    with open(ROOT / "companies.csv", encoding="utf-8-sig") as f:
        rows = [
            {k.strip(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(f)
        ]
    rows = [r for r in rows if r.get("ticker")]
    for i, r in enumerate(rows, start=1):
        r["row"] = i
        r["ticker"] = r["ticker"].upper()
    if start or end:
        s = start or 1
        e = end or len(rows)
        rows = [r for r in rows if s <= r["row"] <= e]
    return rows


def add_batch_args(parser, cfg):
    parser.add_argument("--start", type=int, default=cfg["batch"]["start"],
                        help="first row of companies.csv to process (1-based)")
    parser.add_argument("--end", type=int, default=cfg["batch"]["end"],
                        help="last row of companies.csv to process")
    parser.add_argument("--tickers", default="",
                        help="optional comma list, e.g. JPM,BAC (overrides start/end)")


def select_companies(args):
    if getattr(args, "tickers", ""):
        wanted = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
        return [c for c in load_companies() if c["ticker"] in wanted]
    return load_companies(args.start, args.end)


def log(step, msg):
    """Print a message and also append it to logs/{step}.log."""
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOGS / f"{step}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    """Write JSON safely: write to a temp file, then rename, so a crash
    never leaves a half-written file behind."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------- needs-human list ----------------
# Each teammate writes to their own file, data/needs_human/{name}.csv, so two
# people never edit the same file in GitHub. rank.py combines them all into
# output/needs_human.csv.

NEEDS_HUMAN_DIR = DATA / "needs_human"
NEEDS_HUMAN_DIR.mkdir(parents=True, exist_ok=True)
NEEDS_HUMAN_FIELDS = ["ticker", "step", "reason", "details", "when", "who"]


def _who():
    name = load_config()["user_agent"].get("name", "unknown")
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "unknown"


def _my_flag_file():
    return NEEDS_HUMAN_DIR / f"{_who()}.csv"


def flag_human(ticker, step, reason, details=""):
    """Add a row to the needs-human list (skips exact duplicates)."""
    rows = _read_flags(_my_flag_file())
    key = (ticker, step, reason)
    if any((r["ticker"], r["step"], r["reason"]) == key for r in rows):
        return
    rows.append({"ticker": ticker, "step": step, "reason": reason,
                 "details": details, "when": f"{datetime.now():%Y-%m-%d %H:%M}",
                 "who": _who()})
    _write_flags(_my_flag_file(), rows)


def clear_human_flags(ticker, step):
    """Remove old flags for a ticker+step (called when a step is redone)."""
    rows = [r for r in _read_flags(_my_flag_file())
            if not (r["ticker"] == ticker and r["step"] == step)]
    _write_flags(_my_flag_file(), rows)


def read_needs_human():
    """Everyone's flags combined."""
    rows = []
    for f in sorted(NEEDS_HUMAN_DIR.glob("*.csv")):
        rows += _read_flags(f)
    return rows


def _read_flags(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_flags(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=NEEDS_HUMAN_FIELDS)
        w.writeheader()
        w.writerows(rows)


# ---------------- polite SEC requests ----------------

class Sec:
    """Downloads from sec.gov with the required User-Agent, a pause between
    requests, and polite retries."""

    def __init__(self, cfg):
        ua = cfg["user_agent"]
        if not ua.get("name") or "@" not in ua.get("email", ""):
            sys.exit("Set your name and email under user_agent in config.yaml first.")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f'{ua["name"]} {ua["email"]}',
            "Accept-Encoding": "gzip, deflate",
        })
        self.delay = float(cfg.get("sec_delay_seconds", 0.15))
        self._last = 0.0

    def get(self, url, tries=5):
        for attempt in range(1, tries + 1):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.session.get(url, timeout=60)
                if r.status_code == 200:
                    return r
                if r.status_code == 404:
                    r.raise_for_status()
                msg = f"HTTP {r.status_code}"
            except requests.HTTPError:
                raise
            except requests.RequestException as e:
                msg = str(e)
            backoff = 2 ** attempt
            print(f"  request failed ({msg}); retrying in {backoff}s: {url}")
            time.sleep(backoff)
        raise RuntimeError(f"Gave up after {tries} tries: {url}")
