"""Threshold-boundary tests for src/score.py. Thresholds come from config.yaml."""
import pytest

from src.common import load_config, metric
from src.score import (BORDERLINE, FAIL, PASS, UNKNOWN, decide, score_q1, score_q2,
                       score_q3, score_q4, score_q5, score_phrase)

CFG = load_config()
AT = "2026-10-16T00:00:00+00:00"


def L(i, *, recent=0, badges=(), sales=10_000, shop=None, price=15.0, captured=True,
      low_star=()):
    """One top-20 listing. captured=False means no page capture, so badges are unknown."""
    return {
        "listing_id": 1000 + i, "rank": i + 1, "shop_id": shop if shop is not None else 500 + i,
        "recent_reviews": metric(recent, "api", AT),
        "demand_badges": metric(list(badges), "page_capture", AT) if captured
        else metric(None, "page_capture", AT),
        "shop_sales": metric(sales, "api", AT),
        "price_usd": metric(price, "api", AT),
        "low_star_reviews": [{"rating": 2, "text": t, "source": "api", "at": AT} for t in low_star],
    }


def EV(listings, source="page_capture"):
    return {"phrase": "test phrase here", "slug": "test-phrase-here",
            "top20_source": source, "listings": listings}


def with_demand(n, total=20, **kw):
    return [L(i, recent=3 if i < n else 0, **kw) for i in range(total)]


# ------------------------------------------------------------------ Q1

@pytest.mark.parametrize("n,expected", [(2, FAIL), (3, BORDERLINE), (4, BORDERLINE), (5, PASS)])
def test_q1_demand_count_boundaries(n, expected):
    assert score_q1(EV(with_demand(n)), CFG)["verdict"] == expected


def test_q1_two_recent_reviews_is_not_demand():
    ls = [L(i, recent=2) for i in range(20)]
    assert score_q1(EV(ls), CFG)["verdict"] == FAIL


def test_q1_badges_count_as_demand():
    ls = [L(i, badges=["bestseller"] if i < 3 else (), ) for i in range(20)]
    ls[3]["demand_badges"] = metric(["in_carts"], "page_capture", AT)
    ls[4]["demand_badges"] = metric(["in_carts"], "page_capture", AT)
    assert score_q1(EV(ls), CFG)["verdict"] == PASS


def test_q1_unknown_badges_without_capture_is_unknown_not_fail():
    ls = with_demand(2, captured=False)
    r = score_q1(EV(ls, source="api"), CFG)
    assert r["verdict"] == UNKNOWN and r["range"] == [FAIL, PASS]


def test_q1_enough_known_demand_passes_even_without_capture():
    assert score_q1(EV(with_demand(5, captured=False), source="api"), CFG)["verdict"] == PASS


def test_q1_missing_review_counts_never_pass():
    ls = with_demand(4)
    ls[10]["recent_reviews"] = metric(None, "api", AT)
    r = score_q1(EV(ls), CFG)
    assert r["verdict"] == UNKNOWN and r["range"] == [BORDERLINE, PASS]


def test_q1_no_listings_unknown():
    assert score_q1(EV([]), CFG)["verdict"] == UNKNOWN


# ------------------------------------------------------------------ Q2

def test_q2_small_shop_499_with_demand_passes():
    ls = with_demand(5)
    ls[0]["shop_sales"] = metric(499, "api", AT)
    assert score_q2(EV(ls), CFG)["verdict"] == PASS


def test_q2_shop_at_500_is_not_small():
    ls = with_demand(5)
    ls[0]["shop_sales"] = metric(500, "api", AT)
    assert score_q2(EV(ls), CFG)["verdict"] == BORDERLINE


def test_q2_small_shop_without_demand_is_borderline():
    ls = with_demand(5)
    ls[10]["shop_sales"] = metric(499, "api", AT)      # listing 10 has no demand
    assert score_q2(EV(ls), CFG)["verdict"] == BORDERLINE


def test_q2_all_mega_shops_fail():
    ls = with_demand(5, sales=20_000)
    assert score_q2(EV(ls), CFG)["verdict"] == FAIL


def test_q2_one_shop_below_mega_avoids_that_fail():
    ls = with_demand(5, sales=20_000)
    ls[19]["shop_sales"] = metric(19_999, "api", AT)
    assert score_q2(EV(ls), CFG)["verdict"] == BORDERLINE


@pytest.mark.parametrize("held,expected", [(9, BORDERLINE), (10, FAIL)])
def test_q2_top3_share_boundary(held, expected):
    ls = with_demand(5)
    for i in range(held):                     # spread `held` listings over 3 shops
        ls[i]["shop_id"] = 1 + i % 3
    assert score_q2(EV(ls), CFG)["verdict"] == expected


def test_q2_monopoly_beats_small_shop_pass():
    ls = with_demand(5)
    ls[0]["shop_sales"] = metric(10, "api", AT)
    for i in range(10):
        ls[i]["shop_id"] = 7
    assert score_q2(EV(ls), CFG)["verdict"] == FAIL


def test_q2_unknown_sales_is_unknown():
    ls = with_demand(5)
    ls[0]["shop_sales"] = metric(None, "api", AT)
    assert score_q2(EV(ls), CFG)["verdict"] == UNKNOWN


# ------------------------------------------------------------------ Q5

def priced(median_price, n_selling_above=0):
    """20 listings whose median is exactly median_price."""
    ls = [L(i, price=median_price) for i in range(20)]
    for i in range(n_selling_above):           # extra listings >= $12 with demand
        ls[i] = L(i, price=20.0, recent=5)
    for i in range(n_selling_above, 2 * n_selling_above):   # keep the median in place
        ls[i] = L(i, price=1.0)
    return ls


@pytest.mark.parametrize("median,selling,expected", [
    (4.99, 5, FAIL),        # below hard floor
    (5.00, 5, FAIL),        # "median <= $5" fails even with selling listings
    (11.99, 5, BORDERLINE),
    (11.99, 4, FAIL),
    (12.00, 0, PASS),
])
def test_q5_median_boundaries(median, selling, expected):
    r = score_q5(EV(priced(median, selling)), CFG)
    assert r["median"] == median
    assert r["verdict"] == expected


def test_q5_unknown_prices_are_unknown_when_they_could_matter():
    ls = [L(i, price=11.0) for i in range(20)]
    for i in range(10):
        ls[i]["price_usd"] = metric(None, "api", AT)
    assert score_q5(EV(ls), CFG)["verdict"] == UNKNOWN


def test_q5_one_unknown_price_cannot_rescue_a_cheap_niche():
    ls = [L(i, price=3.0) for i in range(20)]
    ls[0]["price_usd"] = metric(None, "api", AT)
    assert score_q5(EV(ls), CFG)["verdict"] == FAIL


def test_q5_percentiles():
    ls = [L(i, price=float(i + 1)) for i in range(20)]
    r = score_q5(EV(ls), CFG)
    assert (r["p25"], r["median"], r["p75"]) == (5.75, 10.5, 15.25)


# ------------------------------------------------------------------ Q3 / Q4

def test_q3_pass_needs_quotes_found_in_evidence():
    ev = EV([L(0, low_star=["The formulas broke when I added a second income line"])])
    good = {"q3": {"verdict": PASS, "flaws": [{
        "flaw": "formulas break", "type": "review_complaint", "listing_ids": [1000],
        "quotes": ["formulas broke when I added a second income line"]}]}}
    assert score_q3(ev, good, CFG)["verdict"] == PASS

    made_up = {"q3": {"verdict": PASS, "flaws": [{
        "flaw": "x", "type": "review_complaint", "listing_ids": [1000],
        "quotes": ["totally invented complaint"]}]}}
    assert score_q3(ev, made_up, CFG)["verdict"] == BORDERLINE

    wrong_id = {"q3": {"verdict": PASS, "flaws": [{
        "flaw": "x", "type": "no_instructions", "listing_ids": [999999]}]}}
    assert score_q3(ev, wrong_id, CFG)["verdict"] == BORDERLINE


def test_q3_quote_over_15_words_is_rejected():
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
    ev = EV([L(0, low_star=[text])])
    j = {"q3": {"verdict": PASS, "flaws": [{"flaw": "x", "type": "review_complaint",
                                            "listing_ids": [1000], "quotes": [text]}]}}
    assert score_q3(ev, j, CFG)["verdict"] == BORDERLINE


def test_q3_no_flaws_fails_and_missing_judgment_unknown():
    ev = EV([L(0)])
    assert score_q3(ev, {"q3": {"verdict": PASS, "flaws": []}}, CFG)["verdict"] == FAIL
    assert score_q3(ev, None, CFG)["verdict"] == UNKNOWN


@pytest.mark.parametrize("cls,rec,judged,expected", [
    ("functional", "fails", PASS, PASS),
    ("functional", "partial", PASS, BORDERLINE),
    ("functional", "succeeds", PASS, FAIL),
    ("static", "fails", PASS, FAIL),
    ("functional", "fails", BORDERLINE, BORDERLINE),   # judge may be stricter
])
def test_q4_recreation_caps(cls, rec, judged, expected):
    j = {"q4": {"verdict": judged, "product_class": cls, "recreation_verdict": rec,
                "recreation_prompt": "make me a ..."}}
    assert score_q4(EV([L(0)]), j, CFG)["verdict"] == expected


def test_q4_without_prompt_is_unknown():
    j = {"q4": {"verdict": PASS, "product_class": "functional", "recreation_verdict": "fails"}}
    assert score_q4(EV([L(0)]), j, CFG)["verdict"] == UNKNOWN


# ------------------------------------------------------------------ decision

def V(**kw):
    base = {q: PASS for q in ("q1", "q2", "q3", "q4", "q5")}
    base.update(kw)
    return base


@pytest.mark.parametrize("verdicts,expected", [
    (V(), "shortlist"),
    (V(q3=BORDERLINE), "watchlist"),
    (V(q3=BORDERLINE, q5=BORDERLINE), "reject"),
    (V(q4=FAIL), "reject"),
    (V(q4=FAIL, q1=UNKNOWN), "reject"),          # any FAIL rejects
    (V(q1=UNKNOWN), "needs_capture"),
    (V(q5=UNKNOWN, q3=UNKNOWN), "needs_capture"),
    (V(q3=UNKNOWN), "needs_judgment"),
])
def test_decision_rule(verdicts, expected):
    assert decide(verdicts, CFG)["decision"] == expected


def test_needs_capture_names_the_page():
    ev = EV(with_demand(2, captured=False), source="api")
    r = score_phrase(ev, None, None, CFG)
    assert r["decision"] == "needs_capture"
    assert "inputs/captures/test-phrase-here/" in r["action"]
    assert "etsy.com/search?q=test+phrase+here" in r["action"]


def test_red_team_downgrades_shortlist():
    ev = EV(with_demand(6))
    ev["listings"][0]["shop_sales"] = metric(100, "api", AT)
    ev["listings"][0]["low_star_reviews"] = [{"rating": 1, "text": "no instructions at all",
                                              "source": "api", "at": AT}]
    j = {"q3": {"verdict": PASS, "flaws": [{"flaw": "no instructions", "type": "review_complaint",
                                            "listing_ids": [1000],
                                            "quotes": ["no instructions at all"]}]},
         "q4": {"verdict": PASS, "product_class": "functional", "recreation_verdict": "fails",
                "recreation_prompt": "p"}}
    assert score_phrase(ev, j, None, CFG)["decision"] == "shortlist"
    rt = {"refuted": True, "downgrade_to": "watchlist", "reason": "Q1 carried by one viral listing"}
    r = score_phrase(ev, j, rt, CFG)
    assert r["decision"] == "watchlist" and "viral" in r["downgraded_by_red_team"]
