"""Stage 2: score the 5 questions and apply the decision rule.

Q1, Q2 and Q5 are computed from evidence files. Q3 and Q4 come from the
niche-judge's judgment files, and this module checks them against the evidence.

Missing data never passes. A numeric question with unknown inputs is scored
twice: once with every unknown filled pessimistically and once optimistically.
If both give the same verdict, that's the verdict. Otherwise it's UNKNOWN, and
the range is recorded. Every verdict function here is monotone, so the two
extremes bound every possible completion.

Usage:  python -m src.score            # all evidence files -> data/scores.json
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from typing import Any, Callable

from .common import DATA, EVIDENCE, JUDGMENTS, REDTEAM, etsy_search_url, load_config, val

PASS, BORDERLINE, FAIL, UNKNOWN = "PASS", "BORDERLINE", "FAIL", "UNKNOWN"
ORDER = {FAIL: 0, BORDERLINE: 1, PASS: 2}
QUESTIONS = ["q1", "q2", "q3", "q4", "q5"]
INF = float("inf")


# ---------------------------------------------------------------- helpers

def _bounded(fn: Callable[[bool], str]) -> tuple[str, list[str]]:
    """fn(optimistic) -> verdict. Returns (verdict, [pessimistic, optimistic])."""
    lo, hi = fn(False), fn(True)
    return (lo if lo == hi else UNKNOWN), [lo, hi]


def listing_demand(listing: dict, cfg: dict) -> bool | None:
    """True / False / None (unknown) for 'this listing shows recent demand'."""
    q1 = cfg["thresholds"]["q1"]
    recent = val(listing.get("recent_reviews"))
    badges = val(listing.get("demand_badges"))       # None = no capture, unknown
    if recent is not None and recent >= q1["recent_reviews_min"]:
        return True
    if badges is not None and any(b in q1["demand_badges"] for b in badges):
        return True
    if recent is not None and badges is not None:
        return False
    return None


def _fill(x, optimistic: bool, good, bad):
    return x if x is not None else (good if optimistic else bad)


# ---------------------------------------------------------------- Q1

def score_q1(ev: dict, cfg: dict) -> dict:
    t = cfg["thresholds"]["q1"]
    listings = ev.get("listings") or []
    if not listings:
        return {"verdict": UNKNOWN, "detail": "no top-20 listings", "range": None}
    demand = [listing_demand(l, cfg) for l in listings]

    def verdict(opt: bool) -> str:
        n = sum(1 for d in demand if _fill(d, opt, True, False))
        return PASS if n >= t["pass_min"] else BORDERLINE if n >= t["borderline_min"] else FAIL

    v, rng = _bounded(verdict)
    known = sum(1 for d in demand if d is True)
    unknown = sum(1 for d in demand if d is None)
    selling_ids = [l["listing_id"] for l, d in zip(listings, demand) if d is True]
    return {
        "verdict": v, "range": rng,
        "value": known, "unknown_listings": unknown, "of": len(listings),
        "selling_listing_ids": selling_ids,
        "detail": f"{known}/{len(listings)} top listings show recent demand"
                  + (f", {unknown} unknown" if unknown else ""),
    }


# ---------------------------------------------------------------- Q2

def top3_share(listings: list[dict]) -> float:
    counts: dict[Any, int] = {}
    for l in listings:
        counts[l.get("shop_id")] = counts.get(l.get("shop_id"), 0) + 1
    top = sorted(counts.values(), reverse=True)[:3]
    return sum(top) / len(listings) if listings else 0.0


def score_q2(ev: dict, cfg: dict) -> dict:
    t = cfg["thresholds"]["q2"]
    listings = ev.get("listings") or []
    if not listings:
        return {"verdict": UNKNOWN, "detail": "no top-20 listings", "range": None}
    sales = [val(l.get("shop_sales")) for l in listings]
    demand = [listing_demand(l, cfg) for l in listings]
    share = top3_share(listings)

    def verdict(opt: bool) -> str:
        s = [_fill(x, opt, 0, INF) for x in sales]          # optimistic = small shop
        d = [_fill(x, opt, True, False) for x in demand]
        if share >= t["top3_share_fail"] or all(x >= t["mega_shop_min_sales"] for x in s):
            return FAIL
        if any(si < t["small_shop_max_sales"] and di for si, di in zip(s, d)):
            return PASS
        return BORDERLINE

    v, rng = _bounded(verdict)
    small = [l["listing_id"] for l, s in zip(listings, sales)
             if s is not None and s < t["small_shop_max_sales"]]
    small_selling = [l["listing_id"] for l, s, d in zip(listings, sales, demand)
                     if s is not None and s < t["small_shop_max_sales"] and d is True]
    known_sales = [s for s in sales if s is not None]
    return {
        "verdict": v, "range": rng,
        "top3_share": round(share, 3),
        "small_shop_listing_ids": small,
        "small_shop_selling_listing_ids": small_selling,
        "min_shop_sales": min(known_sales) if known_sales else None,
        "unknown_shop_sales": sum(1 for s in sales if s is None),
        "detail": f"top-3 shops hold {share:.0%}; {len(small)} small-shop listings, "
                  f"{len(small_selling)} of them selling",
    }


# ---------------------------------------------------------------- Q5

def price_stats(listings: list[dict]) -> dict:
    prices = sorted(p for p in (val(l.get("price_usd")) for l in listings) if p is not None)
    if not prices:
        return {"p25": None, "median": None, "p75": None, "n": 0}
    if len(prices) == 1:
        q = [prices[0]] * 3
    else:
        q = statistics.quantiles(prices, n=4, method="inclusive")
    return {"p25": round(q[0], 2), "median": round(q[1], 2), "p75": round(q[2], 2),
            "n": len(prices)}


def score_q5(ev: dict, cfg: dict) -> dict:
    t = cfg["thresholds"]["q5"]
    listings = ev.get("listings") or []
    if not listings:
        return {"verdict": UNKNOWN, "detail": "no top-20 listings", "range": None}
    prices = [val(l.get("price_usd")) for l in listings]
    demand = [listing_demand(l, cfg) for l in listings]

    def verdict(opt: bool) -> str:
        p = [_fill(x, opt, INF, 0.0) for x in prices]
        d = [_fill(x, opt, True, False) for x in demand]
        med = statistics.median(p)
        med = med if med == INF else round(med, 2)
        if med >= t["price_floor"]:
            return PASS
        if med <= t["hard_fail_median"]:
            return FAIL
        n = sum(1 for pi, di in zip(p, d) if pi >= t["price_floor"] and di)
        return BORDERLINE if n >= t["borderline_min_listings"] else FAIL

    v, rng = _bounded(verdict)
    stats = price_stats(listings)
    above = sum(1 for p, d in zip(prices, demand)
                if p is not None and p >= t["price_floor"] and d is True)
    return {
        "verdict": v, "range": rng, **stats,
        "unknown_prices": sum(1 for p in prices if p is None),
        "selling_at_or_above_floor": above,
        "detail": f"median ${stats['median']}" if stats["median"] is not None else "no prices",
    }


# ---------------------------------------------------------------- Q3 / Q4

def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def score_q3(ev: dict, judgment: dict | None, cfg: dict) -> dict:
    """Judge's verdict, capped by what the evidence supports."""
    if not judgment or "q3" not in judgment:
        return {"verdict": UNKNOWN, "detail": "no judgment yet"}
    j = judgment["q3"]
    max_words = cfg["report"]["quote_max_words"]
    top_ids = {l["listing_id"] for l in ev.get("listings") or []}
    corpus = [_norm(r.get("text") or "") for l in ev.get("listings") or []
              for r in l.get("low_star_reviews") or []]
    problems, evidenced = [], []
    for f in j.get("flaws") or []:
        ids = [int(i) for i in f.get("listing_ids") or []]
        bad_ids = [i for i in ids if i not in top_ids]
        quotes = f.get("quotes") or []
        long_q = [q for q in quotes if len(q.split()) > max_words]
        unfound = [q for q in quotes if not any(_norm(q) in c for c in corpus)]
        if not ids:
            problems.append(f"flaw '{f.get('flaw')}' cites no listing IDs")
        if bad_ids:
            problems.append(f"flaw '{f.get('flaw')}' cites IDs not in top 20: {bad_ids}")
        if long_q:
            problems.append(f"quotes over {max_words} words: {long_q}")
        if unfound:
            problems.append(f"quotes not found in collected reviews: {unfound}")
        has_basis = bool(quotes) or f.get("type") in {
            "missing_format", "wrong_buyer", "no_instructions", "poor_preview"}
        if ids and not bad_ids and not long_q and not unfound and has_basis:
            evidenced.append(f)
    judged = j.get("verdict", UNKNOWN)
    cap = PASS if evidenced else (BORDERLINE if j.get("flaws") else FAIL)
    verdict = judged if judged == UNKNOWN or ORDER[judged] <= ORDER[cap] else cap
    return {"verdict": verdict, "judge_verdict": judged, "evidenced_flaws": len(evidenced),
            "problems": problems,
            "detail": f"{len(evidenced)} evidenced flaw(s)"
                      + (f"; capped from {judged}" if verdict != judged else "")}


def score_q4(ev: dict, judgment: dict | None, cfg: dict) -> dict:
    if not judgment or "q4" not in judgment:
        return {"verdict": UNKNOWN, "detail": "no judgment yet"}
    j = judgment["q4"]
    prompt = (j.get("recreation_prompt") or "").strip()
    rec = j.get("recreation_verdict")        # succeeds | partial | fails
    cls = j.get("product_class")             # functional | static
    if not prompt or rec not in {"succeeds", "partial", "fails"}:
        return {"verdict": UNKNOWN, "detail": "recreation test not recorded"}
    if cls == "static" or rec == "succeeds":
        cap = FAIL
    elif rec == "partial" or cls != "functional":
        cap = BORDERLINE
    else:
        cap = PASS
    judged = j.get("verdict", UNKNOWN)
    verdict = cap if judged == UNKNOWN else (judged if ORDER[judged] <= ORDER[cap] else cap)
    return {"verdict": verdict, "judge_verdict": judged, "product_class": cls,
            "recreation_prompt": prompt, "recreation_verdict": rec,
            "detail": f"{cls}; recreation {rec}"}


# ---------------------------------------------------------------- decision

def decide(verdicts: dict[str, str], cfg: dict) -> dict:
    d = cfg["decision"]
    fails = [q for q in QUESTIONS if verdicts.get(q) == FAIL]
    if fails:
        return {"decision": "reject", "killed_by": fails}
    unknown_num = [q for q in ("q1", "q2", "q5") if verdicts.get(q) == UNKNOWN]
    if unknown_num:
        return {"decision": "needs_capture", "unknown": unknown_num}
    unknown_judg = [q for q in ("q3", "q4") if verdicts.get(q) == UNKNOWN]
    if unknown_judg:
        return {"decision": "needs_judgment", "unknown": unknown_judg}
    nb = [q for q in QUESTIONS if verdicts.get(q) == BORDERLINE]
    if not nb:
        return {"decision": "shortlist"}
    if len(nb) <= d["max_borderline_for_watchlist"]:
        return {"decision": "watchlist", "borderline": nb}
    return {"decision": d["too_many_borderline"], "borderline": nb,
            "killed_by": [f"{len(nb)} BORDERLINE ({', '.join(nb)})"]}


def capture_instruction(ev: dict) -> str:
    return (f"Open {etsy_search_url(ev['phrase'])} in your own browser (logged out or "
            f"private window), let page 1 load fully, and save it as "
            f"inputs/captures/{ev['slug']}/page1.html (File > Save Page As, "
            f"'Webpage, HTML only'), or save screenshots of all of page 1 in that folder.")


def score_phrase(ev: dict, judgment: dict | None, redteam: dict | None, cfg: dict) -> dict:
    q = {
        "q1": score_q1(ev, cfg), "q2": score_q2(ev, cfg), "q3": score_q3(ev, judgment, cfg),
        "q4": score_q4(ev, judgment, cfg), "q5": score_q5(ev, cfg),
    }
    verdicts = {k: v["verdict"] for k, v in q.items()}
    dec = decide(verdicts, cfg)
    if dec["decision"] == "needs_capture":
        dec["action"] = capture_instruction(ev)
    if redteam and dec["decision"] == "shortlist" and redteam.get("refuted"):
        dec = {"decision": redteam.get("downgrade_to", "watchlist"),
               "downgraded_by_red_team": redteam.get("reason", ""),
               "killed_by": ["red team: " + redteam.get("reason", "")]}
    confidence = "high" if ev.get("top20_source") == "page_capture" else "low"
    return {"phrase": ev["phrase"], "seed": ev.get("seed"), "slug": ev["slug"],
            "buyer": ev.get("buyer"), "collected_at": ev.get("collected_at"),
            "top20_source": ev.get("top20_source"), "confidence_q1_q2": confidence,
            "questions": q, "verdicts": verdicts, **dec}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def redteam_packet(result: dict, judgment: dict) -> dict:
    """What the red team may see: verdicts, numbers and the judge's *claims*.
    No reasoning or notes from the judge."""
    q3 = judgment.get("q3") or {}
    q4 = judgment.get("q4") or {}
    return {
        "phrase": result["phrase"], "slug": result["slug"], "buyer": result.get("buyer"),
        "evidence_file": f"data/evidence/{result['slug']}.json",
        "verdicts": result["verdicts"],
        "numbers": {k: {kk: vv for kk, vv in v.items() if kk not in ("problems",)}
                    for k, v in result["questions"].items() if k in ("q1", "q2", "q5")},
        "q3_claimed_flaws": [{k: f.get(k) for k in ("flaw", "type", "listing_ids", "quotes")}
                             for f in q3.get("flaws") or []],
        "q4_claim": {k: q4.get(k) for k in ("product_class", "recreation_prompt", "recreation_verdict")},
        "product_to_build": (judgment.get("product") or {}).get("spec"),
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    cfg = load_config()
    results = []
    for p in sorted(EVIDENCE.glob("*.json")):
        ev = json.loads(p.read_text(encoding="utf-8"))
        results.append(score_phrase(ev, _load(JUDGMENTS / p.name), _load(REDTEAM / p.name), cfg))
    out = DATA / "scores.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    if "--redteam-packets" in argv:
        pdir = DATA / "redteam_packets"
        pdir.mkdir(parents=True, exist_ok=True)
        todo = [r for r in results if r["decision"] == "shortlist" and not (REDTEAM / f"{r['slug']}.json").exists()]
        for r in todo:
            pkt = redteam_packet(r, _load(JUDGMENTS / f"{r['slug']}.json") or {})
            (pdir / f"{r['slug']}.json").write_text(json.dumps(pkt, indent=2), encoding="utf-8")
        print(f"red-team packets: {[r['slug'] for r in todo]}")
    counts: dict[str, int] = {}
    for r in results:
        counts[r["decision"]] = counts.get(r["decision"], 0) + 1
    print(f"scored {len(results)} phrases -> {out}  {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
