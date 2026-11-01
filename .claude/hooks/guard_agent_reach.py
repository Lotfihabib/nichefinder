"""PreToolUse guard: keeps Agent-Reach on public routes and away from Etsy.

Blocks any shell command that runs `agent-reach` and
  - mentions Etsy (etsy.com, etsy search, ...): Etsy data comes only from the official API
  - logs in, adds channels, or drives the managed browser (login / add / browser / --backend opencli)
  - uses a browser-only channel (instagram, xiaohongshu, linkedin, boss, xueqiu)
  - searches/reads twitter, facebook or reddit without `--backend public-web`

Exit 2 tells Claude Code to block the call and show the reason.
"""
import json
import re
import sys

BROWSER_ONLY = ("instagram", "xiaohongshu", "linkedin", "boss", "xueqiu")
PUBLIC_ONLY = ("twitter", "facebook", "reddit")


INVOKE = re.compile(r"""^(?:\w+=\S*\s+)*["']?(?:\S*[\\/])?agent-reach(?:\.exe)?["']?(?:\s|$)""")


def reason(cmd: str) -> str | None:
    """Check each shell segment that actually runs agent-reach (not text that merely mentions it)."""
    for seg in re.split(r"\n|;|&&|\|\||\|", cmd):
        why = _segment_reason(seg.strip())
        if why:
            return why
    return None


def _segment_reason(seg: str) -> str | None:
    if not INVOKE.match(seg):
        return None
    low = re.sub(r"""^(?:\w+=\S*\s+)*["']?(?:\S*[\\/])?agent-reach(?:\.exe)?["']?""", "agent-reach", seg.lower())
    if "etsy" in low:
        return "Agent-Reach must never touch Etsy. Etsy data comes only from the official API (src/etsy_client.py)."
    if re.search(r"agent-reach\s+(login|logout|add|browser)\b", low) or "--backend opencli" in low:
        return "Agent-Reach browser/login features are off for this project: public routes only."
    if re.search(r"agent-reach\s+(read)\b", low) and re.search(r"instagram\.com|xiaohongshu|linkedin\.com", low):
        return "That site is browser-only in Agent-Reach, which this project doesn't use."
    for ch in BROWSER_ONLY:
        if re.search(rf"agent-reach\s+{ch}\b", low):
            return f"The {ch} channel needs the managed browser, which this project doesn't use."
    for ch in PUBLIC_ONLY:
        if re.search(rf"agent-reach\s+{ch}\b", low) and "--backend public-web" not in low:
            return f"Add `--backend public-web` to {ch} commands (no browser fallback)."
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    cmd = (data.get("tool_input") or {}).get("command") or ""
    why = reason(cmd)
    if why:
        print(f"Blocked by .claude/hooks/guard_agent_reach.py: {why}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
