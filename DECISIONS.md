# Team decisions

Dated record of scoring decisions, newest first. Each one names where it
lives in the code or config.

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
