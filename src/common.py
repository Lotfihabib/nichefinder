"""Shared helpers: paths, config, slugs, and source-stamped metrics."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INPUTS = ROOT / "inputs"
REPORTS = ROOT / "reports"
EVIDENCE = DATA / "evidence"
JUDGMENTS = DATA / "judgments"
REDTEAM = DATA / "redteam"

SOURCES = {"api", "page_capture", "export"}


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SLUG_MAX = 50   # keeps data/ and inputs/captures/ paths under Windows' 260-char limit


def slugify(phrase: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", phrase.lower()).strip("-")
    if len(slug) <= SLUG_MAX:
        return slug
    import hashlib
    return slug[: SLUG_MAX - 7].rstrip("-") + "-" + hashlib.sha1(slug.encode()).hexdigest()[:6]


def metric(value: Any, source: str, at: str) -> dict:
    """Every number we keep carries where it came from and when.

    value=None means UNKNOWN. It is never treated as a pass.
    """
    if source not in SOURCES and not source.startswith("export:"):
        raise ValueError(f"unknown metric source {source!r}")
    return {"value": value, "source": source, "at": at}


def val(m: Any) -> Any:
    """Value of a metric dict, or None if missing/UNKNOWN."""
    if isinstance(m, dict) and "value" in m:
        return m["value"]
    return None


def etsy_search_url(phrase: str) -> str:
    from urllib.parse import quote_plus
    return f"https://www.etsy.com/search?q={quote_plus(phrase)}"
