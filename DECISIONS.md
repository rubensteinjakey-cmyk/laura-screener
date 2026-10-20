# Team decisions

Dated record of scoring decisions, newest first. Each one names where it
lives in the code or config.

## Oct 20, 2026: shortlist cut to the top 30% of each sector

Oct 20: shortlist cut from top 40% to top 30% per sector to narrow the list
(103 to 69 companies).

- Still at least 2 per sector, ties with the last spot included.
- Where: `shortlist_pct: 0.30` in `config.yaml`, applied in `src/rank.py`.
- Repo count: 70, not 69. With Arxis added, Industrials has 17 companies;
  30% of 17 = 5.1, rounded up to 6, so Lyft (26) is the 6th spot. With 16
  companies (no Arxis), 30% = 4.8, rounded up to 5. Every other sector
  matches: IT 19, Health Care 17, Energy 7, Consumer Discretionary 5,
  Financials 4, Materials 3, Consumer Staples 3, Communication Services 2,
  Utilities 2, Real Estate 2.

## Oct 20, 2026: Arxis (ARXS) is scored from its S-1

Arxis IPO'd in April 2026 and has not filed a 10-K yet, so it is scored from
its most recent S-1, filed Oct 13, 2026 (accession 0001193125-26-419718). The
S-1 counts at full weight, like a 10-K, because it is also a legally binding
SEC filing.

- Where: `form_overrides.csv` (download), the S-1 headings in
  `src/extract.py` ("Business" to "Management"; "Management's Discussion and
  Analysis" to "Business"; Risk Factors cut as usual), and a note in the
  scoring prompt (`src/score.py`) that the text is an S-1 to be treated like a
  10-K. Same rubric, model setting, quote/figure checks and second-look rule.
- Source label: "S-1 (no 10-K yet)". Note on the result: scored from the
  Oct 13, 2026 S-1 because Arxis has not filed its first 10-K. Rescore when
  the 10-K is filed (remove the row from `form_overrides.csv`, delete
  `data/filings/ARXS`, `data/sections/ARXS`, `data/scores/ARXS_*` and rerun).
- Sector: Industrials (aerospace & defense components), a draft assignment
  to confirm.
- Result: 9/100 (Creativity 0, Community 1, Cross-cultural 0,
  Entrepreneurship 1), no evidence records; Community and Cross-cultural
  coverage insufficient and no sustainability report found. Rank 16 of 17 in
  Industrials, not on the shortlist (still 103 companies).

## Oct 19, 2026: sustainability-report evidence counts at half weight

Sustainability-report evidence counts at half weight (10-K is a legally
binding SEC filing; reports are voluntary). Chosen before seeing which
companies it affected. Sensitivity: across 0–100% weighting, only about 5 of
199 companies change shortlist status.

- Rule: for any theme rescored in a second look, final theme score =
  10-K score + 0.5 × (report-based score − 10-K score). Decimals such as 1.5
  are allowed.
- Where: `report_weight: 0.5` in `config.yaml`, applied in `src/verify.py`
  (`blend`). Applied as math on the saved second-look results; nothing was
  rescored by the AI.
- `output/results.csv` keeps the other two versions as extra columns:
  `original_10k_scores` / `final_score_10k_only` and `full_report_scores` /
  `final_score_full_report`. Score source reads
  "10-K + sustainability report (half weight)".
- Effect on the Oct 19 results (13 companies had a second look; 10 changed):
  shortlist 106 → 103. Off the shortlist: MRVL, RPRX, RVTY, CF. Back on: CDE.
