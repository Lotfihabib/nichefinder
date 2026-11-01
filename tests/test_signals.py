"""Off-Etsy signals collector with a fake Agent-Reach runner. No network."""
import json

import pytest

from src import signals
from src.common import load_config

CFG = load_config()
CFG["signals"]["pause_seconds"] = 0


def fake_runner(calls, fail=()):
    def run(args, timeout):
        calls.append(args)
        ch = args[0]
        if ch in fail:
            return json.dumps({"ok": False, "items": [], "backend": "public-web",
                               "error": {"code": "UPSTREAM_BROKEN", "message": "no usable content"}})
        items = {
            "youtube": [{"title": "How to budget irregular income", "url": "https://www.youtube.com/watch?v=a",
                         "text": "tutorial", "raw": {"view_count": 24661}},
                        {"title": "Video 2", "url": "https://www.youtube.com/watch?v=b", "text": "x",
                         "raw": {"view_count": None}}],
            "reddit": [{"title": "Free sheet for variable income", "url": "https://www.reddit.com/r/pf/1",
                        "text": "# Title\nPosted by u/x · 126 points · 13 comments · July 2020"}],
            "web": [{"title": "Free irregular income budget template", "url": "https://example.com/t",
                     "text": "download free"},
                    {"title": "Etsy listing", "url": "https://www.etsy.com/listing/1/x", "text": "free"}],
        }.get(ch, [])
        return json.dumps({"ok": True, "backend": "yt-dlp" if ch == "youtube" else "public-web",
                           "items": items, "error": None})
    return run


def test_collects_and_summarises_without_etsy():
    calls = []
    sig = signals.collect_signals({"phrase": "budget spreadsheet for irregular income"}, CFG, fake_runner(calls))
    s = {k: v["value"] for k, v in sig["summary"].items()}
    assert s["youtube_videos"] == 2 and s["youtube_max_views"] == 24661
    assert s["reddit_threads"] == 1 and s["reddit_max_points"] == 126
    assert s["free_alternative_results"] == 1                   # the etsy.com hit was dropped
    web = next(b for b in sig["queries"] if b["channel"] == "web")
    assert web["dropped_etsy_urls"] == 1 and all("etsy" not in i["url"] for i in web["items"])
    assert sig["summary"]["youtube_videos"]["source"] == "agent-reach:youtube/yt-dlp"


def test_public_web_forced_for_social_channels():
    calls = []
    signals.collect_signals({"phrase": "wedding budget tracker"}, CFG, fake_runner(calls))
    for args in calls:
        if args[0] in ("reddit", "twitter", "facebook"):
            assert args[-2:] == ["--backend", "public-web"]


def test_failed_channel_is_unknown_not_zero():
    sig = signals.collect_signals({"phrase": "wedding budget tracker"}, CFG, fake_runner([], fail={"twitter"}))
    assert sig["summary"]["twitter_results"]["value"] is None
    tw = next(b for b in sig["queries"] if b["channel"] == "twitter")
    assert not tw["ok"] and "UPSTREAM_BROKEN" in tw["error"]


@pytest.mark.parametrize("q", [
    {"channel": "web", "action": "search", "query": "{phrase} site:etsy.com", "limit": 5},
    {"channel": "instagram", "action": "search", "query": "{phrase}", "limit": 5},
])
def test_refused_queries_never_run(q):
    calls = []
    block = signals.run_query(fake_runner(calls), q, "budget planner", CFG)
    assert calls == [] and block["error"].startswith("refused")


def test_etsy_host_detection():
    assert signals.is_etsy("https://www.etsy.com/listing/1") and signals.is_etsy("https://etsy.com/x")
    assert not signals.is_etsy("https://notetsy.example.com/x")
