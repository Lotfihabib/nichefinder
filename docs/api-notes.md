# Etsy Open API v3 — notes for NicheFinder

Verified 2026-10-16 against:

- OpenAPI spec `https://www.etsy.com/openapi/generated/oas/3.0.0.json` (spec `info.version` 3.0.0, `openapi` 3.0.2)
- `https://developers.etsy.com/documentation/essentials/authentication`
- `https://developers.etsy.com/documentation/essentials/rate-limits`
- API Terms of Use `https://www.etsy.com/legal/api` (page says "Last updated on 18 Aug, 2026")

Re-check these before changing `src/etsy_client.py`. Field names below were copied from the spec, not from memory.

## Authentication

- Spec `components.securitySchemes.api_key`: header `x-api-key`, value **`keystring:shared_secret`**. Every request needs it.
- Read-only public endpoints below need no OAuth token.
- 403 → check the header format first, then whether the app is still pending approval.

## Endpoints used (all `GET`, base `https://api.etsy.com`)

| Purpose | operationId | Path | Params we send |
|---|---|---|---|
| Keyword search | `findAllListingsActive` | `/v3/application/listings/active` | `keywords`, `limit` (1–100), `offset`, `sort_on=score`, `currency=USD` |
| Listing details + shop | `getListingsByListingIds` | `/v3/application/listings/batch` | `listing_ids` (≤100 per call), `includes=Shop`, `currency=USD` |
| Listing reviews | `getReviewsByListing` | `/v3/application/listings/{listing_id}/reviews` | `limit` (1–100), `offset`, `min_created` (epoch s) |

Not used: `getShop` (`/v3/application/shops/{shop_id}`) — the `Shop` association on the batch call returns the same `Shop` schema, which saves ~20 calls per phrase.

Notes from the spec:

- `findAllListingsActive` defaults to newest-first by creation date. `sort_on=score` (relevance) only works with a search option such as `keywords`, and is always descending.
- `keywords`: "Search term or phrase that must appear in all results."
- **API result order is not the ranked page 1 a buyer sees.** Etsy's search page mixes in ads, personalisation and its own ranking. Without a page capture, Q1/Q2 carry `confidence=low`.

## Fields used

`ShopListing` / `ShopListingWithAssociations`:

| Field | Use |
|---|---|
| `listing_id`, `shop_id` | identity |
| `title`, `tags` | product context for the judge (kept only in evidence files, purged per retention) |
| `price` (`Money`: `amount`, `divisor`, `currency_code`) | native price; spec says it's the **minimum** possible price |
| `converted_price` (`Money`, nullable) | price in `currency=USD`; null if no rate → price `UNKNOWN` unless native is already USD |
| `num_favorers` | favourites (proxy) |
| `created_timestamp`, `original_creation_timestamp` | listing age |
| `listing_type` (`physical` / `download` / `both`) | digital flag (there is **no** `is_digital` field) |
| `shop` (`Shop`, nullable) | from `includes=Shop` |

`Shop`:

| Field | Use |
|---|---|
| `transaction_sold_count` | total shop sales (Q2) |
| `review_count` | reviews **in the past year** (spec wording) |
| `review_average` | average rating, past year |
| `create_date` / `created_timestamp` | shop age |
| `shop_name`, `user_id`, `listing_active_count` | red-team "sister shop" checks |

`ListingReview`: `listing_id`, `rating` (1–5), `review` (nullable text), `created_timestamp`.
`ListingReviews` / `ShopListings`: `count`, `results`.

Recent demand (Q1) = `count` of `getReviewsByListing` with `min_created = now − 90 days`. `collect.py` cross-checks `count` against the rows it pages through and records `count_source`.

## Call budget per phrase

| Call | Count |
|---|---|
| search (limit 100) | 1 |
| batch details + shop (20 ids) | 1 |
| recent reviews, one per top-20 listing | 20 |
| older review pages for 1–3★ text | up to the remaining cap |
| **cap** (`config.yaml: budget.calls_per_phrase`) | **45** |

1–3★ text coverage is partial for listings with many reviews. The evidence file records `reviews_scanned` and `review_total` per listing.

## Rate limits

- Per API key: queries per second (QPS) and queries per day (QPD). QPS is checked first. QPD is a **sliding 24 h window**.
- Success headers: `x-limit-per-second`, `x-remaining-this-second`, `x-limit-per-day`, `x-remaining-today`.
- Over the limit → HTTP 429 with `retry-after` (seconds). Etsy recommends exponential backoff.
- Our defaults (`config.yaml`): 5 QPS and 5,000 per rolling 24 h until approved for more. `etsy_client.py` enforces both locally. It persists a call log for the sliding window and also stops when `x-remaining-today` drops below a safety margin.

## Terms of Use: clauses that shape the design

1. **Display freshness (§5 "Display of Data").** Listing content must not be displayed more than **6 h** older than on Etsy. Other Etsy content can be at most **24 h** old. You "will not cache or store it longer than is reasonably necessary".
   → Raw response cache TTL: 6 h for search and listing calls, 24 h for reviews. `python -m src.purge` deletes expired cache and evidence older than `retention.evidence_days`. Reports are dated snapshots that carry `collected_at`. Re-run rather than reuse old numbers.
2. **Minimum data (§5).** You must not "request more than the minimum amount of data needed".
   → Only the three endpoints above, and only `includes=Shop`. No images, descriptions, users or inventory.
3. **No scraping or automated analysis without written authorisation (§5 Prohibited Behaviour).** Two bullets matter:
   - no "automated systems or browser extensions to access, analyse, or scrape the Etsy Site, the Etsy API or any Etsy data … unless expressly authorised in writing by Etsy"
   - no using the API "to collect, scan, or otherwise request Etsy content for purposes of analytics … unless expressly authorised in writing by Etsy"

   → **Open compliance question.** NicheFinder's purpose is market analysis for the owner's own shop. Before running live collection, get Etsy's written approval of that Application Purpose (§3). The app approval email may cover it, or email developer@etsy.com. `collect.py` refuses live calls unless `.env` sets `ETSY_ANALYTICS_AUTHORISED=yes`, as a deliberate speed bump.
4. **Trademark notice (§1).** The app must show: "The term 'Etsy' is a trademark of Etsy, Inc. This Application uses Etsy's API, but is not endorsed or certified by Etsy." It appears in the README and at the foot of every report.
5. **Credentials (§2).** One key per application. Never create extra keys to get around limits. Report a compromised key to security@etsy.com.
6. **No redistribution / transfer of Etsy content (§5).** Raw responses, evidence and reports stay local and are git-ignored.
7. **Dormancy (§3).** An app with no successful call in 6 months may be suspended.

## Third-party repos reviewed

| Repo | Pinned commit | Licence | Decision |
|---|---|---|---|
| administrativetrick/etsy-mcp-server | `3bc937713c92ae297bdfd23f0769fc12a2691783` | MIT | `src/index.ts` sends `x-api-key: process.env.ETSY_API_KEY` verbatim. To use it, set its `ETSY_API_KEY` to the **combined** `keystring:shared_secret`. Not registered yet; see README. Its calls bypass our rate budget, so keep usage ad hoc. |
| nichescope/nichescope | `cda94e2cfb3f1280fc189f44efa0ff04d2be5677` | none stated | Ideas only, no code copied. |
| brainbytes-dev/etsy-oracle | `4057207f8b1468529e96715a75d0348b112dd9a6` | AGPL-3.0 | Not used, no code copied. |
| lana-info/selloracle-mcp | — | closed | Optional cross-check only; its score is never a verdict. |
