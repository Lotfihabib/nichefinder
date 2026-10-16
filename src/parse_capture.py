"""Parse a search results page 1 that YOU saved in your own browser.

Nothing here fetches etsy.com. Inputs live in inputs/captures/<phrase-slug>/:

  *.html          saved page ("Webpage, HTML only"), parsed below
  capture.json    a transcription of screenshots, in the same shape load() returns.
                  The niche-test skill has Claude read the screenshots and write it.

Etsy's markup is not documented and changes. The HTML heuristics look for:
  - cards: elements with a data-listing-id attribute, else /listing/<id>/ links, in page order
  - ads: an "Ad" token in the card text ("Ad by ...", "Ad from shop ...") or an ad-ish attribute
  - badges: "Bestseller", "Popular now", "in N carts" / "in N+ carts"
Check the summary it prints against what you saw before trusting it.

Usage:  python -m src.parse_capture <phrase-slug>
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from .common import INPUTS

AD_TEXT = re.compile(r"(^|\s)Ad(\s+(by|from)\b|\s*$|\s*·)", re.I | re.M)
BESTSELLER = re.compile(r"\bbest\s?seller\b", re.I)
POPULAR = re.compile(r"\bpopular now\b", re.I)
CARTS = re.compile(r"\bin\s+(\d+)\+?\s+(?:people'?s\s+)?carts?\b", re.I)
LISTING_HREF = re.compile(r"/listing/(\d+)")


def capture_dir(slug: str) -> Path:
    return INPUTS / "captures" / slug


def _card_info(text: str, attrs: dict) -> dict:
    badges = []
    if BESTSELLER.search(text):
        badges.append("bestseller")
    if POPULAR.search(text):
        badges.append("popular_now")
    m = CARTS.search(text)
    in_carts = int(m.group(1)) if m else 0
    if in_carts:
        badges.append("in_carts")
    ad_attr = any(("ad" in k.lower().split("-") and str(v).lower() in ("true", "1"))
                  for k, v in attrs.items())
    return {"is_ad": bool(AD_TEXT.search(text)) or ad_attr, "badges": badges,
            "in_carts": in_carts}


def parse_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items, seen = [], set()
    cards = soup.select("[data-listing-id]")
    if cards:
        for el in cards:
            try:
                lid = int(el["data-listing-id"])
            except (ValueError, KeyError):
                continue
            if lid in seen:
                continue
            # Nested elements can repeat the id; keep the outermost one only.
            if el.find_parent(attrs={"data-listing-id": str(lid)}):
                continue
            seen.add(lid)
            text = el.get_text(" ", strip=True)
            attrs = {k: (" ".join(v) if isinstance(v, list) else v) for k, v in el.attrs.items()}
            items.append({"listing_id": lid, **_card_info(text, attrs)})
    else:
        for a in soup.find_all("a", href=True):
            m = LISTING_HREF.search(a["href"])
            if not m or int(m.group(1)) in seen:
                continue
            lid = int(m.group(1))
            seen.add(lid)
            container = a.find_parent(["li", "div"]) or a
            items.append({"listing_id": lid,
                          **_card_info(container.get_text(" ", strip=True), {})})
    for pos, it in enumerate(items, 1):
        it["position"] = pos
    return items


def load(slug: str) -> dict | None:
    """Return {"items": [...], "file": str, "captured_at": iso} or None if no capture."""
    d = capture_dir(slug)
    if not d.is_dir():
        return None
    htmls = sorted(d.glob("*.htm*"))
    if htmls:
        f = htmls[0]
        items = parse_html(f.read_text(encoding="utf-8", errors="replace"))
        captured = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
        return {"items": items, "file": str(f.relative_to(INPUTS.parent)),
                "captured_at": captured.replace(microsecond=0).isoformat(), "kind": "html"}
    cj = d / "capture.json"
    if cj.exists():
        data = json.loads(cj.read_text(encoding="utf-8"))
        for pos, it in enumerate(data["items"], 1):
            it.setdefault("position", pos)
            it.setdefault("badges", [])
            it.setdefault("in_carts", 0)
            it.setdefault("is_ad", False)
        captured = data.get("captured_at") or datetime.fromtimestamp(
            cj.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()
        return {"items": data["items"], "file": str(cj.relative_to(INPUTS.parent)),
                "captured_at": captured, "kind": "screenshot_transcription"}
    if any(d.glob("*.png")) or any(d.glob("*.jp*g")) or any(d.glob("*.webp")):
        raise FileNotFoundError(
            f"{d} has screenshots but no capture.json. Transcribe them first "
            f"(the niche-test skill does this).")
    return None


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cap = load(argv[0])
    if not cap:
        print(f"no capture in {capture_dir(argv[0])}")
        return 1
    items = cap["items"]
    organic = [i for i in items if not i["is_ad"]]
    print(f"{cap['file']}: {len(items)} cards, {len(items) - len(organic)} ads, "
          f"{sum(1 for i in organic[:20] if i['badges'])} of organic top 20 with demand badges")
    for i in items[:30]:
        print(f"  #{i['position']:>2} {i['listing_id']}  {'AD ' if i['is_ad'] else '   '}"
              f"{','.join(i['badges'])}{' carts=' + str(i['in_carts']) if i['in_carts'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
