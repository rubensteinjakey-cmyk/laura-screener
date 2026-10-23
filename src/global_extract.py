"""Global step 2: pull the sections the AI reads. No AI usage.

20-F (GMAB, ALC, TLK):
  Item 4 "Information on the Company"   (in place of Item 1)
  Item 5 "Operating and Financial Review" (the MD&A: targeted paragraphs only,
                                          the same keyword rule as the US run)
  Item 6.D "Employees"
  Item 3.D "Risk Factors" is never extracted, and every section is cut at
  any Risk Factors heading (same guard as the US run).

Annual report (PDF), treated like the targeted MD&A:
  - chapters picked from the PDF's bookmarks or printed table of contents:
    business overview / strategy, people / employees / human capital, and
    community / education / inclusion / sustainability chapters;
  - plus keyword paragraphs from the rest of the report;
  - risk chapters, financial statements and notes are skipped;
  - over global_max_words (~40,000), the lowest-relevance keyword paragraphs
    are dropped first (then the lowest-relevance chapter pages), and it's logged.
  If no chapters can be found cleanly, the company goes in needs_human.csv
  and is not scored.

Writes data/global/sections/{ticker}/: sent.txt (exactly what the AI reads,
and what quotes are checked against), info.json (words sent, chapters used,
language, people-section words).

Run:  python src/global_extract.py --tickers GMAB,SIKA.SW [--force]
"""

import argparse
import logging
import re

from pypdf import PdfReader

from common import load_config, log, read_json, write_json
from extract import (HEADINGS, PREFIX, cut_risk_factors, find_section, html_to_text,
                     targeted_mda, word_count)
from global_common import (G_DOCS, G_SECTIONS, add_global_args, gclear, gflag,
                           load_global_companies, skipped)
from second_look import chapters_from_outline, chapters_from_toc_page

logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings are noise here

STEP = "global_extract"
MIN_NARRATIVE_WORDS = 5000  # less than this = not the full annual report

# ---------------------------------------------------------------- 20-F

HEADINGS.update({
    # 20-Fs often run the title into the number ("ITEM 4INFORMATION ON THE
    # COMPANY"), so a letter may follow the number, except Item 4A.
    "f20_item4_start": re.compile(PREFIX + r"4(?!\s*a(?![a-z]))(?![0-9])", re.I),
    "f20_item4_end":   re.compile(PREFIX + r"(?:4\s*a(?![a-z])|5(?![0-9]))", re.I),
    "f20_item5_start": re.compile(PREFIX + r"5(?![0-9])", re.I),
    "f20_item5_end":   re.compile(PREFIX + r"6(?![0-9])", re.I),
    "f20_item6_start": re.compile(PREFIX + r"6(?![0-9])", re.I),
    "f20_item6_end":   re.compile(PREFIX + r"7(?![0-9])", re.I),
})
EMPLOYEES_START = re.compile(r"^(?:(?:item\s*)?6\s*\.?\s*)?d\s*[.:)\-–—]?\s*employees\b", re.I)
EMPLOYEES_END = re.compile(r"^(?:(?:item\s*)?6\s*\.?\s*)?e\s*[.:)\-–—]?\s*share\s+ownership\b",
                           re.I)


def employees_part(item6):
    lines = item6.split("\n")
    starts = [i for i, l in enumerate(lines) if len(l) < 80 and EMPLOYEES_START.match(l.strip())]
    if not starts:
        return ""
    s = starts[-1]  # the heading itself, not a reference in Item 6's intro
    ends = [i for i, l in enumerate(lines) if i > s and len(l) < 80
            and EMPLOYEES_END.match(l.strip())]
    return "\n".join(lines[s:ends[0] if ends else len(lines)]).strip()


def extract_20f(t, folder, out, info):
    text = html_to_text((folder / "primary.htm").read_bytes())
    (out / "full.txt").write_text(text, encoding="utf-8")
    parts = {}
    for key in ("item4", "item5", "item6"):
        sec, _ = find_section(text, f"f20_{key}_start", f"f20_{key}_end")
        sec, cut = cut_risk_factors(sec)
        if cut:
            info["notes"].append(f"{key}: removed {cut:,} words at a Risk Factors heading")
        parts[key] = sec
    item5_targeted, kept, total = targeted_mda(parts["item5"])
    emp = employees_part(parts["item6"])
    labeled = [("20-F Item 4: Information on the Company", parts["item4"]),
               ("20-F Item 5: Operating and Financial Review (targeted paragraphs)",
                item5_targeted),
               ("20-F Item 6.D: Employees", emp)]
    for name, body in labeled:
        info["sections"][name] = word_count(body)
    if word_count(parts["item4"]) < 1000:
        return None, f"20-F Item 4 not found cleanly ({word_count(parts['item4'])} words)"
    if not emp:
        info["notes"].append("20-F Item 6.D Employees not found")
    info["people_words"] = word_count(emp)
    info["chapters"] = [n for n, b in labeled if b.strip()]
    sent = "\n\n".join(f'<section name="{n}">\n{b.strip()}\n</section>'
                       for n, b in labeled if b.strip())
    # quotes are checked against Item 4 + the FULL Item 5 + Item 6.D
    verify_text = "\n\n".join([parts["item4"], parts["item5"], emp])
    return (sent, verify_text), ""


# --------------------------------------------------------- annual report

WANT = re.compile(
    r"overview|strateg|business model|our business|about (?:us|the (?:company|group|bank))|"
    r"at a glance|highlights|who we are|chair(?:man|person|woman)?|ceo|president|"
    r"letter|message|statement from|review of (?:the )?year|"
    r"people|employee|workforce|talent|human capital|human resources|our team|colleague|"
    r"communit|education|learning|inclusi|diversity|sustainab|esg|social|responsib|"
    r"impact|stakeholder|culture|well-?being|philanthrop|foundation|csr|human rights|"
    # common local-language chapter words
    r"karyawan|sumber daya manusia|keberlanjutan|tanggung jawab sosial|"
    r"永續|人才|員工|社會|公益|"
    r"medarbejder|bæredygtighed|mitarbeit|nachhaltig", re.I)
SKIP = re.compile(
    r"risk|financial statements?|notes to|consolidated (?:statement|income|balance|cash)|"
    r"auditor|audit report|independent|balance sheet|income statement|cash flow|"
    r"statement of (?:financial|comprehensive|changes|profit)|remuneration|"
    r"compensation (?:report|awarded|paid)|"
    r"parent company|financial review|appendix|glossary|shareholder information|"
    r"risiko|laporan keuangan|財務報表|風險", re.I)
PEOPLE = re.compile(r"people|employee|workforce|talent|human capital|human resources|"
                    r"our team|colleague|karyawan|sumber daya manusia|人才|員工|medarbejder|"
                    r"mitarbeit", re.I)
GLOBAL_TERMS = re.compile(
    r"\b(?:foundations?|philanthrop\w*|charit\w*|communit(?:y|ies)|scholarships?|"
    r"educat\w*|students?|schools?|small[- ]business(?:es)?|sme|msmes?|entrepreneur\w*|"
    r"start-?ups?|creators?|artists?|arts?|cultur\w*|museums?|music|design|"
    r"accessib\w*|multilingual|inclusi\w*|diversity|volunteer\w*|donat\w*|"
    r"human rights|forced labou?r|child labou?r|discriminat\w*|harass\w*|"
    r"tobacco|cigarettes?|coal|weapons?|munitions?|defen[cs]e)\b|"
    r"karyawan|pendidikan|masyarakat|komunitas|beasiswa|umkm|"
    r"員工|教育|社區|公益|多元|共融|人才|"
    r"medarbejder|uddannelse|samfund|mitarbeit|bildung|gemeinschaft", re.I)


def page_is_numbers(text):
    """Financial tables: mostly digits."""
    chars = [c for c in text if not c.isspace()]
    return bool(chars) and sum(c.isdigit() for c in chars) / len(chars) > 0.30


def paragraphs(page_text):
    """Split PDF page text into paragraph-sized pieces: blank lines if there
    are any, else runs of lines that end at a sentence end."""
    if re.search(r"\n\s*\n", page_text):
        return [p.strip() for p in re.split(r"\n\s*\n", page_text) if p.strip()]
    out, cur = [], []
    for line in page_text.split("\n"):
        cur.append(line)
        if re.search(r"[.!?。:]\s*$", line) and len(" ".join(cur).split()) >= 25:
            out.append("\n".join(cur).strip())
            cur = []
    if cur:
        out.append("\n".join(cur).strip())
    return [p for p in out if p]


def chapters_all_levels(reader):
    """Every bookmark at every level, each with its page range (to the next
    bookmark at the same or a higher level). A root bookmark that spans most
    of the document (e.g. "Sika Business Year 2025") is dropped."""
    n = len(reader.pages)
    flat = []

    def walk(items, depth):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(it)
            except Exception:
                continue
            if isinstance(page, int):
                flat.append((depth, str(it.title).strip(), page))

    try:
        walk(reader.outline, 0)
    except Exception:
        return []
    out = []
    for i, (depth, title, start) in enumerate(flat):
        end = next((p for d, _, p in flat[i + 1:] if d <= depth and p > start), n)
        if end - start > 0.6 * n and n > 20:
            continue
        out.append({"title": title, "start": start, "end": max(end, start + 1), "depth": depth})
    return out


CONTENTS_HEAD = re.compile(r"\b(?:table of )?contents\b|daftar isi|目錄|目录|indhold|inhalt|"
                           r"sommaire|innehåll", re.I)
NUM_ONLY = re.compile(r"^\s*(\d{1,3})\s*$")
TITLE_THEN_NUM = re.compile(r"^\s*(?:[\dIVX]+(?:\.\d+)*\.?\s+)?([^\d\s].{1,88}?)[\s.·…]+(\d{1,3})\s*$")
NUM_THEN_TITLE = re.compile(r"^\s*(\d{1,3})\s+([^\d\s].{1,88}?)\s*$")


def chapters_from_contents_page(reader):
    """Read a printed contents page in its common layouts: "Overview 5",
    "1. Letter to Shareholders  1", "5 Overview", or a title line followed by
    a line holding only the page number. Printed page numbers are mapped to
    PDF pages by finding the titles (also handles two printed pages per PDF
    page). Returns [] if it can't do this cleanly."""
    n = len(reader.pages)
    texts = {}

    def text(i):
        if i not in texts:
            texts[i] = (reader.pages[i].extract_text() or "") if 0 <= i < n else ""
        return texts[i]

    pages = [i for i in range(min(15, n)) if CONTENTS_HEAD.search(text(i))]
    if not pages:
        return []
    first = pages[0]
    entries = []
    for i in (first, first + 1):
        lines = [l.strip() for l in text(i).split("\n")]
        for k, line in enumerate(lines):
            if not line or CONTENTS_HEAD.fullmatch(line.lower()):
                continue
            m = TITLE_THEN_NUM.match(line)
            if m and re.search(r"[A-Za-z\u4e00-\u9fff]{3}", m.group(1)):
                entries.append((m.group(1).strip(" .·"), int(m.group(2))))
                continue
            m = NUM_THEN_TITLE.match(line)
            if m and re.search(r"[A-Za-z\u4e00-\u9fff]{3}", m.group(2)):
                entries.append((m.group(2).strip(), int(m.group(1))))
                continue
            m = NUM_ONLY.match(line)
            if m and k > 0 and re.search(r"[A-Za-z\u4e00-\u9fff]{3}", lines[k - 1]) \
                    and not NUM_ONLY.match(lines[k - 1]):
                entries.append((lines[k - 1].strip(), int(m.group(1))))
    seen, clean = set(), []
    for title, num in entries:
        key = title.lower()[:30]
        if 1 <= num <= 2 * n + 5 and key not in seen and len(title) >= 3:
            seen.add(key)
            clean.append((title, num))
    if len(clean) < 4:
        return []

    def hits(mapper):
        found = 0
        for title, num in clean[:12]:
            words = title.lower()[:22]
            if any(words in text(mapper(num) + d).lower() for d in (0, 1)):
                found += 1
        return found

    best, best_hits = None, 0
    for scale in (1.0, 0.5):
        for off in range(-4, 12):
            mapper = (lambda num, s=scale, o=off: int((num - 1) * s) + o)
            h = hits(mapper)
            if h > best_hits:
                best, best_hits = mapper, h
    if not best or best_hits < 3:
        return []
    mapped = sorted({(t, min(max(best(num), 0), n - 1)) for t, num in clean}, key=lambda x: x[1])
    out = []
    for i, (title, start) in enumerate(mapped):
        end = next((p for _, p in mapped[i + 1:] if p > start), n)
        out.append({"title": title, "start": start, "end": max(end, start + 1)})
    return out


def extract_annual_report(t, folder, out, info, max_words):
    reader = PdfReader(str(folder / "annual_report.pdf"))
    n = len(reader.pages)
    pages = [(reader.pages[i].extract_text() or "") for i in range(n)]
    (out / "full.txt").write_text("\n\n".join(f"[page {i + 1}]\n{p}" for i, p in enumerate(pages)),
                                  encoding="utf-8")
    chapters = chapters_all_levels(reader)
    method = "pdf bookmarks (all levels)"
    if not [c for c in chapters if WANT.search(c["title"])]:
        chapters = chapters_from_contents_page(reader)
        method = "printed contents page"
    if not [c for c in chapters if WANT.search(c["title"])]:
        chapters = chapters_from_toc_page(reader)
        method = "printed table of contents"
    picked = [c for c in chapters if WANT.search(c["title"]) and not SKIP.search(c["title"])]
    skipped_ch = [c for c in chapters if SKIP.search(c["title"])]
    if not picked:
        return None, "no usable table of contents (chapters can't be found cleanly)"
    info["chapter_method"] = method
    core_pages = sorted({p for c in picked for p in range(c["start"], c["end"])})
    skip_pages = {p for c in skipped_ch for p in range(c["start"], c["end"])}
    # only chapters that keep at least one page once skipped chapters are cut
    picked = [c for c in picked if any(p not in skip_pages for p in range(c["start"], c["end"]))]
    info["chapters"] = [f"{c['title']} (pages {c['start'] + 1}-{c['end']})" for c in picked]
    info["chapters_skipped"] = sorted({c["title"] for c in skipped_ch})
    core_pages = [p for p in core_pages if p not in skip_pages and not page_is_numbers(pages[p])]
    people_pages = {p for c in picked if PEOPLE.search(c["title"])
                    for p in range(c["start"], c["end"])}
    info["people_words"] = sum(word_count(pages[p]) for p in people_pages)

    # keyword paragraphs from the rest (not skipped chapters, not number pages)
    rest = [p for p in range(n) if p not in core_pages and p not in skip_pages
            and not page_is_numbers(pages[p])]
    kw = []
    for p in rest:
        for para in paragraphs(pages[p]):
            hits = len(GLOBAL_TERMS.findall(para))
            if hits:
                kw.append({"page": p, "text": para, "hits": hits, "words": word_count(para)})

    core_words = sum(word_count(pages[p]) for p in core_pages)
    kw_words = sum(k["words"] for k in kw)
    dropped_kw = dropped_pages = 0
    if core_words + kw_words > max_words:
        kw.sort(key=lambda k: (k["hits"] / max(k["words"], 1), k["hits"]))  # lowest first
        while kw and core_words + kw_words > max_words:
            kw_words -= kw.pop(0)["words"]
            dropped_kw += 1
    if core_words > max_words:
        rel = sorted(core_pages, key=lambda p: len(GLOBAL_TERMS.findall(pages[p])))
        while rel and core_words > max_words:
            p = rel.pop(0)
            core_pages.remove(p)
            core_words -= word_count(pages[p])
            dropped_pages += 1
    if dropped_kw or dropped_pages:
        msg = (f"over {max_words:,} words: dropped {dropped_kw} lowest-relevance keyword "
               f"paragraph(s) and {dropped_pages} lowest-relevance chapter page(s)")
        info["notes"].append(msg)
        log(STEP, f"{t}: {msg}")
    kw.sort(key=lambda k: k["page"])

    core_text = "\n\n".join(f"[page {p + 1}]\n{pages[p].strip()}" for p in core_pages)
    kw_text = "\n\n".join(f"[page {k['page'] + 1}]\n{k['text']}" for k in kw)
    info["sections"] = {"Annual report: selected chapters": word_count(core_text),
                        "Annual report: keyword paragraphs from the rest": word_count(kw_text)}
    sent = (f'<section name="Annual report: selected chapters">\n{core_text}\n</section>'
            + (f'\n\n<section name="Annual report: keyword paragraphs from the rest of the '
               f'report">\n{kw_text}\n</section>' if kw_text else ""))
    return (sent, core_text + "\n\n" + kw_text), ""


def extract_company(company, cfg, force=False):
    t = company["ticker"]
    out = G_SECTIONS / t
    info_path = out / "info.json"
    if info_path.exists() and not force:
        log(STEP, f"{t}: already extracted, skipping")
        return
    if t.upper() in skipped():
        return
    meta_path = G_DOCS / t / "meta.json"
    if not meta_path.exists():
        log(STEP, f"{t}: no document fetched yet, skipping")
        return
    meta = read_json(meta_path)
    gclear(t, STEP)
    out.mkdir(parents=True, exist_ok=True)
    info = {"ticker": t, "kind": meta["kind"], "form": meta["form"],
            "fiscal_year": meta.get("fiscal_year"), "url": meta.get("url", ""),
            "english": meta.get("english", True), "sections": {}, "notes": [],
            "people_words": 0}
    if meta["kind"] == "20-F":
        res, problem = extract_20f(t, G_DOCS / t, out, info)
    else:
        res, problem = extract_annual_report(t, G_DOCS / t, out, info,
                                             int(cfg.get("global_max_words", 40000)))
    if res is None:
        gflag(t, STEP, "sections can't be found cleanly; not scored", problem)
        log(STEP, f"{t}: {problem} - not scored")
        info["problem"] = problem
        write_json(out / "failed.json", info)
        return
    sent, verify_text = res
    if info["kind"] != "20-F" and word_count(sent) < MIN_NARRATIVE_WORDS:
        problem = (f"only {word_count(sent):,} words of narrative text picked: either the "
                   "document is the financial report alone, or its contents page couldn't be "
                   "read correctly (a teammate should check the document and its chapters)")
        gflag(t, STEP, "wrong document? too little narrative text; not scored", problem)
        log(STEP, f"{t}: {problem} - not scored")
        info["problem"] = problem
        write_json(out / "failed.json", info)
        return
    if not info["english"]:
        info["notes"].append("non-English source")
    (out / "sent.txt").write_text(sent + "\n", encoding="utf-8")
    (out / "verify.txt").write_text(verify_text + "\n", encoding="utf-8")
    info["words_sent"] = word_count(sent)
    write_json(info_path, info)
    log(STEP, f"{t}: {info['form']} -> {info['words_sent']:,} words sent "
              f"({'; '.join(f'{k}: {v:,}' for k, v in info['sections'].items())}); "
              f"people section {info['people_words']:,} words"
              + (f"; notes: {'; '.join(info['notes'])}" if info["notes"] else ""))


def main():
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__)
    add_global_args(p)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    for c in load_global_companies(args.country, args.tickers):
        try:
            extract_company(c, cfg, args.force)
        except Exception as e:
            log(STEP, f"{c['ticker']}: ERROR {e}")
            gflag(c["ticker"], STEP, "extraction error", str(e)[:300])


if __name__ == "__main__":
    main()
