"""Stage 4: write reports/<date>-niche-report.md and reports/<date>-scores.csv, then stop.

Usage:  python -m src.report        (run python -m src.score first)
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date

from .common import DATA, EVIDENCE, JUDGMENTS, REPORTS, load_config

DISCLAIMER = ("The term 'Etsy' is a trademark of Etsy, Inc. This Application uses Etsy's API, "
              "but is not endorsed or certified by Etsy.")

PROMOTE = {
    "q1": lambda q, c: f"≥{c['thresholds']['q1']['pass_min']} top-20 listings showing recent demand (now {q.get('value')})",
    "q2": lambda q, c: f"a shop with <{c['thresholds']['q2']['small_shop_max_sales']} sales whose listing shows recent demand",
    "q3": lambda q, c: "a flaw backed by review quotes or an observed gap, citing listing IDs",
    "q4": lambda q, c: "a functional product whose AI recreation test clearly fails",
    "q5": lambda q, c: f"median price ≥ ${c['thresholds']['q5']['price_floor']:.2f} (now ${q.get('median')})",
}

SOURCES = {"q1": "api reviews (90 d) + page_capture badges", "q2": "api shop.transaction_sold_count",
           "q3": "niche-judge over api 1–3★ reviews", "q4": "niche-judge recreation test",
           "q5": "api converted_price (USD, minimum variant price)"}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def rank_key(r: dict):
    q = r["questions"]
    return (r["confidence_q1_q2"] == "high", q["q1"].get("value") or 0,
            len(q["q2"].get("small_shop_selling_listing_ids") or []), q["q5"].get("median") or 0)


def missing_data(r: dict, ev: dict) -> list[str]:
    out = []
    if r["top20_source"] != "page_capture":
        out.append("no page capture: top 20 is API relevance order, not buyer-facing page 1; badges and ads unknown")
    for k in ("q1", "q2", "q5"):
        q = r["questions"][k]
        for f in ("unknown_listings", "unknown_shop_sales", "unknown_prices"):
            if q.get(f):
                out.append(f"{k}: {q[f]} {f.replace('_', ' ')}")
    partial = [l["listing_id"] for l in ev.get("listings", [])
               if l.get("review_total") and l["reviews_scanned"] < l["review_total"]]
    if partial:
        out.append(f"1–3★ scan partial for {len(partial)} listing(s) (budget cap)")
    out += ev.get("notes", [])
    return out


def shortlist_block(i: int, r: dict, ev: dict, j: dict) -> str:
    q = r["questions"]
    prod = j.get("product") or {}
    lines = [f"### {i}. {r['phrase']}",
             f"**Buyer:** {r.get('buyer') or ev.get('buyer') or '—'}  ·  **Seed:** {r.get('seed') or '—'}  ·  "
             f"**Collected:** {r['collected_at']}  ·  **Confidence:** "
             f"{'high (page capture)' if r['confidence_q1_q2'] == 'high' else 'low (API order, no capture)'}",
             "", "| Q | Verdict | Numbers | Source |", "|---|---|---|---|"]
    for k in ("q1", "q2", "q3", "q4", "q5"):
        lines.append(f"| {k.upper()} | {q[k]['verdict']} | {q[k].get('detail', '')} | {SOURCES[k]} |")
    q5 = q["q5"]
    lines += ["", f"**Price band (USD, n={q5.get('n')}):** 25th ${q5.get('p25')} · median ${q5.get('median')} · "
                  f"75th ${q5.get('p75')}",
              f"**Flaw to fix:** {prod.get('flaw_to_fix') or '—'}"]
    for f in (j.get("q3") or {}).get("flaws") or []:
        quotes = "; ".join(f'"{x}"' for x in f.get("quotes") or [])
        lines.append(f"- {f.get('flaw')} — listings {f.get('listing_ids')}" + (f" — {quotes}" if quotes else ""))
    q4 = q["q4"]
    lines += [f"**AI recreation test:** prompt `{q4.get('recreation_prompt', '—')}` → {q4.get('recreation_verdict', '—')}",
              "", f"**Core product (target ${prod.get('target_price_usd', '—')}):** {prod.get('spec') or '—'}",
              "", "**Variations:**"]
    lines += [f"{n}. {v}" for n, v in enumerate(prod.get("variations") or [], 1)] or ["—"]
    md = missing_data(r, ev)
    lines += ["", "**Missing data:** " + ("; ".join(md) if md else "none"), ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    cfg = load_config()
    scores_path = DATA / "scores.json"
    if not scores_path.exists():
        print("run `python -m src.score` first")
        return 1
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    today = date.today().isoformat()
    REPORTS.mkdir(exist_ok=True)

    by = {k: [r for r in scores if r["decision"] == k]
          for k in ("shortlist", "watchlist", "needs_capture", "needs_judgment", "reject")}
    ranked = sorted(by["shortlist"], key=rank_key, reverse=True)
    cap = cfg["report"]["shortlist_max"]
    shortlist, overflow = ranked[:cap], ranked[cap:]

    out = [f"# Niche report — {today}", "",
           f"{len(scores)} phrases scored: {len(shortlist)} shortlisted, "
           f"{len(by['watchlist']) + len(overflow)} watchlist, {len(by['needs_capture'])} need a capture, "
           f"{len(by['needs_judgment'])} need judging, {len(by['reject'])} rejected.", "",
           "> Sales are not public. Reviews, favourites and badges are **proxies**, and every number "
           "below is an estimate from those proxies. API search order is **not** the page 1 a buyer "
           "sees (ads, personalisation, Etsy ranking). Without a page capture, Q1/Q2 are low confidence. "
           "Numbers are a snapshot taken at the collection time shown, so re-collect before acting on old ones.",
           "", f"## Shortlist (max {cap})", ""]
    for i, r in enumerate(shortlist, 1):
        out.append(shortlist_block(i, r, _load(EVIDENCE / f"{r['slug']}.json"),
                                   _load(JUDGMENTS / f"{r['slug']}.json")))
    if not shortlist:
        out.append("_None this run._\n")

    out += ["## Watchlist", ""]
    for r in by["watchlist"] + overflow:
        if r in overflow:
            why = f"passed everything but ranked below the top {cap}"
        else:
            why = "; ".join(PROMOTE[k](r["questions"][k], cfg) for k in r.get("borderline", []))
        extra = f" (red team: {r['downgraded_by_red_team']})" if r.get("downgraded_by_red_team") else ""
        out.append(f"- **{r['phrase']}**: borderline {', '.join(r.get('borderline', [])) or '—'}{extra}. "
                   f"Promote if: {why or 'red-team concern is resolved'}.")
    if not (by["watchlist"] or overflow):
        out.append("_None._")

    if by["needs_capture"]:
        out += ["", "## Needs a page capture (Q1/Q2/Q5 UNKNOWN)", ""]
        for r in by["needs_capture"]:
            out.append(f"- **{r['phrase']}** ({', '.join(r['unknown'])}): {r['action']}")
    if by["needs_judgment"]:
        out += ["", "## Needs judging (Q3/Q4 not yet judged)", ""]
        out += [f"- {r['phrase']}" for r in by["needs_judgment"]]

    out += ["", "## Rejected", ""]
    for r in by["reject"]:
        k = r["killed_by"][0]
        detail = r["questions"][k].get("detail", "") if k in r["questions"] else ""
        out.append(f"- {r['phrase']} — killed by {k.upper() if k in r['questions'] else k}"
                   + (f": {detail}" if detail else ""))
    if not by["reject"]:
        out.append("_None._")
    out += ["", "---", DISCLAIMER, ""]

    md = REPORTS / f"{today}-niche-report.md"
    md.write_text("\n".join(out), encoding="utf-8")

    fields = ["phrase", "seed", "buyer", "decision", "killed_by", "q1", "q2", "q3", "q4", "q5",
              "q1_selling", "q1_unknown", "q2_top3_share", "q2_small_shop_selling", "q2_min_shop_sales",
              "q2_unknown_sales", "q3_evidenced_flaws", "q4_product_class", "q4_recreation",
              "q5_p25", "q5_median", "q5_p75", "q5_unknown_prices", "q5_selling_at_floor",
              "top20_source", "confidence_q1_q2", "api_result_count", "calls_used", "collected_at"]
    with open(REPORTS / f"{today}-scores.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in scores:
            q = r["questions"]
            ev = _load(EVIDENCE / f"{r['slug']}.json")
            w.writerow({
                "phrase": r["phrase"], "seed": r.get("seed"), "buyer": r.get("buyer"),
                "decision": r["decision"], "killed_by": "|".join(r.get("killed_by", [])),
                **{k: r["verdicts"][k] for k in ("q1", "q2", "q3", "q4", "q5")},
                "q1_selling": q["q1"].get("value"), "q1_unknown": q["q1"].get("unknown_listings"),
                "q2_top3_share": q["q2"].get("top3_share"),
                "q2_small_shop_selling": len(q["q2"].get("small_shop_selling_listing_ids") or []),
                "q2_min_shop_sales": q["q2"].get("min_shop_sales"),
                "q2_unknown_sales": q["q2"].get("unknown_shop_sales"),
                "q3_evidenced_flaws": q["q3"].get("evidenced_flaws"),
                "q4_product_class": q["q4"].get("product_class"),
                "q4_recreation": q["q4"].get("recreation_verdict"),
                "q5_p25": q["q5"].get("p25"), "q5_median": q["q5"].get("median"),
                "q5_p75": q["q5"].get("p75"), "q5_unknown_prices": q["q5"].get("unknown_prices"),
                "q5_selling_at_floor": q["q5"].get("selling_at_or_above_floor"),
                "top20_source": r["top20_source"], "confidence_q1_q2": r["confidence_q1_q2"],
                "api_result_count": (ev.get("api_result_count") or {}).get("value"),
                "calls_used": ev.get("calls_used"), "collected_at": r["collected_at"],
            })
    print(f"wrote {md} and {today}-scores.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
