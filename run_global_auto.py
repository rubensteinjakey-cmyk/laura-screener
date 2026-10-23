"""Run the global screen country by country and RESUME BY ITSELF after a
Claude usage-limit stop.

  python run_global_auto.py                      Denmark, Switzerland, Taiwan,
                                                 Indonesia, Singapore, in order
  python run_global_auto.py --countries Taiwan,Singapore

Each pass runs `run_global.py --country X` for every country (finished
companies are skipped, so a pass only does what's left). If a pass hits the
usage limit, the script reads the reset time from Claude's message ("resets
5:50pm (UTC)"), sleeps until a few minutes after it, and starts the next
pass. It stops when a pass finishes with no limit hit. It does NOT rank:
run `python run_global.py --rank` once every company is scored.

Progress: logs/global_auto.log; current state: logs/global_auto_status.txt.
To run it in the background so it keeps going on its own:
  nohup python run_global_auto.py > /dev/null 2>&1 &
"""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "logs" / "global_auto.log"
STATUS = ROOT / "logs" / "global_auto_status.txt"
ORDER = ["Denmark", "Switzerland", "Taiwan", "Indonesia", "Singapore"]
LIMIT = re.compile(r"hit your (?:session |usage |weekly )?limit|usage limit|session limit|"
                   r"limit \S* resets|rate limit", re.I)
RESET = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*\(?(UTC)?", re.I)
NOISE = re.compile(r"fontTools|XMLParsedAsHTML|warnings\.|BeautifulSoup|you're using an HTML|"
                   r"Assuming this|If you want or need|from bs4|filterwarnings|soup = ")


def note(msg):
    line = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC] {msg}"
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    STATUS.write_text(line + "\n", encoding="utf-8")


def next_reset(text):
    """When the usage limit resets, from Claude's message (UTC). None if unknown."""
    m = None
    for m in RESET.finditer(text):
        pass  # the last mention is the freshest
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    now = datetime.now(timezone.utc)
    t = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t


def run_country(country):
    """Run one country; return (limit_hit, output_text)."""
    proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "run_global.py"),
                             "--country", country], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    kept = []
    with open(LOG, "a", encoding="utf-8") as f:
        for line in proc.stdout:  # written as it comes, so a crash loses nothing
            if line.strip() and not NOISE.search(line):
                f.write(line)
                f.flush()
                kept.append(line)
    proc.wait()
    out = "".join(kept)
    return bool(LIMIT.search(out)), out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--countries", default=",".join(ORDER))
    p.add_argument("--max-passes", type=int, default=30)
    p.add_argument("--exit-on-limit", action="store_true",
                   help="exit (code 3) at a usage limit instead of sleeping; the status "
                        "file says when to resume")
    a = p.parse_args()
    countries = [c.strip() for c in a.countries.split(",") if c.strip()]
    for n in range(1, a.max_passes + 1):
        note(f"pass {n}: starting {', '.join(countries)}")
        hit_text = ""
        for c in countries:
            note(f"pass {n}: {c}")
            hit, out = run_country(c)
            if hit:
                hit_text = out
                note(f"pass {n}: usage limit hit during {c}")
                break
        if not hit_text:
            note(f"pass {n}: finished with no usage-limit stop. ALL COUNTRIES DONE "
                 "(check needs_human.csv for blocked downloads; then run_global.py --rank)")
            return
        reset = next_reset(hit_text)
        wake = (reset + timedelta(minutes=5)) if reset else \
            datetime.now(timezone.utc) + timedelta(minutes=30)
        if a.exit_on_limit:
            # In the cloud session a long sleep doesn't survive an idle
            # container: exit and let a scheduled resume start the next pass.
            note(f"pass {n}: LIMIT; resume at {wake:%Y-%m-%d %H:%M} UTC")
            sys.exit(3)
        note(f"pass {n}: sleeping until {wake:%Y-%m-%d %H:%M} UTC "
             f"({'limit resets ' + f'{reset:%H:%M}' if reset else 'reset time unknown'}), "
             "then resuming automatically")
        while datetime.now(timezone.utc) < wake:
            time.sleep(60)
    note(f"gave up after {a.max_passes} passes")


if __name__ == "__main__":
    main()
