# NicheFinder: rules for Claude

Purpose: take broad Etsy product seeds and return **which niches and products to build**, ranked by the 5-question test. Run it with the `niche-test` skill. Stop at the report. Out of scope: listings, SEO, mockups, designs, publishing.

## Non-negotiable
1. **Never invent a number.** Every metric is `{"value","source","at"}`, with source `api`, `page_capture` or `export:<file>`. Missing means `UNKNOWN`, never a pass.
2. **Estimates are labelled as estimates.** Etsy publishes no per-listing sales. Reviews, favourites and badges are proxies.
3. **No scraping of etsy.com and no bot-detection bypass.** No scrapers, headless crawlers, proxy rotation or CAPTCHA solvers, and don't fetch etsy.com pages. Live data comes only from the official Open API v3 (read-only, `src/etsy_client.py`) and the user's own files in `inputs/captures/` and `inputs/exports/`.
4. **Etsy API Terms** (docs/api-notes.md): raw cache ≤ 6 h for listings/search and ≤ 24 h for reviews. Request minimum data. Run no live analytics until `ETSY_ANALYTICS_AUTHORISED=yes`, i.e. Etsy approved it in writing. Etsy data is never redistributed.
5. **Field and endpoint names come from the official OpenAPI spec.** Re-check it before changing the client.

## Secrets and the public repo
- Keys live only in `.env`, which is git-ignored. The shared secret is as sensitive as the key. `data/`, `inputs/captures/`, `inputs/exports/`, `inputs/seeds.txt`, `reports/` and `.cache/` are never committed.
- Before every push, run `git status`, then scan staged changes:
  `git diff --cached | grep -nE "ETSY_(API_KEY|SHARED_SECRET)=.+"`, plus a grep for the actual key and secret values from `.env`. Any match → abort the push.
- If a key is ever pushed: stop and tell the user immediately. The fix is revoking it in the Etsy developer portal. Rewriting history is not enough.

## Layout
- `config.yaml`: thresholds, budget, cache TTLs. Thresholds are the user's call.
- `src/collect.py` (Stage 1), `src/parse_capture.py`, `src/score.py` (Stage 2 + decision rule + red-team packets), `src/report.py` (Stage 4), `src/purge.py`, `src/phrases.py`.
- Agents: `phrase-expander`, `niche-judge`, `red-team` (fresh context, packets + evidence only).
- Tests: `python -m pytest`. Fixtures in `tests/fixtures/` are synthetic.

## Decision rule
Any FAIL → reject. UNKNOWN on Q1/Q2/Q5 → `needs_capture` (name the exact page to save). All PASS → shortlist. ≤1 BORDERLINE → watchlist. 2+ BORDERLINE → `decision.too_many_borderline` (default reject).

Ask the user only about thresholds, phrase pruning, missing captures and anything touching the API terms.
