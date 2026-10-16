"""Stage 1: collect evidence per phrase from the Etsy API, your captures and your exports.

Writes data/evidence/<slug>.json with derived metrics. Every metric is
{"value", "source", "at"}, and value=None means UNKNOWN.

Usage:
  python -m src.collect                      # every row of data/phrases.csv
  python -m src.collect --phrase "..."       # one phrase
  python -m src.collect --max-phrases 1      # first N rows (dry run)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import parse_capture
from .common import DATA, EVIDENCE, INPUTS, ROOT, load_config, metric, now_iso, slugify
from .etsy_client import BudgetExceeded, EtsyClient

SEARCH = "/v3/application/listings/active"
BATCH = "/v3/application/listings/batch"
REVIEWS = "/v3/application/listings/{}/reviews"


def money_usd(listing: dict) -> float | None:
    for field in ("converted_price", "price"):
        m = listing.get(field)
        if m and m.get("currency_code") == "USD" and m.get("divisor"):
            return round(m["amount"] / m["divisor"], 2)
    return None


def iso(ts) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if ts else None


def load_exports(phrase: str) -> dict:
    """Rows from your eRank/EverBee CSVs whose keyword column matches the phrase."""
    out = {}
    for f in sorted((INPUTS / "exports").glob("*.csv")):
        at = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()
        with open(f, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            key = next((c for c in reader.fieldnames or []
                        if c.strip().lower() in ("keyword", "keywords", "search term", "tag", "phrase")), None)
            if not key:
                continue
            for row in reader:
                if (row.get(key) or "").strip().lower() == phrase.lower():
                    for col, v in row.items():
                        if col != key and v not in (None, ""):
                            out[f"{f.name}:{col}"] = metric(v, f"export:{f.name}", at)
    return out


def collect_phrase(client: EtsyClient, row: dict, cfg: dict) -> dict:
    c = cfg["collection"]
    phrase, slug, at = row["phrase"].strip(), slugify(row["phrase"]), now_iso()
    client.start_phrase()
    notes = []

    # 1. API search (relevance order; NOT the buyer-facing page 1)
    search = client.get(SEARCH, {"keywords": phrase, "limit": c["search_limit"], "sort_on": "score",
                                 "currency": c["currency"]}, kind="search")
    api_ids = [r["listing_id"] for r in search.get("results", [])]

    # 2. top-20 set: capture if present, else API order
    cap = parse_capture.load(slug)
    ads = []
    if cap:
        organic = [i for i in cap["items"] if not i["is_ad"]]
        ads = [{"listing_id": i["listing_id"], "position": i["position"]}
               for i in cap["items"] if i["is_ad"]]
        top = organic[: c["top_n"]]
        source, cap_at = "page_capture", cap["captured_at"]
        if len(top) < c["top_n"]:
            notes.append(f"capture has only {len(top)} organic listings")
    else:
        top = [{"listing_id": i, "badges": None, "in_carts": None} for i in api_ids[: c["top_n"]]]
        source, cap_at = "api", None
    top_ids = [t["listing_id"] for t in top]

    # 3+4. listing details with Shop association, one batch call
    details = {}
    if top_ids:
        batch = client.get(BATCH, {"listing_ids": top_ids, "includes": ["Shop"],
                                   "currency": c["currency"]}, kind="listings")
        details = {r["listing_id"]: r for r in batch.get("results", [])}

    listings = []
    for rank, t in enumerate(top, 1):
        d = details.get(t["listing_id"], {})
        shop = d.get("shop") or {}
        if not d:
            notes.append(f"listing {t['listing_id']} not returned by batch call")
        price_native = d.get("price") or {}
        listings.append({
            "listing_id": t["listing_id"], "rank": rank, "shop_id": d.get("shop_id"),
            "api_rank": api_ids.index(t["listing_id"]) + 1 if t["listing_id"] in api_ids else None,
            "title": metric(d.get("title"), "api", at),
            "tags": metric(d.get("tags"), "api", at),
            "listing_type": metric(d.get("listing_type"), "api", at),
            "price_usd": metric(money_usd(d) if d else None, "api", at),
            "price_native": metric(
                round(price_native["amount"] / price_native["divisor"], 2)
                if price_native.get("divisor") else None, "api", at),
            "currency": metric(price_native.get("currency_code"), "api", at),
            "favorites": metric(d.get("num_favorers"), "api", at),
            "created_at": metric(iso(d.get("original_creation_timestamp") or d.get("created_timestamp")), "api", at),
            "shop_name": metric(shop.get("shop_name"), "api", at),
            "shop_user_id": metric(shop.get("user_id"), "api", at),
            "shop_sales": metric(shop.get("transaction_sold_count"), "api", at),
            "shop_review_count_1y": metric(shop.get("review_count"), "api", at),
            "shop_review_avg_1y": metric(shop.get("review_average"), "api", at),
            "shop_created_at": metric(iso(shop.get("create_date") or shop.get("created_timestamp")), "api", at),
            "shop_active_listings": metric(shop.get("listing_active_count"), "api", at),
            "demand_badges": metric(t.get("badges"), "page_capture", cap_at or at),
            "in_carts": metric(t.get("in_carts"), "page_capture", cap_at or at),
            "recent_reviews": metric(None, "api", at),
            "low_star_reviews": [], "review_dates": [],
            "reviews_scanned": 0, "review_total": None,
        })

    # 5. reviews: recent window per listing, then older pages for 1-3 star text
    day = int(time.time()) // 86400 * 86400          # whole-day boundary keeps cache keys stable
    window_start = day - c["review_window_days"] * 86400
    seen: dict[int, set] = {l["listing_id"]: set() for l in listings}

    def take(l, rows):
        for r in rows:
            k = (r.get("created_timestamp"), r.get("rating"), (r.get("review") or "")[:40])
            if k in seen[l["listing_id"]]:
                continue
            seen[l["listing_id"]].add(k)
            l["reviews_scanned"] += 1
            l["review_dates"].append(iso(r.get("created_timestamp")))
            if r.get("rating") and r["rating"] <= c["low_star_max"] and r.get("review"):
                l["low_star_reviews"].append({
                    "rating": r["rating"], "text": r["review"],
                    "created_at": iso(r.get("created_timestamp")), "source": "api", "at": at})

    offsets = {l["listing_id"]: 0 for l in listings if l["shop_id"]}
    try:
        for l in listings:
            if not l["shop_id"]:
                continue
            r = client.get(REVIEWS.format(l["listing_id"]),
                           {"limit": c["review_page_size"], "min_created": window_start},
                           kind="reviews")
            rows = r.get("results", [])
            in_window = [x for x in rows if (x.get("created_timestamp") or 0) >= window_start]
            if len(rows) < c["review_page_size"]:
                n, how = len(in_window), "rows"           # complete page, count it directly
                if r.get("count") not in (None, len(in_window)):
                    notes.append(f"{l['listing_id']}: count field {r.get('count')} != rows {len(in_window)}; used rows")
            else:
                n, how = max(r.get("count") or 0, len(in_window)), "count_field"
            l["recent_reviews"] = metric(n, "api", at)
            l["recent_reviews_method"] = how
            take(l, rows)

        # older reviews, round-robin while the phrase budget lasts
        while offsets and client.phrase_calls_left() > 0:
            for l in listings:
                lid = l["listing_id"]
                if lid not in offsets or client.phrase_calls_left() <= 0:
                    continue
                r = client.get(REVIEWS.format(lid),
                               {"limit": c["review_page_size"], "offset": offsets[lid],
                                "max_created": window_start - 1}, kind="reviews")
                rows = r.get("results", [])
                if l["review_total"] is None and r.get("count") is not None:
                    l["review_total"] = r["count"] + (l["recent_reviews"]["value"] or 0)
                take(l, rows)
                offsets[lid] += c["review_page_size"]
                if len(rows) < c["review_page_size"]:
                    del offsets[lid]
    except BudgetExceeded as e:
        notes.append(f"budget stop: {e}")

    for l in listings:                     # fully paged but no count field
        if l["review_total"] is None and l["shop_id"] and l["listing_id"] not in offsets:
            l["review_total"] = l["reviews_scanned"]

    return {
        "phrase": phrase, "slug": slug, "seed": row.get("seed"), "buyer": row.get("buyer"),
        "job": row.get("job"), "collected_at": at,
        "top20_source": source, "capture_file": cap["file"] if cap else None,
        "capture_kind": cap["kind"] if cap else None,
        "api_result_count": metric(search.get("count"), "api", at),
        "api_top20_ids": api_ids[: c["top_n"]],
        "api_vs_page1_overlap": (len(set(api_ids[: c["top_n"]]) & set(top_ids)) if cap else None),
        "ads": ads, "listings": listings,
        "exports": load_exports(phrase),
        "calls_used": client.calls_phrase, "notes": notes,
    }


def read_phrases(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("phrase") or "").strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrases", default=str(DATA / "phrases.csv"))
    ap.add_argument("--phrase", help="collect a single phrase")
    ap.add_argument("--max-phrases", type=int)
    ap.add_argument("--skip-fresh", action="store_true",
                    help="skip phrases whose evidence file is younger than the listing TTL")
    a = ap.parse_args(argv)
    cfg = load_config()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if os.getenv("ETSY_ANALYTICS_AUTHORISED", "").lower() != "yes":
        print("Refusing live API calls: Etsy's API terms (s.5) prohibit using the API for "
              "analytics unless expressly authorised in writing. Once Etsy has approved this "
              "app's purpose in writing, set ETSY_ANALYTICS_AUTHORISED=yes in .env. "
              "See docs/api-notes.md.")
        return 3

    rows = ([{"seed": "", "phrase": a.phrase, "buyer": "", "job": ""}] if a.phrase
            else read_phrases(Path(a.phrases)))
    if a.max_phrases:
        rows = rows[: a.max_phrases]
    client = EtsyClient.from_env(cfg)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    done = 0
    for row in rows:
        out = EVIDENCE / f"{slugify(row['phrase'])}.json"
        if a.skip_fresh and out.exists() and \
                time.time() - out.stat().st_mtime < cfg["cache"]["ttl_hours"]["listings"] * 3600:
            continue
        try:
            ev = collect_phrase(client, row, cfg)
        except BudgetExceeded as e:
            print(f"stopped: {e}")
            break
        out.write_text(json.dumps(ev, indent=2), encoding="utf-8")
        done += 1
        print(f"{row['phrase']!r}: {ev['calls_used']} calls, top-20 from {ev['top20_source']}"
              + (f"; notes: {ev['notes']}" if ev["notes"] else ""))
    summary = (f"{now_iso()} phrases={done} calls_used={client.calls_run} "
               f"cache_hits={client.cache_hits} remaining_today={client.remaining_today}")
    print(summary)
    with open(DATA / "runs.log", "a", encoding="utf-8") as f:
        f.write(summary + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
