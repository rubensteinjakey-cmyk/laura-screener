"""Calling Claude through Claude Code in headless mode (`claude -p`).

This runs on your Claude subscription (whatever account `claude` is logged
into), NOT a paid API key. Every call is locked down the same way so all
teammates get comparable results:
  --model      fixed from config.yaml
  --effort     fixed from config.yaml
  --tools ""   no tools at all (the AI can't read files or browse); it only
               sees the text we send it
  --system-prompt / --setting-sources "" / --strict-mcp-config
               ignore personal settings, CLAUDE.md files and plugins
The long text is sent through standard input (stdin), not the command line,
so there's no length problem.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

SYSTEM_PROMPT = (
    "You are a careful, politically neutral analyst scoring public company "
    "filings against a fixed rubric. You answer with a single JSON object "
    "and nothing else."
)

THEMES = ["creativity", "community", "cross_cultural", "entrepreneurship"]


class ClaudeError(RuntimeError):
    pass


def run_claude(prompt, cfg, tools="", extra_args=None):
    """Send one prompt to `claude -p`. Returns (reply_text, run_info)."""
    exe = shutil.which("claude")
    if not exe:
        raise ClaudeError("The `claude` command was not found. Install Claude Code "
                          "and log in first (see README).")
    cmd = [exe, "-p",
           "--model", cfg["model"],
           "--output-format", "json",
           "--tools", tools,
           "--system-prompt", SYSTEM_PROMPT,
           "--setting-sources", "",
           "--strict-mcp-config",
           "--no-session-persistence"]
    if cfg.get("effort"):
        cmd += ["--effort", cfg["effort"]]
    cmd += extra_args or []
    # Run from an empty temp folder so no project files/CLAUDE.md are seen.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True,
                                  text=True, cwd=tmp, encoding="utf-8",
                                  timeout=int(cfg.get("claude_timeout_seconds", 900)),
                                  env=os.environ.copy())
        except subprocess.TimeoutExpired:
            raise ClaudeError("Claude call timed out")
    out = proc.stdout.strip()
    try:
        envelope = json.loads(out)
    except json.JSONDecodeError:
        raise ClaudeError(f"claude -p failed (exit {proc.returncode}): "
                          f"{(proc.stderr or out)[:500]}")
    if envelope.get("is_error"):
        msg = str(envelope.get("result", ""))[:500]
        raise ClaudeError(f"Claude returned an error: {msg}")
    usage = envelope.get("usage", {})
    info = {
        "models_used": list(envelope.get("modelUsage", {}).keys()),
        "input_tokens": (usage.get("input_tokens", 0)
                         + usage.get("cache_creation_input_tokens", 0)
                         + usage.get("cache_read_input_tokens", 0)),
        "output_tokens": usage.get("output_tokens", 0),
        "duration_ms": envelope.get("duration_ms"),
    }
    if info["models_used"] and cfg["model"] not in info["models_used"]:
        raise ClaudeError(f"Asked for {cfg['model']} but Claude used "
                          f"{info['models_used']} - scores would not be comparable")
    return envelope.get("result", ""), info


def parse_json_reply(text):
    """Pull the JSON object out of the reply (tolerates ```json fences)."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        a, b = text.find("{"), text.rfind("}")
        if a == -1 or b == -1:
            raise ValueError("no JSON object in reply")
        text = text[a:b + 1]
    return json.loads(text)


def check_themes(obj, themes=THEMES):
    """Raise ValueError if the themes part isn't in the rubric's format."""
    if not isinstance(obj, dict) or not isinstance(obj.get("themes"), dict):
        raise ValueError("missing 'themes' object")
    for th in themes:
        t = obj["themes"].get(th)
        if not isinstance(t, dict):
            raise ValueError(f"missing theme '{th}'")
        if not isinstance(t.get("score"), int) or not 0 <= t["score"] <= 5:
            raise ValueError(f"{th}.score must be a whole number 0-5")
        if not isinstance(t.get("quotes", []), list) or \
                not all(isinstance(q, str) for q in t.get("quotes", [])):
            raise ValueError(f"{th}.quotes must be a list of strings")
        if not isinstance(t.get("level_reason", ""), str):
            raise ValueError(f"{th}.level_reason must be text")
