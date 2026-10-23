# Team decisions

Dated record of scoring decisions, newest first. Each one names where it
lives in the code or config.

## Oct 23, 2026: global sleeve values screen ("Rooted Capital", 70 companies)

A second, separate run on 70 non-US companies (Denmark, Switzerland,
Taiwan, Indonesia, Singapore). Input `companies_global.csv`, outputs in
`output/global/`, run with `run_global.py`. The US results are untouched.
Same rubric (v2.1), model setting, quote/figure checks and half-weight rule
as the US run, with these differences:

- **List edits.** DANS.VI changed to DANSKE.CO (Danske Bank, Copenhagen).
  2884.TW renamed E.SUN Financial Holding (the listed parent; its annual
  report is scored). SWICH.AS (SWI Capital Holding) stays on the list but is
  not scored ("identity unclear: check ticker/name with the team"; in
  `global_skip.csv` and `output/global/needs_human.csv`). Singapore
  Financials is ranked on the other 5. DSFIR.AS stays under Switzerland
  Materials (intended).
- **Sources.** 20-F filers (confirmed on EDGAR: Genmab GMAB, Alcon ALC,
  Telkom Indonesia TLK) use their latest 20-F: Item 4 in place of Item 1,
  Item 5 as the MD&A (targeted paragraphs), Item 6.D for employees; Item 3.D
  Risk Factors is skipped. Everyone else uses the latest annual report
  (fiscal 2025, published 2026) from the company's own investor-relations
  website (own or brand-name domain; an IR file service only if the
  company's own site links to it). Blocked downloads go to needs_human.csv;
  a PDF saved as `data/global/manual/{ticker}.pdf` is used instead.
  English version when one exists; otherwise quotes stay in the original
  language (that is what the code verifies), with an English translation in
  a separate field and "non-English source" in the notes.
- **Sections.** From the annual report's table of contents: business
  overview / strategy, people / employees / human capital, and community /
  education / inclusion / sustainability chapters, plus keyword paragraphs
  from the rest. Risk sections, financial statements and notes are skipped.
  Over ~40,000 words, the lowest-relevance keyword paragraphs are cut first.
- **Weights.** Sustainability content inside the official annual report
  (including EU-style sustainability statements) counts at full weight. A
  separate, standalone sustainability report is used only in a second look,
  with the same trigger and the same half weight as the US run. No
  standalone report: scored on the annual report alone, no penalty, no bonus.
- **Red flags.** Tobacco products as a core business; thermal coal mining or
  coal-fired power as a significant business; weapons manufacturing; a
  disclosed finding, fine or settlement in the reporting period for
  discrimination, harassment, forced or child labor, or serious human-rights
  abuses. Each needs a verified quote from the source document (never
  outside knowledge) and subtracts `red_flag_penalty: 10` points (floor 0).
  A red flag never forces a Fail on its own. Final score = alignment score
  (composite 0-100) minus the red-flag penalty.
  "Rights finding" red flags are a judgment call, so each one is also listed
  in `output/global/needs_human.csv` for a quick human check.
- **Pass/Fail.** Ranked only within country-sector. Pass = top half of the
  country-sector by final score, ties included (cutoff = ceil(n / 2), n =
  scored companies in the group, same safe rounding as the shortlist rule).
  Everyone else Fails; skipped companies are "Not scored". No overall
  ranking and no 30% shortlist for this run.
- **Third component: TBD (TODO).** The team sheet's "Third component (TBD)"
  column is still undecided; `third_component` is left blank in
  `results_global.csv`.

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
