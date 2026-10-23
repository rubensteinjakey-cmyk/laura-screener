"""Deliberate-error test for the quote / figure / evidence checker
(Rubric v2.1).

It feeds the checker a fake AI answer with planted mistakes and shows that
each one is caught:
  1. an ALTERED quote (one word changed)
  2. a MADE-UP quote (not in the filing at all)
  3. the SAME ACTIVITY cited twice in one theme (two ids, or one id twice)
  4. a PLANNED activity used to support a score
  5. a MADE-UP FIGURE in indicators
  6. a REAL FIGURE taken from the WRONG PARAGRAPH (far from the quote)
  7. Community scored 3 with only employee_development evidence -> capped at 2
  8. Entrepreneurship scored 3 with only "serving" evidence -> capped at 1
Global screen (Oct 23):
  9. a RED FLAG whose quote is not in the source is removed (no penalty)
 11. a rights finding the company substantiated ITSELF (internal hotline /
     compliance case) is not a red flag: no penalty, only a note
 10. an ORIGINAL-LANGUAGE (non-English) quote is checked word for word:
     the exact Chinese quote passes, an altered one fails
plus correct answers that must pass untouched (so the checker isn't just
rejecting everything): a correct record, a score of 1 resting on general
language with no evidence, and the "cap rule" when one of two cited quotes
fails.

The "filing" text is built from real passages of Bank of America's 10-K,
with an unrelated paragraph in between so "wrong paragraph" is far away.

Run:  python tests/test_checker.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from verify import check_evidence, check_theme  # noqa: E402
from global_verify import check_red_flags, internal_note, red_flag_result  # noqa: E402

FILLER = ("The Corporation is subject to an extensive regulatory framework applicable "
          "to bank holding companies, financial holding companies and banks. ") * 20

FILING = f"""We have 11 Employee Networks with over 330,000 voluntary memberships, which provide teammates opportunities to meet new people, have an impact across multiple business lines and grow personally and professionally.

In 2025, we delivered more than 7.6 million hours of training and development to our teammates through Bank of America Academy.

{FILLER}

We offer industry-leading support to approximately four million small business households.

In 2025, more than 14,000 employees found new roles within the Corporation.
"""


def rec(id_, quote, themes, status="current", indicators="none disclosed", activity=None):
    return {"id": id_, "activity": activity or id_, "quote": quote, "source_doc": "10-K",
            "section": "Item 1 > Human Capital", "status": status,
            "indicators": indicators, "themes": themes}


NETWORKS = {"cross_cultural": {"kind": "internal_practice", "criterion": "level 3"}}
ACADEMY = {"community": {"kind": "employee_development", "criterion": "level 2"}}

EVIDENCE = [
    # correct record, correct figures
    rec("E1", "We have 11 Employee Networks with over 330,000 voluntary memberships",
        NETWORKS, indicators="11 networks, 330,000 memberships",
        activity="Employee networks with membership figures"),
    # 1. altered quote: 330,000 -> 430,000
    rec("E2", "We have 11 Employee Networks with over 430,000 voluntary memberships", NETWORKS),
    # 2. made-up quote
    rec("E3", "We fund 500 art scholarships for local high school students every year",
        {"community": {"kind": "external_education", "criterion": "level 3"}}),
    # 3. same activity as E1 under a second id
    rec("E4", "11 Employee Networks with over 330,000 voluntary memberships", NETWORKS,
        activity="Employee networks with membership figures"),
    # 4. planned activity (its quote is real, but status is planned)
    rec("E5", "we delivered more than 7.6 million hours of training and development",
        ACADEMY, status="planned"),
    # 5. made-up figure: the quote is real, "25,000 attendees" is invented
    rec("E6", "we delivered more than 7.6 million hours of training and development",
        ACADEMY, indicators="7.6 million hours; 25,000 attendees"),
    # 6. real figure from the wrong paragraph: "14,000" is in the filing, but
    #    far from this quote (it belongs to the internal-mobility paragraph)
    rec("E7", "We have 11 Employee Networks with over 330,000 voluntary memberships",
        NETWORKS, indicators="11 networks; 14,000 members"),
    # 7. employee development only (real quote, real figure)
    rec("E8", "we delivered more than 7.6 million hours of training and development",
        ACADEMY, indicators="7.6 million hours"),
    # 8. serving small businesses only (real quote; "four million" written as a word)
    rec("E9", "We offer industry-leading support to approximately four million small business households.",
        {"entrepreneurship": {"kind": "serving", "criterion": "small-business customers"}},
        indicators="4 million small business households"),
]

T = lambda score, ids, coverage="sufficient": {  # noqa: E731
    "score": score, "coverage": coverage, "evidence_ids": ids, "reason": "test"}

CASES = [
    # name, theme, AI's theme answer
    ("control", "cross_cultural", T(3, ["E1"])),
    ("general_language", "community", T(1, [])),
    ("altered", "cross_cultural", T(3, ["E2"])),
    ("made_up", "community", T(3, ["E3"])),
    ("double_count", "cross_cultural", T(3, ["E1", "E4"])),
    ("double_same_id", "cross_cultural", T(3, ["E1", "E1"])),
    ("mixed", "cross_cultural", T(3, ["E1", "E2"])),
    ("planned", "community", T(2, ["E5"])),
    ("made_up_figure", "community", T(2, ["E6"])),
    ("wrong_paragraph_figure", "cross_cultural", T(3, ["E7"])),
    ("cap_community", "community", T(3, ["E8"])),
    ("cap_entrepreneurship", "entrepreneurship", T(3, ["E9"])),
]


# Global screen: red flags and a non-English source (MediaTek-style text)
GLOBAL_SOURCE = """本公司持續推動STEM教育，2025年共有超過3,000名學生參與聯發科技舉辦的程式設計營隊。

We sell cigarettes and other tobacco products in 30 markets, and tobacco is our core business.
"""
RED_FLAGS = [
    {"id": "R1", "flag": "tobacco", "quote": "We sell cigarettes and other tobacco products in 30 markets"},
    {"id": "R2", "flag": "weapons", "quote": "We manufacture munitions for national defense forces"},
]
GLOBAL_SOURCE += """
Our compliance team substantiated 12 harassment cases reported through our whistleblower hotline in 2025.
"""
INTERNAL_ONLY = [
    {"id": "R3", "flag": "rights_finding", "found_by": "internal", "case_type": "harassment",
     "quote": "Our compliance team substantiated 12 harassment cases reported through our whistleblower hotline"},
]
ZH_EVIDENCE = [
    {"id": "Z1", "activity": "STEM camps", "status": "current", "source_doc": "filing",
     "section": "Annual report > Social engagement", "indicators": "3,000",
     "quote": "2025年共有超過3,000名學生參與聯發科技舉辦的程式設計營隊",
     "quote_en": "In 2025, more than 3,000 students joined MediaTek's coding camps",
     "themes": {"community": {"kind": "external_education", "criterion": "level 3"}}},
    {"id": "Z2", "activity": "STEM camps (altered)", "status": "current", "source_doc": "filing",
     "section": "Annual report > Social engagement", "indicators": "",
     "quote": "2025年共有超過5,000名學生參與聯發科技舉辦的程式設計營隊",
     "themes": {"community": {"kind": "external_education", "criterion": "level 3"}}},
]


def has(r, text):
    return any(text in f for f in r["flags"])


def main():
    ev = check_evidence(EVIDENCE, {"10-K": FILING})
    res = {name: check_theme(theme, ans, ev) for name, theme, ans in CASES}
    kept, removed, _ = check_red_flags(RED_FLAGS, {"filing": GLOBAL_SOURCE})
    in_kept, in_removed, in_internal = check_red_flags(INTERNAL_ONLY, {"filing": GLOBAL_SOURCE})
    in_kinds, in_deducted, in_final = red_flag_result(in_kept, 30.0, 10)
    rf_kinds, rf_deducted, rf_final = red_flag_result(kept, 23.0, 10)
    zh = check_evidence(ZH_EVIDENCE, {"filing": GLOBAL_SOURCE}, default_doc="filing")
    # 12. incomplete country-sector -> Provisional, never Pass
    from global_rank import group_results
    part = group_results([{"final_score": 46}, {"final_score": 46}], total=6)
    full = group_results([{"final_score": 30}, {"final_score": 20}], total=2)
    # 13. TOC fallback: keyword paragraphs from narrative pages only
    from global_extract import extract_toc_fallback, short_extract_problem
    narrative = ("Our foundation runs scholarships for students and supports small business "
                 "owners in the community. " * 6).strip()
    pages = ([narrative] * 6
             + ["Risk Management\n" + narrative]
             + [narrative] * 3
             + ["Independent Auditor's Report\n" + narrative]
             + ["Notes to the consolidated financial statements\n" + narrative] * 4
             + ["Sustainability Report\n" + narrative] * 2)
    finfo = {"notes": [], "people_words": 0}
    (fsent, _), _ = extract_toc_fallback("TEST", pages, finfo, 40000, "no usable table of contents")
    fb_pages = sorted({int(m) for m in re.findall(r"\[page (\d+)\]", fsent)})
    checks = [
        ("Correct record passes, score kept, figures kept",
         ev["E1"]["verified"] and not ev["E1"]["figures_removed"]
         and res["control"]["score"] == 3 and not res["control"]["flags"]),
        ("Score of 1 on general language with no evidence is allowed",
         res["general_language"]["score"] == 1 and not res["general_language"]["flags"]),
        ("1. Altered quote is caught and removed, score -> 0",
         not ev["E2"]["verified"] and ev["E2"]["problem"] == "quote_check_failed"
         and res["altered"]["score"] == 0),
        ("2. Made-up quote is caught and removed, score -> 0",
         not ev["E3"]["verified"] and res["made_up"]["score"] == 0),
        ("3a. Same activity under two ids is flagged as double counting",
         has(res["double_count"], "double_counting")),
        ("3b. Same id cited twice is flagged as double counting",
         has(res["double_same_id"], "double_counting")),
        ("Cap rule: one cited quote fails, valid evidence remains -> kept + review",
         res["mixed"]["score"] == 3 and has(res["mixed"], "needs human review")),
        ("4. Planned activity can't support a score, score -> 0",
         res["planned"]["score"] == 0 and has(res["planned"], "non-current")),
        ("5. Made-up figure is removed from indicators (record stays)",
         ev["E6"]["verified"] and ev["E6"]["figures_removed"] == ["25,000"]
         and "7.6" in ev["E6"]["indicators"] and "25,000" not in ev["E6"]["indicators"]),
        ("6. Real figure from the wrong paragraph is removed; level-3 theme flagged",
         ev["E7"]["figures_removed"] == ["14,000"]
         and res["wrong_paragraph_figure"]["score"] == 3
         and has(res["wrong_paragraph_figure"], "figure(s) removed")),
        ("   (number words count: '4 million' matches 'four million')",
         ev["E9"]["verified"] and not ev["E9"]["figures_removed"]),
        ("7. Community 3 with only employee_development -> capped to 2",
         res["cap_community"]["score"] == 2 and has(res["cap_community"], "cap:")),
        ("8. Entrepreneurship 3 with only 'serving' -> capped to 1",
         res["cap_entrepreneurship"]["score"] == 1 and has(res["cap_entrepreneurship"], "cap:")),
        ("9. Red flag with no verified quote is removed; only the verified one costs points",
         [r["id"] for r in kept] == ["R1"] and [r["id"] for r in removed] == ["R2"]
         and rf_kinds == ["tobacco"] and rf_deducted == 10 and rf_final == 13.0),
        ("   (red-flag penalty never goes below 0)", red_flag_result(kept, 4.0, 10)[2] == 0.0),
        ("11. Internal-only finding (own hotline/compliance) gets no penalty, only a note",
         not in_kept and not in_removed and len(in_internal) == 1 and in_deducted == 0
         and in_final == 30.0 and "not a red flag" in internal_note(in_internal)),
        ("10. Non-English quote checked in the original language (exact passes, altered fails)",
         zh["Z1"]["verified"] and not zh["Z1"]["figures_removed"]
         and not zh["Z2"]["verified"] and zh["Z2"]["problem"] == "quote_check_failed"),
        ("12. Group with 2 of 6 scored is Provisional (no Pass); a complete group gets Pass/Fail",
         [r["result"] for r in part] == ["Provisional", "Provisional"]
         and [r["result"] for r in full] == ["Pass", "Fail"]),
        ("13. TOC fallback: keyword paragraphs, skipping risk pages and financial statements "
         "(a sustainability chapter after them is kept)",
         fb_pages == [1, 2, 3, 4, 5, 6, 8, 9, 10, 16, 17]
         and any(n.startswith("TOC fallback") for n in finfo["notes"])),
        ("14. Under 2,000 words is never scored (HMSP's 564); 2,500 is",
         short_extract_problem(564) != "" and short_extract_problem(2500) == ""),
    ]
    width = max(len(c[0]) for c in checks)
    for name, ok in checks:
        print(f"{name:<{width}}  {'PASS' if ok else 'FAIL'}")
    print()
    for name, theme, ans in CASES:
        r = res[name]
        print(f"   {name:<23} {theme:<16} AI {r['ai_score']} -> {r['score']}   "
              f"{'; '.join(r['flags']) or 'no flags'}")
    for i in ("E6", "E7"):
        print(f"   {i} indicators: {ev[i]['indicators_original']!r} -> {ev[i]['indicators']!r}")
    failed = [c for c, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
