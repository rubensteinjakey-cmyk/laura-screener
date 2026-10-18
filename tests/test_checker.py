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
plus correct answers that must pass untouched (so the checker isn't just
rejecting everything): a correct record, a score of 1 resting on general
language with no evidence, and the "cap rule" when one of two cited quotes
fails.

The "filing" text is built from real passages of Bank of America's 10-K,
with an unrelated paragraph in between so "wrong paragraph" is far away.

Run:  python tests/test_checker.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from verify import check_evidence, check_theme  # noqa: E402

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


def has(r, text):
    return any(text in f for f in r["flags"])


def main():
    ev = check_evidence(EVIDENCE, {"10-K": FILING})
    res = {name: check_theme(theme, ans, ev) for name, theme, ans in CASES}
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
