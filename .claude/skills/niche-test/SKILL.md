---
name: niche-test
description: Run the NicheFinder pipeline (stages 0-4) that takes broad Etsy product seeds and returns a ranked decision on which niches and products to build, judged by the 5-question test. Use when the user says "run the niche test", adds seeds, provides captures/exports, or asks for a niche report. Stops at the report: no listings, SEO, mockups or designs.
---

# niche-test

Follow `CLAUDE.md` rules throughout. Use the python on PATH, run from the repo root.

## Stage 0: phrases (pause for the user)
1. Read `inputs/seeds.txt`. If it's missing or empty, ask the user for seeds.
2. Launch the `phrase-expander` agent for the seeds that have no rows in `data/phrases.csv` yet.
3. Run `python -m src.phrases`. It must report no problems.
4. **Stop.** Show the user the phrases grouped by seed, with the path `data/phrases.csv`. Ask which to keep. Apply their pruning to the CSV. Don't start Stage 1 until they say so.

## Stage 1: collect (Python, not the LLM)
1. Preconditions. If any fails, tell the user and stop:
   - `.env` has `ETSY_API_KEY`, `ETSY_SHARED_SECRET` and `ETSY_ANALYTICS_AUTHORISED=yes` (Etsy's written OK for analytics use; see docs/api-notes.md).
   - Budget: phrases × 45 calls must fit within today's remaining 5,000-call window (`.cache/etsy/calls.log` holds the last 24 h). If it doesn't fit, ask which phrases to run first.
2. Screenshot captures: for each `inputs/captures/<slug>/` that has images but no `.html` or `capture.json`, read the screenshots in page order and write `capture.json`:
   `{"captured_at": "<ISO or file date>", "items": [{"listing_id": 123, "is_ad": false, "badges": ["bestseller"|"popular_now"|"in_carts"], "in_carts": 0}]}`.
   Get listing IDs only from what's visible (e.g. URLs). If IDs aren't visible, tell the user that an HTML save is needed instead. Never guess IDs.
3. For HTML captures, run `python -m src.parse_capture <slug>` and check that the ad and badge counts look plausible against the page. Etsy's markup is unverified, so if a capture parses to 0 cards or 0 ads, tell the user.
4. Run `python -m src.collect` (or `--phrase "..."`/`--max-phrases N`). Report the calls used and any notes it prints.

## Stage 2: score
1. Run `python -m src.score`. Numeric FAILs are final, so don't judge those phrases.
2. For every phrase with `decision: needs_judgment` in `data/scores.json`, launch `niche-judge` agents (up to ~5 slugs per agent, in parallel when there are many). Give each agent only the slugs.
3. Run `python -m src.score` again. If the scorer capped a judgment (check `questions.q3.problems`), mention it.

## Stage 3: red team
1. Run `python -m src.score --redteam-packets`.
2. For each slug it lists, launch a **fresh** `red-team` agent. Pass only the slug, never your own or the judge's reasoning.
3. Run `python -m src.score` again. Red-team downgrades are applied automatically.

## Stage 4: report, then stop
1. Run `python -m src.report`.
2. Show the user the path `reports/<date>-niche-report.md` and summarise the shortlist, the watchlist and the `needs_capture` list (with the exact page to save for each).
3. Stop. Don't write listings, SEO, mockups or designs.

## Ask the user only for
threshold changes, which phrases to keep, missing captures, budget overruns, and anything touching the API terms.
