# NicheFinder

Personal, read-only market research tool for my own future Etsy shop. It takes broad product ideas and returns **which niches and products to build**, judged by a 5-question test. It stops at a ranked decision.

## What it does
- Expands broad seeds into buyer-specific long-tail search phrases (a Claude Code agent; I prune the list).
- For each phrase, reads public data via the **Etsy Open API v3** (API key only): active-listing search, listing details with shop totals, and listing reviews. Each phrase is capped at 45 calls, with 5 req/s and 5,000 req/day enforced locally.
- Optionally uses files I save myself: search page 1 saved from my own browser (`inputs/captures/<phrase-slug>/`) and eRank/EverBee CSV exports (`inputs/exports/`).
- Scores each phrase on five questions: proven demand, not monopolized, beatable, AI-proof, price ceiling ≥ $12. Thresholds are in `config.yaml`.
- A fresh-context "red team" tries to refute every shortlisted niche.
- Writes a private report: a shortlist (max 5) with a product spec, a watchlist, and a rejected list naming the question that killed each idea.

## What it does not do
- No OAuth, no access to any shop's private data, no buyer data
- No listing creation or edits, no SEO, mockups or designs
- No scraping of etsy.com, no headless browsers, proxies or CAPTCHA solving
- No resale or redistribution of Etsy data. Raw API responses are cached at most 6 h (listings) / 24 h (reviews), and all Etsy data stays local and git-ignored
- Single user (me), rate-limited well within Etsy's limits

Every number in a report carries its source (`api`, `page_capture`, `export`) and a timestamp. Missing data is `UNKNOWN`, never a pass. Etsy publishes no per-listing sales, so reviews, favourites and badges are reported as proxies.

## Setup
```
pip install -r requirements.txt
cp .env.example .env       # fill in ETSY_API_KEY and ETSY_SHARED_SECRET (sent as "keystring:shared_secret")
python -m pytest           # offline tests on synthetic fixtures
```
Live collection stays off until `ETSY_ANALYTICS_AUTHORISED=yes` is set in `.env`. See [docs/api-notes.md](docs/api-notes.md) for why.

## Run
In Claude Code, use the `niche-test` skill. Or run the steps by hand:
```
python -m src.phrases                 # validate data/phrases.csv
python -m src.collect --max-phrases 1 # Stage 1, prints calls used
python -m src.score                   # Stage 2 (Q3/Q4 come from the niche-judge agent)
python -m src.report                  # Stage 4 -> reports/<date>-niche-report.md
python -m src.purge                   # delete expired cached Etsy data
```

The term 'Etsy' is a trademark of Etsy, Inc. This Application uses Etsy's API, but is not endorsed or certified by Etsy.
