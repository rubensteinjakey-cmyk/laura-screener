"""Step 3: turn each downloaded 10-K into clean text and cut out
Item 1 (Business) and Item 7 (MD&A). No AI usage.

Item 1A (Risk Factors) is never extracted or sent anywhere.

Output, per company, in data/sections/{TICKER}/:
  item1.txt, item7.txt   the section text (quotes are checked against it)
  item7_targeted.txt     "targeted MD&A": only the Item 7 paragraphs that
                         mention Laura-relevant terms (foundation, community,
                         small business, artists...). Much smaller to send.
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
        # if another start heading comes before that end, the later one is
        # the real heading (e.g. a cover-page line "Part I - Items 1, 1A...")
        if any(s < s2 < nxt[0] for s2 in starts):
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
    r"exhibit 13|annual report to shareholders|annual report to stockholders|"
    r"appears? on pages?",
    re.I)


def looks_like_cross_reference(section):
    """True if a section is mostly pointers to another document instead of
    real content: short, with cross-reference wording."""
    wc = word_count(section)
    hits = len(IBR_PATTERNS.findall(section))
    return (wc < 1500 and hits >= 1) or wc < 300, wc, hits


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


# When Item 7 is a cross-reference, it names where the MD&A really is, in
# quotes: JPM says it is "entitled 'Management's discussion and analysis'",
# Wells Fargo says it is in the Annual Report "under 'Financial Review'".
# We look for those titles as headings in Exhibit 13 (if the filing has one)
# or elsewhere in the 10-K itself, and cut up to the financial statements.
MDA_DEFAULT_TITLE = "management's discussion and analysis"
MDA_END = re.compile(r"^(?:quantitative and qualitative disclosures about market risk"
                     r"|(?:consolidated )?financial statements"
                     r"|report of independent registered public accounting firm"
                     r"|management['’]s report on internal control"
                     r"|controls and procedures)\b", re.I)


def quoted_titles(xref_text):
    titles = re.findall(r"[“\"]([^”\"]{4,80})[”\"]", xref_text)
    return [t.strip(" ,.;:") for t in titles]


def find_referenced_mda(source_text, xref_text):
    """Best piece of source_text that starts at a heading named in the
    cross-reference and runs to the next financial-statements heading."""
    titles = quoted_titles(xref_text) + [MDA_DEFAULT_TITLE]
    pats = [re.compile(r"^" + re.escape(t).replace("['’]", "['’]").replace("'", "['’]")
                       + r"\b", re.I) for t in titles]
    lines = line_offsets(source_text)
    starts = [o for o, l in lines if len(l) < 120 and any(p.match(l) for p in pats)]
    ends = [o for o, l in lines if len(l) < 160 and MDA_END.match(l)]
    pieces = []
    for st in starts:
        nxt = [e for e in ends if e > st]
        if nxt:
            pieces.append(source_text[st:nxt[0]])
    return max(pieces, key=len).strip() if pieces else ""


# Safety rule: we never read Risk Factors. Some Annual Reports (e.g. Wells
# Fargo's Exhibit 13) put a "Risk Factors" chapter inside the referenced
# MD&A, so every section is cut off at the first such heading.
RISK_HEADING = re.compile(r"^(?:item\s*1\s*a\b|risk factors\b)", re.I)


def cut_risk_factors(section):
    for off, line in line_offsets(section):
        if off > 0 and len(line) < 80 and RISK_HEADING.match(line):
            return section[:off].strip(), word_count(section[off:])
    return section, 0

# ------------------------------------------------------ targeted MD&A
# Only the MD&A paragraphs that mention these terms are sent to the AI when
# config.yaml says item7_targeted. Quotes are still checked against the FULL
# Item 7 text.
TARGET_TERMS = re.compile(
    r"\b(?:foundations?|philanthrop\w*|charit\w*|communit(?:y|ies)|scholarships?|"
    r"educat\w*|students?|small[- ]business(?:es)?|entrepreneur\w*|creators?|"
    r"artists?|accessib\w*|multilingual)\b", re.I)


NUMBER_LINE = re.compile(r"^(?:[\d$(.\-–—]|(?:thousand|million|billion|trillion|%)\s*$)", re.I)


def targeted_mda(item7):
    """Return (excerpt_text, paragraphs_kept, paragraphs_total)."""
    paras = [p.strip() for p in item7.split("\n") if p.strip()]
    kept = []
    for i, p in enumerate(paras):
        if not TARGET_TERMS.search(p):
            continue
        # a short table label ("Small business customers (in millions)") has
        # its numbers on the next line ("7.4 | 7.0 | 6.4"): keep them together
        # Some layouts put the figure BEFORE the label ("$33" / "billion" /
        # "Credit for U.S. small businesses"), so also keep up to two
        # number/unit lines just before it.
        if len(p.split()) <= 12:
            nxt = paras[i + 1] if i + 1 < len(paras) else ""
            if NUMBER_LINE.match(nxt):
                p = p + "\n" + nxt
            before = []
            for j in (i - 1, i - 2):
                if j >= 0 and NUMBER_LINE.match(paras[j]) and len(paras[j].split()) <= 4:
                    before.insert(0, paras[j])
                else:
                    break
            if before:
                p = "\n".join(before) + "\n" + p
        kept.append(p)
    return "\n\n".join(kept), len(kept), len(paras)

# ------------------------------------------------------ Human Capital part

# "Human Capital" is the usual heading, but some companies use another name
# for the same section (Etsy: "Workforce Access").
HC_START = re.compile(r"^(?:(?:our )?human capital(?: management| resources)?|"
                      r"(?:our )?employees|(?:our )?people(?: and culture)?|"
                      r"(?:our )?workforce(?: access)?|team members|our team)\s*$", re.I)
# Headings that usually come right after Human Capital inside Item 1.
HC_END = re.compile(
    r"^(?:available information|supervision and regulation|regulation|"
    r"government (?:supervision|regulation)|regulatory|competition|properties|"
    r"information about our executive officers|executive officers|"
    r"intellectual property|seasonality|environmental|corporate information|"
    r"where you can find more information|website|segment|"
    r"cybersecurity|legal proceedings|our corporate structure|"
    r"distribution|sales and marketing|research and development)\b", re.I)


PEOPLE_TERMS = re.compile(r"\b(?:employees?|workforce|talent|human capital|"
                          r"our people|team members|volunteer\w*|communit(?:y|ies))\b", re.I)


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
            found = ""
            if which == "item7":
                if exhibit13_docs(meta):
                    if ex13_text is None:
                        ex13_text = get_exhibit13_text(sec, t, meta)
                        (out / "exhibit13.txt").write_text(ex13_text, encoding="utf-8")
                    found, where = find_referenced_mda(ex13_text, section), "Exhibit 13"
                else:
                    found, where = find_referenced_mda(text, section), "elsewhere in the 10-K"
            if found and word_count(found) > max(wc, 1500):
                section = (section + f"\n\n[Referenced section, from {where}]\n\n"
                           + found).strip()
                source = "10-K + Exhibit 13" if where == "Exhibit 13" else "10-K (referenced section)"
                note += f"; used the referenced section {where} ({word_count(found):,} words)"
            else:
                note += "; could not locate the referenced content"
                flag_human(t, "extract", f"{which} incorporated by reference; "
                           "could not locate the referenced content", note)
            info["notes"].append(note)
            log("extract", f"{t}: {note}")

        section, cut = cut_risk_factors(section)
        if cut:
            note = f"{which}: removed {cut:,} words starting at a Risk Factors heading"
            info["notes"].append(note)
            log("extract", f"{t}: {note}")
        (out / f"{which}.txt").write_text(section + "\n", encoding="utf-8")
        info["sections"][which] = {"words": word_count(section), "source": source,
                                   "candidates": cand}

    item7 = (out / "item7.txt").read_text(encoding="utf-8")
    excerpt, kept, total = targeted_mda(item7)
    (out / "item7_targeted.txt").write_text(excerpt + "\n", encoding="utf-8")
    info["sections"]["item7_targeted"] = {
        "words": word_count(excerpt), "source": "paragraphs of item7 with target terms",
        "paragraphs_kept": kept, "paragraphs_total": total}

    item1 = (out / "item1.txt").read_text(encoding="utf-8")
    hc = human_capital_part(item1)
    info["human_capital_words"] = word_count(hc)
    info["human_capital_found"] = bool(hc)
    # Backup for filings that discuss their people without a heading (e.g.
    # XOM): words in Item 1 paragraphs about employees / workforce / talent.
    people = [p for p in item1.split("\n") if PEOPLE_TERMS.search(p)]
    info["people_paragraph_words"] = word_count("\n".join(people))
    (out / "human_capital.txt").write_text(hc + "\n", encoding="utf-8")
    if not hc:
        info["notes"].append("Human Capital subsection not found in Item 1")

    write_json(info_path, info)  # written last: its presence means "done"
    log("extract", f"{t}: item1 {info['sections']['item1']['words']:,} words "
                   f"({info['sections']['item1']['source']}), item7 "
                   f"{info['sections']['item7']['words']:,} words "
                   f"({info['sections']['item7']['source']}), targeted MD&A "
                   f"{info['sections']['item7_targeted']['words']:,} words, human capital "
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
