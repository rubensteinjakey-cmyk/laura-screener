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
| 5 | `src/score.py` | **yes** | Claude scores 4 themes 0–5 with word-for-word quotes |
| 6 | `src/verify.py` | no | Code checks every quote really is in the filing, then calculates the score out of 100 |
| 9 | `src/second_look.py` | **yes** (a little) | Thin filings only: finds the company's sustainability report and rescores Community + Cross-cultural (can only go up) |
| 8 | `src/merge.py` | no | Combines everyone's results |
| 10 | `src/rank.py` | no | Sector ranks + `output/results.csv`, `summary.md`, `needs_human.csv` |
| – | `src/overlap_check.py` | no | Compares two runs of the same companies |

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

   **Don't change** `model`, `effort` or `sections`. Everyone must use the
   same ones or the scores aren't comparable.
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

## Outputs

- `output/results.csv`: one row per company: scores, final score, sector rank
  and percentile, thin filing flag, score source, quote check status, report URL.
- `output/summary.md`: per sector: companies ranked, each with its 4 theme
  scores and its best verified quotes.
- `output/needs_human.csv`: everything the tool couldn't handle, with a reason
  (section not found, quote check failed, no sustainability report found...).
- `data/sections/{TICKER}/`: the exact text the AI read (`item1.txt`,
  `item7.txt`), plus `info.json` with word counts and notes.
- `data/scores/`: the AI's raw answers. `data/verified/`: after the quote check.
- `logs/`: what happened, step by step (including text size sent per company).

## How the rules are enforced

- **Quotes are checked by code, not by the AI** (`verify.py`): spaces, line
  breaks, curly/straight quotes, dashes and capital letters are ignored, but
  the words must match exactly. Any failed quote → that theme = 0, and the
  company goes in `needs_human.csv`.
- **The code calculates final scores and ranks**, not the AI.
- **Same model for everyone**: `config.yaml` fixes the full model ID and effort
  level. Each Claude call also runs with no tools and no personal settings, and
  the code stops if Claude answers with a different model.
- **Risk Factors (Item 1A) are never read.** Item 1 is cut off right where
  Item 1A begins.
- **Thin filings**: if the AI says so, or the Human Capital part of Item 1 is
  missing / under 150 words, `thin_filing = true` and `second_look.py` tries the
  company's own sustainability report (2024 or later, only from a website the
  company lists in its own 10-K). Scores can only go up.

## Open decisions (TODO)

These are **not decided yet**. The code is built so each can change without
rewriting anything:

- [ ] **TODO: final list of 200 companies.** Replace `companies.csv` (same
      columns: `ticker,company_name,sector`). No code changes needed.
- [ ] **TODO: keyword cross-check.** Should a cheap keyword scan be added as a
      second opinion on the AI scores? Not built in this version.
- [ ] **TODO: score Item 7 (MD&A) too?** The 3-bank test compares Item 1 alone
      vs Item 1 + Item 7. If the team adds it, change `sections` in
      `config.yaml` to `[item1, item7]` (everyone at once, before scoring).
- [x] Model: `claude-opus-5-5` (decided by Jakey).
