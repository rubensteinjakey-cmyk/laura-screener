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
    if tools:
        # in headless mode a tool must also be pre-approved, or every use of
        # it is silently denied
        cmd += ["--allowedTools", tools]
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


STATUSES = {"current", "past", "planned"}
COVERAGES = {"sufficient", "insufficient"}
SOURCE_DOCS = {"10-K", "sustainability_report"}
# Rubric v2.1 "Allowed kind values" per theme
KINDS = {
    "creativity": {"creates", "enables", "uses_as_input"},
    "community": {"employee_development", "external_education",
                  "community_investment", "education_business"},
    "cross_cultural": {"internal_practice", "customer_access",
                       "connecting_communities", "demographics_only"},
    "entrepreneurship": {"serving", "enabling", "own_innovation"},
}


def check_reply(obj, themes=THEMES, source_docs=("10-K",)):
    """Raise ValueError if the reply isn't in Rubric v2.1's output format:
    a list of evidence records (each tagging a kind per theme), then the
    themes, each with score, coverage, evidence_ids and reason.
    source_docs: the source_doc values allowed in this call."""
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")
    ev = obj.get("evidence")
    if not isinstance(ev, list):
        raise ValueError("missing 'evidence' list")
    for i, e in enumerate(ev):
        if not isinstance(e, dict):
            raise ValueError(f"evidence[{i}] is not an object")
        for f in ("id", "activity", "quote", "section", "status", "source_doc"):
            if not isinstance(e.get(f), str):
                raise ValueError(f"evidence[{i}].{f} must be text")
        if e["status"] not in STATUSES:
            raise ValueError(f"evidence[{i}].status must be current/past/planned")
        if e["source_doc"] not in source_docs:
            raise ValueError(f"evidence[{i}].source_doc must be one of {sorted(source_docs)}")
        if not isinstance(e.get("indicators", ""), str):
            raise ValueError(f"evidence[{i}].indicators must be text")
        tags = e.get("themes")
        if not isinstance(tags, dict) or not tags:
            raise ValueError(f"evidence[{i}].themes must name at least one theme")
        for th, tag in tags.items():
            if th not in KINDS:
                raise ValueError(f"evidence[{i}].themes has unknown theme {th!r}")
            if not isinstance(tag, dict) or tag.get("kind") not in KINDS[th]:
                raise ValueError(f"evidence[{i}].themes.{th}.kind must be one of "
                                 f"{sorted(KINDS[th])}")
    if not isinstance(obj.get("themes"), dict):
        raise ValueError("missing 'themes' object")
    for th in themes:
        t = obj["themes"].get(th)
        if not isinstance(t, dict):
            raise ValueError(f"missing theme '{th}'")
        if not isinstance(t.get("score"), int) or not 0 <= t["score"] <= 5:
            raise ValueError(f"{th}.score must be a whole number 0-5")
        if t.get("coverage") not in COVERAGES:
            raise ValueError(f"{th}.coverage must be sufficient or insufficient")
        if not isinstance(t.get("evidence_ids", []), list) or \
                not all(isinstance(x, str) for x in t.get("evidence_ids", [])):
            raise ValueError(f"{th}.evidence_ids must be a list of ids")
        if not isinstance(t.get("reason", ""), str) or not t.get("reason", "").strip():
            raise ValueError(f"{th}.reason must be a sentence")
