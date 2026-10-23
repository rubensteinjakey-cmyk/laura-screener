# Laura Gao 10-K Values Screener

A tool for our Wharton Global Youth Investment Competition team. It reads each
company's **10-K** (the annual report every public company files with the SEC),
scores how well the company fits our client Laura Gao's values using a fixed
rubric (`rubric.md`), and ranks companies **within their sector**.

> **This is a values filter only.** The financial strategy comes first,
> especially funding Laura's fixed $50,000 yearly payments for 2033–2042. The
> screener only helps choose among stocks that already fit the financial plan.

## What it does, step by step

| Step | Script | Uses Claude? | What it does |
|---|---|---|---|
| 2 | `src/download.py` | no | Gets each company's latest 10-K from SEC EDGAR (cached) |
| 3 | `src/extract.py` | no | Cuts out Item 1 (Business) and Item 7 (MD&A). Never Item 1A (Risk Factors) |
| 5 | `src/score.py` | **yes** | Claude lists evidence records (each with a word-for-word quote) and scores 4 themes 0–5, each with a coverage rating |
| 6 | `src/verify.py` | no | Code checks every quote really is in the filing, removes failed evidence, checks each score points to `current` evidence, then calculates the score out of 100 |
| 9 | `src/second_look.py` | **yes** (a little) | Only when Community or Cross-cultural coverage is `insufficient`: finds the company's sustainability report and rescores those themes from scratch (scores can go up or down) |
| 8 | `src/merge.py` | no | Combines everyone's results |
| 10 | `src/rank.py` | no | Sector ranks, shortlist + `output/results.csv`, `evidence.csv`, `summary.md`, `needs_human.csv` |
| – | `src/overlap_check.py` | no | Compares two runs of the same companies |
| – | `src/blind_check.py` | no | Blank hand-scoring sheet + compares a teammate's hand scores to the AI |
| – | `tests/test_checker.py` | no | Proves the checker catches altered/made-up quotes, double counting and planned activities |

`run_all.py` runs all of them in order with one command.

**No API key and no paid API.** Scoring runs through **Claude Code** on your own
Claude subscription (the `claude -p` command, which is Claude Code's
"headless" mode, meaning it runs from a script without a chat window).

## Words you'll see

- **Terminal**: the text window where you type commands (Mac: the *Terminal*
  app; Windows: *PowerShell*).
- **Repository / repo**: the project folder, stored on GitHub.
- **Clone**: download the repo to your computer the first time.
- **Pull**: get teammates' latest changes. **Commit**: save a snapshot of your
  changes. **Push**: upload your commits to GitHub.
- **CIK**: the SEC's ID number for a company.
- **run_id**: a label stuck on your score files (we use your first name).

## 1. One-time setup

1. **Install Python 3.10 or newer** from https://www.python.org/downloads/
   (Windows: tick "Add Python to PATH" in the installer).
2. **Install Claude Code** and log in with your Claude account:
   https://code.claude.com/docs/en/setup. Then in a terminal run `claude` once and
   follow the login steps.
3. **Install Git** from https://git-scm.com/downloads, and get access to the
   team repo on GitHub (ask Jakey to add you).
4. **Download the project** (only the first time):
   ```
   git clone https://github.com/<OWNER>/laura-screener.git
   cd laura-screener
   ```
5. **Install the Python packages** the code needs:
   ```
   python -m pip install -r requirements.txt
   ```
   (On a Mac you may need `python3` instead of `python` everywhere.)
6. **Edit `config.yaml`** (any text editor) and change only:
   - `user_agent` → **your** name and email (the SEC requires it)
   - `run_id` → your first name, e.g. `"maya"`

   **Don't change** `model`, `effort`, `sections` or the shortlist settings.
   Everyone must use the same ones or the scores aren't comparable.
7. **Check Claude Code works from a script**:
   ```
   claude -p --model claude-opus-5-5 "Reply with the word ok"
   ```
   It should print `ok`. If it asks you to log in, run `claude` and log in.

## 2. Run your batch

The team splits `companies.csv` by row number. For example, with 200
companies:

| Teammate | Command |
|---|---|
| Person 1 | `python run_all.py --start 1 --end 66` |
| Person 2 | `python run_all.py --start 67 --end 133` |
| Person 3 | `python run_all.py --start 134 --end 200` |

**If it stops** (usage limit, laptop closed, error), just run the **same
command again**. Finished companies are saved after each one and skipped.

You can also run steps one at a time, e.g.
`python src/score.py --start 1 --end 66` or `python src/score.py --tickers JPM`.

## 3. Share your results

```
git pull
git add data/ output/
git commit -m "Maya: scores for rows 67-133"
git push
```

Each person's files have their own name (`run_id`), so you won't overwrite
each other. After everyone has pushed, one person runs:

```
git pull
python src/merge.py
python src/rank.py
```

and the combined `output/results.csv` and `output/summary.md` are ready.

## 4. Consistency check (overlap)

To check that scores are repeatable, score the same companies twice (or have
two teammates score the same few companies with different `run_id`s):

```
python src/score.py --tickers JPM,BAC --run-id maya_repeat
python src/verify.py --run-id maya_repeat
python src/overlap_check.py --a maya --b maya_repeat
```

**PASS** = final scores within 5 points and no theme more than 1 point apart.

## Blind check (hand-scoring)

1. `python src/blind_check.py make --tickers JPM,BAC,WFC` writes
   `output/blind_check_template.csv`. It has **no AI scores** in it.
2. A teammate copies it (e.g. `blind_check_maya.csv`), reads the companies'
   text in `data/sections/{TICKER}/` with `rubric.md`, and fills in a 0–5
   score and coverage (`sufficient`/`insufficient`) for each theme, **without
   looking at** `output/`, `data/scores/` or `data/verified/`.
3. `python src/blind_check.py compare blind_check_maya.csv --run-id jakey --tenk-only`
   shows where the human and the AI agree (same rule as the overlap check).

## Outputs

- `output/results.csv`: one row per company: the 4 theme scores and their
  coverage, final score, sector rank and percentile, `in_shortlist`, score
  source, the 10-K-only and full-report scores and finals (if a second look
  ran), quote check status,
  flags, report URL.
- `output/evidence.csv`: one row per evidence record (activity, quote,
  section, status, indicators, themes, source document, verified or not).
- `output/summary.md`: per sector: the shortlist, then companies ranked, each
  with its 4 theme scores and coverage next to the total and its strongest
  verified evidence per theme.
- `output/needs_human.csv`: everything the tool couldn't handle, with a reason
  (section not found, quote check failed, no sustainability report found,
  report not on the company's main website...).
- `data/sections/{TICKER}/`: the exact text the AI read (`item1.txt`,
  `item7.txt`, `item7_targeted.txt`), plus `info.json` with word counts and notes.
- `data/scores/`: the AI's raw answers. `data/verified/`: after the quote check.
- `logs/`: what happened, step by step (including text size sent per company).

## How the rules are enforced

- **Quotes are checked by code, not by the AI** (`verify.py`): spaces, line
  breaks, curly/straight quotes, dashes and capital letters are ignored, but
  the words must match exactly. A failed quote's evidence record is removed.
  A theme that cited it keeps its score only if other valid `current`
  evidence remains (and is flagged for review); otherwise it becomes 0.
- **Only `current` activities count.** A score of 2+ must point to at least one
  verified `current` evidence record, or it becomes 0 (a score of 1 may rest
  on general language). Citing the same activity twice in one theme is flagged.
- **Figures are checked too.** Every number in a record's `indicators` must
  appear near its quote (same paragraph or table, about 1,200 characters).
  Missing figures are removed and flagged; a level-3+ theme that relied on
  one is flagged for human review (never silently rescored).
- **Caps by `kind`, enforced by code** (Rubric v2.1): Creativity 2+ needs a
  `creates`/`enables` record; Community 3+ needs `external_education`,
  `community_investment` or `education_business`; Cross-cultural 2+ needs a
  record that isn't `demographics_only`; Entrepreneurship 2+ needs `enabling`
  (ordinary small-business lending, deposits and payments are `serving`, 1 at
  most). A broken cap lowers the score to the cap and is flagged.
- `python tests/test_checker.py` plants each kind of mistake and proves the
  checker catches it (13 checks).
- **The code calculates final scores and ranks**, not the AI.
- **Same model for everyone**: `config.yaml` fixes the full model ID and effort
  level. Each Claude call also runs with no tools and no personal settings, and
  the code stops if Claude answers with a different model.
- **Risk Factors (Item 1A) are never read.** Item 1 is cut off right where
  Item 1A begins.
- **Coverage and the second look**: each theme is `sufficient` or
  `insufficient`, using the rubric's strict definition (no discussion at all,
  under about 150 words, or only a pointer to another document). A filing
  that describes only employee training is `sufficient`. If the AI marks
  Community or Cross-cultural `insufficient` (its reason is logged), or the
  people discussion in Item 1 is missing / under 150 words,
  `second_look.py` finds the company's sustainability report (2024 or later,
  on a website containing the company's brand name from its 10-K) and
  rescores those themes from scratch with the 10-K + report. Scores can go up
  or down. The report counts at **half weight** (team decision, Oct 19; see
  `DECISIONS.md`): final theme score = 10-K score + `report_weight` ×
  (report score − 10-K score), with `report_weight: 0.5` in `config.yaml`.
  The 10-K-only and full-report scores are both kept in `results.csv`. Reports not on the company's
  main domain are listed in `needs_human.csv` for a quick check. A human can
  supply a report link in `data/reports/manual_urls.csv` (`ticker,url`).
- **Shortlist**: the top 30% of each sector (`shortlist_pct`; 40% until Oct 20), at least 2 per
  sector (`shortlist_min_per_sector`), ties included.
- **No 10-K?** Companies that file a 20-F or 40-F (foreign companies), or
  whose latest 10-K is more than 18 months old, are skipped and listed in
  `needs_human.csv`. No other form is substituted, unless the team approves
  one in `form_overrides.csv` (`ticker,form,accession,fiscal_year,source_label,reason`).
  Arxis (ARXS) is scored from its Oct 13, 2026 S-1 this way: the extractor
  takes the S-1's "Business" and "Management's Discussion and Analysis"
  sections, and everything else (rubric, model, checks, second look) is the
  same. See `DECISIONS.md`.
- **Ticker moved to a new company?** If the SEC lists no 10-K for a ticker
  (e.g. XOM after its 2026 holding-company reorganization), add the old
  company's CIK to `cik_overrides.csv` (`ticker,cik,reason`).

## Global sleeve screen (Rooted Capital)

A separate run on 70 non-US companies (`companies_global.csv`: Denmark,
Switzerland, Taiwan, Indonesia, Singapore). Outputs go to `output/global/`;
the US files are never touched. Same rubric, model, quote/figure checks and
half-weight rule; the differences are recorded in `DECISIONS.md` (Oct 23).

```
python run_global.py --country Denmark     # one country at a time (resumable)
python run_global.py --rank                # Pass/Fail + outputs, once every company is scored
```

- **Sources:** 20-F from SEC EDGAR for GMAB, ALC, TLK (Items 4, 5 targeted,
  6.D; Item 3.D Risk Factors skipped). Everyone else: the fiscal-2025 annual
  report from the company's own website (`src/global_fetch.py`). If a site
  blocks the download, the company is listed in
  `output/global/needs_human.csv`; save the PDF as
  `data/global/manual/{ticker}.pdf` and run the same command again.
- **Sections** (`src/global_extract.py`): chapters picked from the PDF's
  bookmarks or printed contents page (overview/strategy, people,
  community/education/inclusion/sustainability) plus keyword paragraphs;
  risk chapters, financial statements and notes skipped; capped at
  `global_max_words`.
- **Red flags** (`src/global_verify.py`): tobacco, thermal coal, weapons,
  rights findings; each needs a verified quote and costs `red_flag_penalty`
  points (floor 0).
- **Pass/Fail** (`src/global_rank.py`): top half of the country-sector by
  final score, ties included. Not-scored companies (`global_skip.csv`) are
  "Not scored". `third_component` is blank: TBD.
- Outputs: `results_global.csv`, `evidence_global.csv`, `summary_global.md`,
  `needs_human.csv`.

## Open decisions (TODO)

Decisions already made, with dates, are in `DECISIONS.md`.

- [ ] **TODO: global screen "third component".** Still undecided; the
      `third_component` column in `output/global/results_global.csv` is blank.

These are **not decided yet**. The code is built so each can change without
rewriting anything:

- [ ] **TODO: final list of 200 companies.** Replace `companies.csv` (same
      columns: `ticker,company_name,sector`). No code changes needed.
- [ ] **TODO: keyword cross-check.** Should a cheap keyword scan be added as a
      second opinion on the AI scores? Not built in this version.
- [x] Sections: Item 1 + targeted MD&A (`[item1, item7_targeted]`), decided
      after the pilot. `[item1]` and `[item1, item7]` remain for tests only.
- [x] Model: `claude-opus-5-5` (decided by Jakey).
