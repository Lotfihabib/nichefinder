"""Collector and client tests against synthetic fixtures. No network."""
import json
import shutil
import time
from pathlib import Path

import pytest

from src import collect, parse_capture
from src.common import load_config
from src.etsy_client import AuthError, BudgetExceeded, EtsyClient

FIX = Path(__file__).parent / "fixtures"
CFG = load_config()


class FakeEtsy:
    """Routes the three allowed endpoints to fixture data and records every request."""

    def __init__(self, fail_first=None):
        self.search = json.loads((FIX / "search.json").read_text())
        self.batch = {r["listing_id"]: r for r in json.loads((FIX / "batch.json").read_text())["results"]}
        now = int(time.time())
        self.reviews = {int(k): [{"rating": r["rating"], "review": r["review"],
                                  "created_timestamp": now - r["days_ago"] * 86400}
                                 for r in v] for k, v in json.loads((FIX / "reviews.json").read_text()).items()}
        self.requests = []
        self.fail_first = list(fail_first or [])

    def __call__(self, url, params, headers):
        self.requests.append((url, dict(params), dict(headers)))
        if self.fail_first:
            return self.fail_first.pop(0)
        path = url.replace("https://api.etsy.com", "")
        if path == "/v3/application/listings/active":
            return 200, {"x-remaining-today": "4900"}, self.search
        if path == "/v3/application/listings/batch":
            ids = params["listing_ids"]
            ids = [int(x) for x in (ids.split(",") if isinstance(ids, str) else ids)]
            res = [self.batch[i] for i in ids if i in self.batch]
            return 200, {}, {"count": len(res), "results": res}
        lid = int(path.split("/")[4])
        rows = self.reviews.get(lid, [])
        if "min_created" in params:
            rows = [r for r in rows if r["created_timestamp"] >= params["min_created"]]
        if "max_created" in params:
            rows = [r for r in rows if r["created_timestamp"] <= params["max_created"]]
        off, lim = params.get("offset", 0), params["limit"]
        return 200, {}, {"count": len(rows), "results": rows[off:off + lim]}


@pytest.fixture
def client(tmp_path):
    def make(transport=None, cfg=CFG):
        return EtsyClient("KEYSTRING", "SECRET", cfg, transport=transport or FakeEtsy(),
                          cache_dir=tmp_path / "cache", sleep=lambda s: None)
    return make


@pytest.fixture
def no_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(parse_capture, "INPUTS", tmp_path / "inputs")
    monkeypatch.setattr(collect, "INPUTS", tmp_path / "inputs")
    return tmp_path / "inputs"


ROW = {"seed": "budget spreadsheet", "phrase": "budget spreadsheet for freelancers",
       "buyer": "freelancers", "job": "track irregular income"}


# ------------------------------------------------------------------ client

def test_header_is_keystring_colon_secret(client):
    fake = FakeEtsy()
    client(fake).get("/v3/application/listings/active", {"keywords": "x", "limit": 1}, "search")
    assert fake.requests[0][2]["x-api-key"] == "KEYSTRING:SECRET"


def test_missing_secret_rejected():
    with pytest.raises(AuthError):
        EtsyClient("KEYSTRING", "", CFG)


def test_403_explains_header_format(client):
    c = client(FakeEtsy(fail_first=[(403, {}, {"error": "Invalid API key"})]))
    with pytest.raises(AuthError, match="keystring:shared_secret"):
        c.get("/v3/application/listings/active", {"keywords": "x"}, "search")


def test_429_retries_and_counts_calls(client):
    fake = FakeEtsy(fail_first=[(429, {"retry-after": "1"}, {}), (503, {}, {})])
    c = client(fake)
    c.get("/v3/application/listings/active", {"keywords": "x"}, "search")
    assert c.calls_run == 3 and len(fake.requests) == 3


def test_cache_hit_is_free(client):
    c = client()
    for _ in range(3):
        c.get("/v3/application/listings/active", {"keywords": "x"}, "search")
    assert c.calls_run == 1 and c.cache_hits == 2


def test_expired_cache_is_deleted(client, tmp_path):
    c = client()
    c.get("/v3/application/listings/active", {"keywords": "x"}, "search")
    c.clock = lambda: time.time() + 7 * 3600            # search TTL is 6 h
    c.get("/v3/application/listings/active", {"keywords": "x"}, "search")
    assert c.calls_run == 2


def test_phrase_budget_enforced(client):
    cfg = {**CFG, "budget": {**CFG["budget"], "calls_per_phrase": 2}}
    c = client(cfg=cfg)
    c.get("/v3/application/listings/active", {"keywords": "a"}, "search")
    c.get("/v3/application/listings/active", {"keywords": "b"}, "search")
    with pytest.raises(BudgetExceeded):
        c.get("/v3/application/listings/active", {"keywords": "c"}, "search")


def test_daily_budget_enforced(client):
    cfg = {**CFG, "budget": {**CFG["budget"], "requests_per_day": 1}}
    c = client(cfg=cfg)
    c.get("/v3/application/listings/active", {"keywords": "a"}, "search")
    c.start_phrase()
    with pytest.raises(BudgetExceeded, match="daily"):
        c.get("/v3/application/listings/active", {"keywords": "b"}, "search")


@pytest.mark.parametrize("path", ["/v3/application/shops/1/receipts",
                                  "/v3/application/listings/12/inventory",
                                  "/v3/application/users/1"])
def test_only_allowed_endpoints(client, path):
    with pytest.raises(ValueError):
        client().get(path, {}, "search")


# ------------------------------------------------------------------ collector

def test_collect_without_capture(client, no_inputs):
    c = client()
    ev = collect.collect_phrase(c, ROW, CFG)
    ls = ev["listings"]
    assert ev["top20_source"] == "api" and len(ls) == 20
    assert ev["calls_used"] <= CFG["budget"]["calls_per_phrase"]
    assert ev["calls_used"] == c.calls_phrase >= 22                  # search + batch + 20 recent
    assert [l["listing_id"] for l in ls] == ev["api_top20_ids"]
    # EUR listing with no converted price -> UNKNOWN, never guessed
    assert ls[7]["price_usd"]["value"] is None and ls[7]["currency"]["value"] == "EUR"
    assert ls[0]["price_usd"] == {"value": 4.99, "source": "api", "at": ev["collected_at"]}
    # recent-review counts from the 90-day window
    assert [l["recent_reviews"]["value"] for l in ls[:10]] == [0, 1, 2, 3, 5, 9, 0, 150, 0, 4]  # #5 includes its recent 1-star
    assert ls[7]["recent_reviews_method"] == "count_field"
    # badges are UNKNOWN without a capture
    assert all(l["demand_badges"]["value"] is None for l in ls)
    # 1-3 star text collected, including the older one
    texts = [r["text"] for l in ls for r in l["low_star_reviews"]]
    assert any("formulas broke" in t for t in texts)
    assert any("No instructions" in t for t in texts)
    assert ls[0]["shop_sales"]["value"] == 120


def test_collect_with_capture_excludes_ads(client, no_inputs):
    d = no_inputs / "captures" / "budget-spreadsheet-for-freelancers"
    d.mkdir(parents=True)
    shutil.copy(FIX / "capture_page1.html", d / "page1.html")
    ev = collect.collect_phrase(client(), ROW, CFG)
    assert ev["top20_source"] == "page_capture"
    assert {a["listing_id"] for a in ev["ads"]} == {900029, 900028}
    ids = [l["listing_id"] for l in ev["listings"]]
    assert 900029 not in ids and 900028 not in ids and len(ids) == 20
    badges = {l["listing_id"]: l["demand_badges"]["value"] for l in ev["listings"]}
    assert badges[900000] == ["bestseller"] and badges[900004] == ["in_carts"]
    assert badges[900006] == ["popular_now"] and badges[900001] == []
    assert ev["listings"][4]["in_carts"]["value"] == 20


def test_parse_capture_dedupes_nested_ids():
    items = parse_capture.parse_html((FIX / "capture_page1.html").read_text())
    ids = [i["listing_id"] for i in items]
    assert len(ids) == len(set(ids)) == 26
    assert items[0]["is_ad"] and items[0]["listing_id"] == 900029


def test_exports_matched_by_keyword(client, no_inputs):
    (no_inputs / "exports").mkdir(parents=True)
    (no_inputs / "exports" / "erank.csv").write_text(
        "Keyword,Avg Searches,Competition\nbudget spreadsheet for freelancers,1200,5000\nother,1,1\n")
    ev = collect.collect_phrase(client(), ROW, CFG)
    m = ev["exports"]["erank.csv:Avg Searches"]
    assert m["value"] == "1200" and m["source"] == "export:erank.csv"
