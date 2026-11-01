"""Stage 1b: off-Etsy signals per phrase via Agent-Reach (public routes only).

Runs the fixed queries in config.yaml `signals.queries` (YouTube, Reddit, Twitter,
Facebook, web search) and writes data/signals/<slug>.json. These are context for the
niche-judge and red team (buyer questions, free alternatives, tutorials). They are
not part of the 5-question pass/fail rules.

Guardrails (same rules as .claude/hooks/guard_agent_reach.py, enforced here too,
because the hook only sees Claude's own shell commands):
  - nothing that mentions Etsy; Etsy-hosted result URLs are dropped
  - reddit/twitter/facebook always run with --backend public-web (no browser fallback)
  - browser-only channels, login and the managed browser are refused

Usage:
  python -m src.signals                     # phrases not already rejected in data/scores.json
  python -m src.signals --phrase "..."      # one phrase
  python -m src.signals --max-phrases 2
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .common import DATA, ROOT, SIGNALS, load_config, metric, now_iso, slugify

PUBLIC_ONLY = {"reddit", "twitter", "facebook"}
POINTS = re.compile(r"(\d[\d,]*)\s+points?\b", re.I)
COMMENTS = re.compile(r"(\d[\d,]*)\s+comments?\b", re.I)

_spec = importlib.util.spec_from_file_location("guard", ROOT / ".claude" / "hooks" / "guard_agent_reach.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def agent_reach_exe() -> str:
    exe = shutil.which("agent-reach")
    if not exe:
        for cand in (Path.home() / ".local" / "bin" / "agent-reach.exe",
                     Path.home() / ".local" / "bin" / "agent-reach"):
            if cand.exists():
                exe = str(cand)
                break
    if not exe:
        raise FileNotFoundError("agent-reach not found. Install the pinned release (see README).")
    return exe


def build_args(q: dict, phrase: str) -> list[str]:
    query = q["query"].format(phrase=phrase)
    args = [q["channel"], q["action"], query, "--limit", str(q.get("limit", 10))]
    if q["channel"] in PUBLIC_ONLY:
        args += ["--backend", "public-web"]
    why = guard.reason("agent-reach " + " ".join(args))
    if why:
        raise PermissionError(why)
    return args


def _int(m) -> int | None:
    return int(m.group(1).replace(",", "")) if m else None


def is_etsy(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "etsy.com" or host.endswith(".etsy.com") or host.endswith("etsy.me")


def clean_item(it: dict, words: int) -> dict:
    text = it.get("text") or ""
    raw = it.get("raw") or {}
    return {
        "title": it.get("title"), "url": it.get("url"), "author": it.get("author"),
        "published_at": it.get("published_at"),
        "snippet": " ".join(re.sub(r"[#*`]", "", text).split()[:words]),
        "views": raw.get("view_count"),                    # YouTube, as yt-dlp reports it
        "points": _int(POINTS.search(text[:600])),          # Reddit, as shown on the page
        "comments": _int(COMMENTS.search(text[:600])),
    }


def run_query(runner, q: dict, phrase: str, cfg: dict) -> dict:
    s = cfg["signals"]
    at = now_iso()
    block = {"channel": q["channel"], "action": q["action"], "purpose": q.get("purpose"),
             "query": q["query"].format(phrase=phrase), "at": at, "ok": False,
             "backend": None, "error": None, "items": [], "dropped_etsy_urls": 0}
    try:
        out = runner(build_args(q, phrase), s["timeout_seconds"])
        d = json.loads(out)
    except PermissionError as e:
        block["error"] = f"refused: {e}"
        return block
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        block["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return block
    block["ok"] = bool(d.get("ok"))
    block["backend"] = d.get("backend")
    if d.get("error"):
        block["error"] = f"{d['error'].get('code')}: {str(d['error'].get('message'))[:200]}"
    for it in d.get("items") or []:
        if is_etsy(it.get("url")):
            block["dropped_etsy_urls"] += 1
            continue
        block["items"].append(clean_item(it, s["snippet_words"]))
    block["source"] = f"agent-reach:{q['channel']}/{block['backend'] or 'none'}"
    return block


def summarise(blocks: list[dict]) -> dict:
    """Counts only from what the tools returned; None when a channel failed."""
    def blk(ch):
        return next((b for b in blocks if b["channel"] == ch), None)

    def m(ch, fn):
        b = blk(ch)
        if not b:
            return None
        return metric(fn(b) if b["ok"] else None, b.get("source") or f"agent-reach:{ch}/none", b["at"])

    known = lambda xs: [x for x in xs if x is not None]
    return {
        "youtube_videos": m("youtube", lambda b: len(b["items"])),
        "youtube_max_views": m("youtube", lambda b: max(known([i["views"] for i in b["items"]]), default=None)),
        "reddit_threads": m("reddit", lambda b: len(b["items"])),
        "reddit_max_points": m("reddit", lambda b: max(known([i["points"] for i in b["items"]]), default=None)),
        "twitter_results": m("twitter", lambda b: len(b["items"])),
        "facebook_results": m("facebook", lambda b: len(b["items"])),
        "free_alternative_results": m("web", lambda b: sum(
            1 for i in b["items"] if "free" in f"{i['title']} {i['snippet']}".lower())),
    }


def collect_signals(row: dict, cfg: dict, runner) -> dict:
    phrase = row["phrase"].strip()
    blocks = []
    for n, q in enumerate(cfg["signals"]["queries"]):
        if n:
            time.sleep(cfg["signals"]["pause_seconds"])
        blocks.append(run_query(runner, q, phrase, cfg))
    return {"phrase": phrase, "slug": slugify(phrase), "collected_at": now_iso(),
            "note": "Off-Etsy context. Counts are what each public search returned, not market size.",
            "summary": summarise(blocks), "queries": blocks}


def subprocess_runner(args: list[str], timeout: float) -> str:
    r = subprocess.run([agent_reach_exe(), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout


def main(argv: list[str] | None = None) -> int:
    from .collect import read_phrases
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrases", default=str(DATA / "phrases.csv"))
    ap.add_argument("--phrase")
    ap.add_argument("--max-phrases", type=int)
    ap.add_argument("--include-rejected", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config()
    rows = [{"phrase": a.phrase}] if a.phrase else read_phrases(Path(a.phrases))
    scores = DATA / "scores.json"
    if not a.phrase and not a.include_rejected and scores.exists():
        dead = {r["slug"] for r in json.loads(scores.read_text(encoding="utf-8")) if r["decision"] == "reject"}
        rows = [r for r in rows if slugify(r["phrase"]) not in dead]
    if a.max_phrases:
        rows = rows[: a.max_phrases]
    SIGNALS.mkdir(parents=True, exist_ok=True)
    for row in rows:
        sig = collect_signals(row, cfg, subprocess_runner)
        (SIGNALS / f"{sig['slug']}.json").write_text(json.dumps(sig, indent=2, ensure_ascii=False), encoding="utf-8")
        s = {k: (v or {}).get("value") for k, v in sig["summary"].items()}
        fails = [b["channel"] for b in sig["queries"] if not b["ok"]]
        print(f"{sig['phrase']!r}: {s}" + (f"; failed: {fails}" if fails else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
