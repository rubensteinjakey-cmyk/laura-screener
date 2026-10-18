"""Deliberate-error test for the quote/evidence checker (Section 7, item 3).

It feeds the checker a fake AI answer with four planted mistakes and shows
that each one is caught:
  1. an ALTERED quote (one word changed)
  2. a MADE-UP quote (not in the filing at all)
  3. the SAME ACTIVITY cited twice in one theme
  4. a PLANNED activity used to support a score
plus a correct answer that must pass untouched (so the checker isn't just
rejecting everything).

The "filing" text is a real passage from Bank of America's 10-K Item 1.

Run:  python tests/test_checker.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from verify import check_evidence, check_theme  # noqa: E402

FILING = """We have 11 Employee Networks with over 330,000 voluntary memberships,
which provide teammates opportunities to meet new people, have an impact across
multiple business lines and grow personally and professionally. In 2025, we
delivered more than 7.6 million hours of training and development to our
teammates through Bank of America Academy."""

EVIDENCE = [
    # correct record
    {"id": "E1", "activity": "Employee networks with membership figures",
     "quote": "We have 11 Employee Networks with over 330,000 voluntary memberships",
     "section": "Item 1 > Human Capital", "status": "current", "themes": {}},
    # 1. altered quote: 330,000 -> 430,000
    {"id": "E2", "activity": "Employee networks (altered)",
     "quote": "We have 11 Employee Networks with over 430,000 voluntary memberships",
     "section": "Item 1 > Human Capital", "status": "current", "themes": {}},
    # 2. made-up quote
    {"id": "E3", "activity": "Arts scholarship fund",
     "quote": "We fund 500 art scholarships for local high school students every year",
     "section": "Item 1 > Community", "status": "current", "themes": {}},
    # 3. same activity as E1 under a second id
    {"id": "E4", "activity": "Employee networks with membership figures",
     "quote": "11 Employee Networks with over 330,000 voluntary memberships",
     "section": "Item 1 > Human Capital", "status": "current", "themes": {}},
    # 4. planned activity (its quote is real, but status is planned)
    {"id": "E5", "activity": "Training academy (planned expansion)",
     "quote": "delivered more than 7.6 million hours of training and development",
     "section": "Item 1 > Human Capital", "status": "planned", "themes": {}},
]

THEMES = {
    "control":        {"score": 3, "coverage": "sufficient", "evidence_ids": ["E1"], "reason": ""},
    "altered":        {"score": 3, "coverage": "sufficient", "evidence_ids": ["E2"], "reason": ""},
    "made_up":        {"score": 3, "coverage": "sufficient", "evidence_ids": ["E3"], "reason": ""},
    "double_count":   {"score": 3, "coverage": "sufficient", "evidence_ids": ["E1", "E4"], "reason": ""},
    "double_same_id": {"score": 3, "coverage": "sufficient", "evidence_ids": ["E1", "E1"], "reason": ""},
    "mixed":          {"score": 3, "coverage": "sufficient", "evidence_ids": ["E1", "E2"], "reason": ""},
    "planned":        {"score": 2, "coverage": "sufficient", "evidence_ids": ["E5"], "reason": ""},
}


def main():
    ev = check_evidence(EVIDENCE, {"10-K": FILING})
    res = {name: check_theme(th, ev) for name, th in THEMES.items()}
    checks = [
        ("Correct quote passes, score kept",
         ev["E1"]["verified"] and res["control"]["score"] == 3 and not res["control"]["flags"]),
        ("1. Altered quote is caught and removed, score -> 0",
         not ev["E2"]["verified"] and ev["E2"]["problem"] == "quote_check_failed"
         and res["altered"]["score"] == 0),
        ("2. Made-up quote is caught and removed, score -> 0",
         not ev["E3"]["verified"] and res["made_up"]["score"] == 0),
        ("3a. Same activity under two ids is flagged as double counting",
         any("double_counting" in f for f in res["double_count"]["flags"])),
        ("3b. Same id cited twice is flagged as double counting",
         any("double_counting" in f for f in res["double_same_id"]["flags"])),
        ("Cap rule: one cited quote fails but valid evidence remains -> kept + human review",
         res["mixed"]["score"] == 3
         and any("needs human review" in f for f in res["mixed"]["flags"])),
        ("4. Planned activity can't support a score, score -> 0",
         ev["E5"]["verified"] and res["planned"]["score"] == 0
         and any("non-current" in f for f in res["planned"]["flags"])),
    ]
    width = max(len(c[0]) for c in checks)
    for name, ok in checks:
        print(f"{name:<{width}}  {'PASS' if ok else 'FAIL'}")
    for name in THEMES:
        r = res[name]
        print(f"   {name:<15} AI {r['ai_score']} -> {r['score']}   {'; '.join(r['flags']) or 'no flags'}")
    failed = [c for c, ok in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
