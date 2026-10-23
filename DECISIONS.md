# Team decisions

Dated record of scoring decisions, newest first. Each one names where it
lives in the code or config.

## Oct 23, 2026: global screen: exchange filing sites, Provisional groups, TOC fallback

Three team decisions before anyone saves PDFs by hand.

1. **Official stock-exchange filing sites count as a source**, the same as
   the company's own website (they are the official copies companies must
   file, like EDGAR in the US). Tried first, before the company's site
   (`src/exchange_filings.py`, called from `src/global_fetch.py`):
   - Denmark: Nasdaq Copenhagen company announcements ("Annual Financial
     Report" announcements; company confirmed by name and the Copenhagen
     market).
   - Switzerland: SIX Swiss Exchange. SIX keeps no archive of issuers' annual
     reports to download from, so this is logged. Lonza also lists on SGX,
     so its SGX filing is used (company confirmed by name).
   - Taiwan: MOPS (Market Observation Post System), the shareholder-meeting
     annual report by stock code, English version first. MOPS refuses fast
     requests, so they are spaced 25 seconds apart.
   - Indonesia: IDX (idx.co.id). Its bot check refuses automated requests,
     so this is logged and the company's own site is tried next.
   - Singapore: SGX company announcements (links.sgx.com). Claude's web
     search suggests the announcement; the code confirms the SGX stock code
     on the announcement page before downloading.
   The same checks apply as for a company-site report (fiscal 2025 or later,
   30+ pages, English preferred). If the exchange copy isn't in English, the
   company's own site is still searched for an English version. The source is
   logged in `meta.json` (`source_site`) and in the `source_site` and
   `filing_used` columns of `results_global.csv`.
2. **Incomplete country-sectors are "Provisional", not Pass/Fail.** A group
   gets final Pass/Fail only once every company in it is scored or formally
   excluded by the team (`global_skip.csv`, e.g. SWICH.AS). Until then
   `values_result` = "Provisional" and `values_notes` says how many are
   scored ("2 of 6 scored"). No company passes because its group is mostly
   empty.
3. **TOC fallback in the extractor.** If the table of contents can't be read
   reliably (no usable chapters, or the chapters picked hold under 5,000
   words, e.g. HMSP's chapter numbers read as page numbers), the company is
   no longer skipped: the keyword-paragraph method runs across the whole
   report, with the same word cap and exclusions (risk-heading pages, number
   pages, and the financial-statements block from the auditor's report to
   the last statements/notes page; narrative chapters printed after the
   financials, such as governance or sustainability, are kept). Noted as
   "TOC fallback" in the flags. Any extract under 2,000 words is flagged and
   not scored (it was 5,000 words of narrative before).

## Oct 23, 2026: global red flag 4 narrowed to outside findings

Rule 4 now reads: "A discrimination, harassment, forced/child labor or
human-rights violation found by a court, regulator or other outside body,
or a fine or settlement for one, during the reporting period."

- Why: cases a company substantiated through its own compliance or
  whistleblower process don't count. Most large companies with a working
  hotline report these; penalizing them would reward companies that
  disclose less.
- Where: the scoring prompt (`src/global_score.py`) asks who found each
  rights finding (`found_by`); `src/global_verify.py` drops internal ones
  (no points) and `values_notes` gets one line instead, e.g. "Discloses
  internally substantiated harassment cases; not a red flag under the rule."
  Test case 11 in `tests/test_checker.py`.
- Effect found in the test: Sika's 10-point deduction (internally
  substantiated harassment cases) is removed and it is off needs_human.csv.
  The four test companies were rescored with the final prompt so every
  company uses the same wording.
- Blocked downloads: teammates save PDFs in `data/global/manual/`
  (`{ticker}.pdf` or without the exchange suffix, e.g. `UNVR.pdf`; a
  standalone sustainability report as `{ticker}_sustainability.pdf`, e.g.
  `GMAB_sustainability.pdf`). The next run picks them up; a sustainability
  PDF redoes the second look (half weight).
- Report hosts (Oct 23, clarifying the website rule): a PDF on a host other
  than the company's own or brand-name domain (an IR file service like
  q4cdn, or a document host such as ipaper or a company CDN) is accepted
  only if the code finds the link on a page of the company's own website.
  It is always listed in needs_human.csv for a quick check. (Sydbank's and
  Demant's annual reports are hosted this way.)

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
