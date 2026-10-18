"""Step 3: turn each downloaded 10-K into clean text and cut out
Item 1 (Business) and Item 7 (MD&A). No AI usage.

Item 1A (Risk Factors) is never extracted or sent anywhere.

Output, per company, in data/sections/{TICKER}/:
  item1.txt, item7.txt   the section text (this exact text is what the AI
                         reads and what quotes are checked against)
  info.json              word counts, Human Capital word count, and notes
                         (e.g. "incorporated by reference" found)

Run:  python src/extract.py            (batch range from config)
      python src/extract.py --tickers JPM --force   (redo one company)
"""

import argparse
import re

from bs4 import BeautifulSoup, NavigableString

from common import (FILINGS, SECTIONS, Sec, add_batch_args, clear_human_flags,
                    flag_human, load_config, log, read_json, select_companies,
                    write_json)

# ---------------------------------------------------------------- HTML -> text

BLOCK_TAGS = ["p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "table", "section", "article", "ul", "ol", "center"]


def html_to_text(html):
    """Convert 10-K HTML to plain text, one paragraph per line, blank line
    between paragraphs. Table cells are separated by ' | '."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head", "title"]):
        tag.decompose()
    # Inline XBRL puts hidden machine-readable data in <ix:header>; drop it.
    for tag in soup.find_all(re.compile(r"^ix:header$", re.I)):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        tag.decompose()
    for td in soup.find_all(["td", "th"]):
        td.append(NavigableString(" | "))
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString("\n"))
        tag.append(NavigableString("\n"))
    text = soup.get_text("")
    return tidy(text)


def tidy(text):
    text = text.replace("\xa0", " ").replace("​", "").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        line = re.sub(r"^(\|\s*)+", "", line)          # leading empty cells
        line = re.sub(r"(\s*\|)+\s*$", "", line)       # trailing empty cells
        line = re.sub(r"(\|\s*){2,}", "| ", line)      # runs of empty cells
        lines.append(line.strip())
    out, blank = [], 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        elif blank == 0:
            out.append("")
            blank = 1
    # Lines that are just a page number or "Table of Contents" are page
    # furniture, not content.
    out = [l for l in out if not re.fullmatch(r"(\d{1,3}|table of contents|"
                                              r"[ivx]{1,5})", l, re.I)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", text))

# --------------------------------------------------------- finding the items

# A heading line looks like "Item 1. Business", "ITEM 1 — BUSINESS",
# "Part I, Item 1: Business", "Items 1 and 2. Business and Properties" or
# "Item 1. | Business" (table layout). It must be at the start of a line.
PREFIX = r"^(?:part\s+i{1,2}\s*[,.\-–—:|]?\s*)?items?\s*"
HEADINGS = {
    "item1_start": re.compile(PREFIX + r"1(?:\s*(?:and|&|,)\s*2)?(?![0-9a-z])", re.I),
    "item1_end":   re.compile(PREFIX + r"1\s*a(?![a-z0-9])", re.I),
    "item7_start": re.compile(PREFIX + r"7(?![0-9a-z])", re.I),
    "item7_end":   re.compile(PREFIX + r"(?:7\s*a|8)(?![a-z0-9])", re.I),
}
# Lines like "Item 1. Business, page 5" inside a cross-reference index are
# fine to match; the "longest real content" rule below sorts them out.


def line_offsets(text):
    """(start_offset, line) for every line."""
    pos, out = 0, []
    for line in text.split("\n"):
        out.append((pos, line))
        pos += len(line) + 1
    return out


def find_section(text, start_key, end_key):
    """Return (section_text, candidates_info). Items appear several times
    (table of contents, cross-reference index, the real thing), so we try
    every start heading, cut to the next end heading, and keep the longest
    piece. A table-of-contents hit is only a line or two long."""
    lines = line_offsets(text)
    starts = [o for o, l in lines if HEADINGS[start_key].match(l)]
    ends = [o for o, l in lines if HEADINGS[end_key].match(l)]
    pieces = []
    for s in starts:
        nxt = [e for e in ends if e > s]
        if not nxt:
            continue
        piece = text[s:nxt[0]].strip()
        pieces.append(piece)
    if not pieces:
        return "", {"starts": len(starts), "ends": len(ends)}
    best = max(pieces, key=len)
    return best, {"starts": len(starts), "ends": len(ends),
                  "lengths": sorted((word_count(p) for p in pieces), reverse=True)}

# ------------------------------------------------ "incorporated by reference"

IBR_PATTERNS = re.compile(
    r"incorporated (?:herein )?by reference|"
    r"(?:is|are) included (?:in|under)|"
    r"see (?:the )?(?:information|discussion)?\s*(?:under|in)|"
    r"exhibit 13|annual report to shareholders|annual report to stockholders",
    re.I)


def looks_like_cross_reference(section):
    """True if a section is mostly pointers to another document instead of
    real content: short, with cross-reference wording."""
    wc = word_count(section)
    hits = len(IBR_PATTERNS.findall(section))
    return wc < 1500 and hits >= 1, wc, hits


def exhibit13_docs(meta):
    return [d for d in meta.get("documents", [])
            if re.match(r"EX-13", d.get("type", ""), re.I)
            and d["name"].lower().endswith((".htm", ".html"))]


def get_exhibit13_text(sec, ticker, meta):
    """Download (cached) every Exhibit 13 document and return its text."""
    texts = []
    for d in exhibit13_docs(meta):
        path = FILINGS / ticker / d["name"]
        if not path.exists():
            path.write_bytes(sec.get(d["url"]).content)
        texts.append(html_to_text(path.read_bytes()))
    return "\n\n".join(texts)


# Headings an Annual Report (Exhibit 13) uses for the material a bank's
# 10-K Item 1 / Item 7 points to. Used only when a section is incorporated
# by reference.
EX13_ITEM7_START = re.compile(r"^management['’]s discussion and analysis\b", re.I)
EX13_ITEM7_END = re.compile(r"^(?:quantitative and qualitative disclosures about market risk"
                            r"|(?:consolidated )?financial statements"
                            r"|report of independent registered public accounting firm"
                            r"|management['’]s report on internal control)\b", re.I)


def find_in_exhibit13(ex_text, which):
    """Best-effort: find the MD&A in an Annual Report exhibit. For Item 1 we
    don't guess (see notes) - the 10-K's own Item 1 is kept and the company
    is flagged for a human if it is only a cross-reference."""
    if which != "item7":
        return ""
    lines = line_offsets(ex_text)
    starts = [o for o, l in lines if EX13_ITEM7_START.match(l) and len(l) < 120]
    ends = [o for o, l in lines if EX13_ITEM7_END.match(l) and len(l) < 120]
    pieces = []
    for s in starts:
        nxt = [e for e in ends if e > s]
        pieces.append(ex_text[s:nxt[0]] if nxt else "")
    return max(pieces, key=len).strip() if pieces else ""

# ------------------------------------------------------ Human Capital part

HC_START = re.compile(r"^(?:our )?human capital(?: management| resources)?\b", re.I)
# Headings that usually come right after Human Capital inside Item 1.
HC_END = re.compile(
    r"^(?:available information|supervision and regulation|regulation|"
    r"government regulation|regulatory|competition|properties|"
    r"information about our executive officers|executive officers|"
    r"intellectual property|seasonality|environmental|corporate information|"
    r"where you can find more information|website|segment|"
    r"cybersecurity|legal proceedings|our corporate structure|"
    r"distribution|sales and marketing|research and development)\b", re.I)


def human_capital_part(item1):
    """The Human Capital subsection of Item 1: from its heading to the next
    typical Item 1 heading (or the end of Item 1)."""
    lines = line_offsets(item1)
    starts = [o for o, l in lines if HC_START.match(l) and len(l.split()) <= 8]
    if not starts:
        return ""
    s = starts[0]
    ends = [o for o, l in lines
            if o > s and HC_END.match(l) and len(l.split()) <= 10]
    return item1[s:ends[0] if ends else len(item1)].strip()

# ------------------------------------------------------------------- driver


def extract_company(sec, company, force=False):
    t = company["ticker"]
    out = SECTIONS / t
    info_path = out / "info.json"
    if info_path.exists() and not force:
        log("extract", f"{t}: already extracted, skipping")
        return
    meta_path = FILINGS / t / "meta.json"
    if not meta_path.exists():
        log("extract", f"{t}: not downloaded yet, skipping")
        return
    meta = read_json(meta_path)
    clear_human_flags(t, "extract")
    out.mkdir(parents=True, exist_ok=True)

    text = html_to_text((FILINGS / t / "primary.htm").read_bytes())
    (out / "full_10k.txt").write_text(text, encoding="utf-8")

    info = {"ticker": t, "accession_number": meta["accession_number"],
            "filing_date": meta["filing_date"], "fiscal_year": meta["fiscal_year"],
            "full_text_words": word_count(text), "sections": {}, "notes": []}
    ex13_text = None

    for which in ("item1", "item7"):
        section, cand = find_section(text, f"{which}_start", f"{which}_end")
        source = "10-K"
        is_xref, wc, hits = looks_like_cross_reference(section) if section else (True, 0, 0)

        if is_xref:
            note = (f"{which}: 10-K section is {'missing' if not section else f'only {wc} words'}"
                    f" with {hits} cross-reference phrase(s)")
            if exhibit13_docs(meta):
                if ex13_text is None:
                    ex13_text = get_exhibit13_text(sec, t, meta)
                    (out / "exhibit13.txt").write_text(ex13_text, encoding="utf-8")
                ex_part = find_in_exhibit13(ex13_text, which)
                if ex_part and word_count(ex_part) > max(wc, 1500):
                    # keep the 10-K's own text too (e.g. Human Capital is often
                    # in the 10-K itself even when the rest is in Exhibit 13)
                    section = (section + "\n\n[From Exhibit 13, Annual Report]\n\n"
                               + ex_part).strip()
                    source = "10-K + Exhibit 13"
                    note += f"; used Exhibit 13 ({word_count(ex_part)} words)"
                else:
                    note += "; Exhibit 13 exists but the matching part wasn't found"
                    flag_human(t, "extract", f"{which} incorporated by reference; "
                               "could not locate it in Exhibit 13", note)
            else:
                note += "; no Exhibit 13 in this filing"
                flag_human(t, "extract", f"{which} not found cleanly", note)
            info["notes"].append(note)
            log("extract", f"{t}: {note}")

        (out / f"{which}.txt").write_text(section + "\n", encoding="utf-8")
        info["sections"][which] = {"words": word_count(section), "source": source,
                                   "candidates": cand}

    item1 = (out / "item1.txt").read_text(encoding="utf-8")
    hc = human_capital_part(item1)
    info["human_capital_words"] = word_count(hc)
    info["human_capital_found"] = bool(hc)
    (out / "human_capital.txt").write_text(hc + "\n", encoding="utf-8")
    if not hc:
        info["notes"].append("Human Capital subsection not found in Item 1")

    write_json(info_path, info)  # written last: its presence means "done"
    log("extract", f"{t}: item1 {info['sections']['item1']['words']:,} words "
                   f"({info['sections']['item1']['source']}), item7 "
                   f"{info['sections']['item7']['words']:,} words "
                   f"({info['sections']['item7']['source']}), human capital "
                   f"{info['human_capital_words']} words")


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    add_batch_args(p, cfg)
    p.add_argument("--force", action="store_true", help="re-extract even if done")
    args = p.parse_args()
    sec = Sec(cfg)
    for c in select_companies(args):
        try:
            extract_company(sec, c, args.force)
        except Exception as e:
            log("extract", f"{c['ticker']}: ERROR {e}")
            flag_human(c["ticker"], "extract", "extraction error", str(e)[:300])


if __name__ == "__main__":
    main()
