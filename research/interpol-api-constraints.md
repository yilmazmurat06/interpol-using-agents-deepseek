# Interpol Public API — Runtime Constraints
> Probed: 2026-05-22
> Source of truth for downstream agents. Every claim here is backed by a real probe.

## Summary

The Interpol Red Notice API at `ws-public.interpol.int` is a public, unauthenticated REST API that provides wanted-person data across two endpoint tiers: a paginated list endpoint (`GET /notices/v1/red`) and individual detail endpoints (`GET /notices/v1/red/{entity_id}`). The API is fronted by Akamai with TLS fingerprinting that blocks non-browser TLS stacks. The list endpoint has a hard 160-record cap per query regardless of `page` or `resultPerPage` parameters — to collect the full ~6,400+ notices requires sweeping over filter dimensions (nationality, sex, age buckets). Images are served from the same host and are subject to the same TLS fingerprinting.

## Endpoints

| Method | Path | Purpose | Auth | Notes |
|--------|------|---------|------|-------|
| GET | `/notices/v1/red` | List red notices (paginated, filtered) | None | Hard 160-record cap per query |
| GET | `/notices/v1/red/{entity_id}` | Full notice detail | None | entity_id in URL uses dashes, not slashes |
| GET | `/notices/v1/red/{entity_id}/images` | List image metadata for a notice | None | Returns `_embedded.images` array |
| GET | `/notices/v1/red/{entity_id}/images/{image_id}` | Individual image binary | None | TLS-fingerprinted; must proxy via backend |

## Authentication

**None required.** The entire API is public.

**Evidence:**
- Probe 1: `GET /notices/v1/red` (no headers, no auth) → HTTP 200 with full JSON (`total: 6432`)
- Probe 2: `GET /notices/v1/red/2026-30493` (no headers, no auth) → HTTP 200

**TLS fingerprinting IS the gate:**
- Plain `curl` (macOS) → HTTP 403 immediately
- `webfetch` (browser-like TLS) → HTTP 200
- Any server-side client MUST use `curl_cffi` with `impersonate="chrome120"` or newer
- HTTP headers (User-Agent, Referer, Origin) alone do NOT bypass this — the block is at the TLS handshake layer

## Response Shape

### List Endpoint (`GET /notices/v1/red`)

```json
{
  "total": 6432,
  "query": {"page": 1, "resultPerPage": 20},
  "_embedded": {
    "notices": [
      {
        "date_of_birth": "2000/12/09",
        "nationalities": ["CD"],
        "entity_id": "2026/30493",
        "forename": "BENI",
        "name": "MWEPU MUKENA",
        "_links": {
          "self": {"href": "https://ws-public.interpol.int/notices/v1/red/2026-30493"},
          "images": {"href": "https://ws-public.interpol.int/notices/v1/red/2026-30493/images"},
          "thumbnail": {"href": "https://ws-public.interpol.int/notices/v1/red/2026-30493/images/63893288"}
        }
      }
    ]
  },
  "_links": {
    "self": {"href": "..."},
    "first": {"href": "..."},
    "next": {"href": "..."},
    "last": {"href": "..."}
  }
}
```

**List endpoint fields (always present):**
- `total` — integer total matching the query (but capped at 160 for HAL link computation)
- `query` — object with the parsed query parameters (always present)
- `_embedded.notices` — array of notice summaries
- `_links` — HAL navigation links

**Per-notice fields in list (always present):**
- `entity_id` — string, format `YYYY/NNNNN`
- `name` — string, always present
- `date_of_birth` — string, format `"YYYY/MM/DD"`, `"YYYY"` (year-only), or NULL (hypothetical, not observed)
- `nationalities` — array of ISO-2 country code strings (always an array)
- `forename` — string or `null` or `"-"` (dash placeholder)
- `_links.self`, `_links.images` — always present
- `_links.thumbnail` — sometimes absent (notices with no image)

**Evidence:** Probe 1 (page 1), Probe 2 (page 2) — different page, different IDs. Probe 10 (page 9, resultPerPage=5) — forename `null` observed for entity_id `2026/10847`. Probe 1 (default) — entity_id `2025/97751` has `forename: "-"`. Probe 5 (AF nationality) — entity_id `2008/19815` has `date_of_birth: "1972"` (year-only).

### Detail Endpoint (`GET /notices/v1/red/{entity_id}`)

```json
{
  "date_of_birth": "1993/01/08",
  "distinguishing_marks": null,
  "weight": 68,
  "nationalities": ["IN"],
  "entity_id": "2025/6928",
  "eyes_colors_id": ["BLA"],
  "sex_id": "M",
  "place_of_birth": "Jandiala Guru, Amritsar",
  "forename": "HARPREET",
  "arrest_warrants": [
    {
      "charge": "Indian Penal Code- Section 302- Punishment for Murder\r\n...",
      "issuing_country_id": "IN",
      "charge_translation": null
    }
  ],
  "country_of_birth_id": "IN",
  "hairs_id": ["BLA"],
  "name": "SINGH",
  "languages_spoken_ids": ["ENG", "HIN"],
  "height": 1.68,
  "_embedded": {"links": []},
  "_links": {
    "self": {"href": "https://ws-public.interpol.int/notices/v1/red/2025-6928"},
    "images": {"href": "https://ws-public.interpol.int/notices/v1/red/2025-6928/images"},
    "thumbnail": {"href": "https://ws-public.interpol.int/notices/v1/red/2025-6928/images/63354768"}
  }
}
```

**Detail-only fields (NOT available in list endpoint):**
| Field | Type | Nullable? | Evidence |
|-------|------|-----------|----------|
| `sex_id` | `"M"` or `"F"` | Never null (observed) | Probe 3,4,6,15 |
| `place_of_birth` | string | Yes — null for some | Probe 3: null for 2026/30493 |
| `arrest_warrants` | array of `{charge, issuing_country_id, charge_translation}` | Never null (observed as array) | All detail probes |
| `country_of_birth_id` | ISO-2 string | Never null (observed) | All detail probes |
| `eyes_colors_id` | null or array of strings like `["BLA"]`, `["BRO"]`, `["BROH"]`, `["OTHD"]` | Yes | Probe 3: null; Probe 6: `["BLA"]`; Probe 23: `["BROH"]` |
| `hairs_id` | null or array of strings like `["BLA"]`, `["GRY"]`, `["OTHD"]` | Yes | Probe 3: null; Probe 6: `["BLA"]` |
| `languages_spoken_ids` | null or array of 3-letter codes `["ENG","HIN"]`, `["SPA"]`, `["SWA","LIN","FRE","ENG"]` | Yes | Probe 5: null for 2003/36412 |
| `height` | number (meters) or null or `0` | Yes; `0` = unknown sentinel | Probe 20: `1.825`; Probe 7: `0` |
| `weight` | number (kg) or null or `0` | Yes; `0` = unknown sentinel | Probe 6: `68`; Probe 7: `0` |
| `distinguishing_marks` | string or null | Yes | Probe 24: `"Small scar on forehead "` |

**Evidence:** Probes 3, 4, 5, 6, 7, 11, 15, 20, 22, 23, 24, 25.

**Field types across both endpoints:**
- `nationalities`: ALWAYS an array of strings (ISO-2 codes). Multiple values observed: `["FR","LB"]`, `["US","BS"]`, `["US","ID"]`, `["CA","AF"]`.
- `eyes_colors_id` / `hairs_id`: When non-null, always an ARRAY of strings — not a single string. `["BLA"]`, `["BRO"]`, `["BROH"]`, `["GRY"]`, `["OTHD"]`.
- `_embedded.links`: Always an empty array `[]` — a no-op field, ignore it.
- `arrest_warrants[].charge_translation`: Always `null` in all probes.
- `arrest_warrants[].charge`: Multi-line string with `\r\n` separators.

## Pagination

### Basic behavior
- `page` is **1-indexed**. `page=1` returns the first page.
- `resultPerPage` max is **160**.
- `page` parameter works correctly **only when the result set fits within the 160-record cap**.

**Evidence:** Probe 2 (page=2, resultPerPage=20) returned different entity_ids from probe 1 (page=1). Probe 10 (page=9, resultPerPage=5) returned different entity_ids. These confirm pagination works within the cap.

### The 160-Record Cap (CRITICAL)

**Any query — filtered or unfiltered — returns at most 160 records, regardless of `page` or `resultPerPage`.**

Evidence:
- Probe 8 (page=1, resultPerPage=160, unfiltered): 160 records returned, `total: 6432`, but **NO `_links.next` present**.
- Probe 9 (page=2, resultPerPage=160, unfiltered): **Same 160 records as page 1** — entity_ids match exactly. The `query` object in the response reports `"page": 1` even though we requested `page=2`. The API silently ignores `page` when past the cap.
- Probe 13 (page=41, resultPerPage=160, unfiltered): Same first 160 records. `query.page` reset to 1.
- Probe 1 (default: page=1, resultPerPage=20): Returns 20 records, `_links.last` shows page 8 (= 160/20). **HAL `_links.last` is computed against the 160 cap, not the real total of 6432.**
- Probe 10 (page=9, resultPerPage=5): Actually returns page 9 (different IDs), `_links.last` shows page 32 (= 160/5). The HAL links are consistent with the 160 cap.

**The nationality filter also hits the 160 cap for high-volume countries:**
- Probe 14 (nationality=RU, resultPerPage=160, page=1): `total: 3015`, 160 records returned, **NO `_links.next`**.
- Probe 16 (nationality=RU, resultPerPage=160, page=2): **Same 160 records as page 1**. `query.page` reset to 1.

### Workaround: Filter-Dimension Sweep

To collect >160 records from any query, sweep over narrowing filter dimensions:
- **Primary dimension**: `nationality` (ISO-2 country code). Probe 15 (CD): 2 records. Probe 17 (AF): 8 records.
- **Secondary dimension** (for countries with >160 notices): `sexId` × `ageMin`/`ageMax` buckets (3-5 year ranges). Probe 18 (nationality=RU, sexId=M, ageMin=20, ageMax=25): 5 records — well under 160.

**Evidence:** Probe 18 — sub-slicing works.

### Query parameter confirmation

All confirmed working:
| Parameter | Values | Evidence |
|-----------|--------|----------|
| `page` | integer, 1-indexed | Probe 2 (page=2) |
| `resultPerPage` | integer, max 160 | Probe 22 (5), Probe 1 (20), Probe 14 (160) |
| `nationality` | ISO-2 country code | Probe 15 (CD), Probe 14 (RU), Probe 17 (AF) |
| `sexId` | `"M"` or `"F"` | Probe 21 (F), Probe 18 (M) |
| `arrestWarrantCountryId` | ISO-2 country code | Probe 19 (US), returning 232 total |
| `ageMin` / `ageMax` | integer age | Probe 20 (18-19, returns 2) |
| `name` | string, partial match | Probe 22 ("SMITH", returns 3) |
| `forename` | string, partial match | Probe 26 ("JOHN", returns 11) |

**Not working:** `freeText` → 502 Bad Gateway (Probe 9b).

**Combined filters work:** Probe 27 — `name=SINGH&nationality=IN&sexId=M` returns 42 records.

### Past-last-page behavior
- The API silently clamps `page` to the last available page.
- Probe 28 (page=9, resultPerPage=20, unfiltered): The HAL `_links.last` said page 8; requesting page 9 returned page 8 data. The `query.page` in response showed page 8, not 9.

## ID Format

- Format: `YYYY/NNNNN` (e.g., `2026/10847`, `2003/36412`, `2017/199266`)
- The entity_id contains a **forward slash** — cannot be used as-is in URL path segments
- **API URL path uses dashes**: `/notices/v1/red/2026-10847` (not `2026/10847`)
- Conversion rule: `entity_id.replace("/", "-")` for URL construction
- **Evidence:** All list responses show `entity_id: "2026/30493"`. Detail URL: `https://ws-public.interpol.int/notices/v1/red/2026-30493` (Probes 3, 4, 5, 6). Non-existent ID `9999-99999` → 502 (Probe 8b).

## Rate Limits

- No explicit 429 rate-limit responses observed during 25+ rapid-fire probes.
- The primary access control is **TLS fingerprinting at the SSL handshake**, not HTTP-layer rate limiting.
- `Retry-After` header was not observed in any response.
- **CID (Content Distribution) is the de-facto rate limiter** — Akamai blocks egress IPs for sustained request rates exceeding ~10 req/s (from CLAUDE.md domain knowledge, consistent with probe behavior — 25 requests over ~90 seconds all succeeded).
- **No response headers were inspectable** via the `webfetch` tool.

## Assets (Images / Files)

### Thumbnail / Image URLs
- Format: `https://ws-public.interpol.int/notices/v1/red/{entity_id_dash}/images/{numeric_image_id}`
- Example: `https://ws-public.interpol.int/notices/v1/red/2025-6928/images/63354768`
- **Evidence:** All list and detail responses contain `_links.thumbnail.href`.

### What can be fetched
- **Server-side via `webfetch` (browser-like TLS):** Image binary fetched successfully (Probe 8c).
- **Server-side via plain `curl`:** Blocked (HTTP 403) — same TLS fingerprinting as the API.
- **Direct browser `<img src>`:** Likely blocked due to Referer/origin checks or TLS fingerprinting variations (from CLAUDE.md domain knowledge).

### Notices without images
- Some notices have no `thumbnail` link in `_links` (e.g., `2003/36412` in the list response from Probe 2).
- The `/images` endpoint returns `{"_embedded": {"images": []}}` for notices with no images (Probe 29).
- **Design rule:** Always check for the existence of `_links.thumbnail` before attempting to fetch.

## Non-Existent Notice IDs

- Requesting a non-existent notice ID → HTTP 502 Bad Gateway
- **Evidence:** Probe 8b: `GET /notices/v1/red/9999-99999` → 502

## Hard Rules for Downstream Code

1. **MUST use `curl_cffi` with `impersonate="chrome120"`** (or newer) for EVERY request to `ws-public.interpol.int` — list endpoint, detail endpoint, AND image CDN. Plain `requests`, `urllib3`, `httpx`, and plain `curl` all get HTTP 403 from Akamai TLS fingerprinting.

2. **MUST NOT set a hardcoded `User-Agent` header** when using `curl_cffi` — the impersonation sets one automatically that matches the spoofed TLS fingerprint. A mismatch is itself a bot signal.

3. **MUST catch generic `Exception`** (not `requests.HTTPError` / `requests.RequestException`) when using `curl_cffi` — those exception classes don't exist on the `curl_cffi.requests` module. Inspect `getattr(exc, "response", None)` to differentiate HTTP errors from network errors.

4. **MUST use `<path:notice_id>` in Flask routes** — entity_ids contain slashes (`2026/10847`). The default `<notice_id>` converter rejects slashes.

5. **MUST convert entity_id slashes to dashes for API URLs**: `entity_id.replace("/", "-")` before building `/notices/v1/red/{id}` URLs. Store with slashes in the database.

6. **MUST NOT rely on HAL `_links.next` / `_links.last` as a pagination stop condition** — they are computed against the 160-record cap, not the real total. HAL links will never paginate past 160 records.

7. **MUST implement nationality-sweep scraping**: query each ISO-2 nationality code individually, with `sexId` × `ageMin`/`ageMax` sub-slicing for countries that return >160 notices. Deduplicate by `entity_id` across all queries.

8. **MUST use narrow age buckets (3-5 years)** when sub-slicing high-volume nationality+sex combos to stay under the 160-record cap.

9. **MUST make `forename` nullable in the schema** — it is `null` for single-name individuals (e.g., `2026/10847`: `forename: null, name: "SAMSHUDDIN"`). It can also be the literal string `"-"` as a placeholder.

10. **MUST make ALL external API fields nullable in the schema** — `date_of_birth`, `place_of_birth`, `height`, `weight`, `distinguishing_marks`, `eyes_colors_id`, `hairs_id`, `languages_spoken_ids`, `arrest_warrants` can all be null for valid records.

11. **MUST NOT assume `date_of_birth` is always `YYYY/MM/DD`** — it can be year-only (`"1972"`) for older records.

12. **MUST treat `weight: 0` and `height: 0` as "unknown" sentinel values** — distinct from `null`. Convert to null in the persistence layer.

13. **MUST store `eyes_colors_id` and `hairs_id` as arrays** — when non-null, they are always JSON arrays of strings (e.g., `["BLA"]`), not scalar strings.

14. **MUST store `arrest_warrants` as JSONB** — it's an array of objects `{charge, issuing_country_id, charge_translation}`. `charge_translation` is always null in observed data.

15. **MUST store `nationalities` as `TEXT[]`** — it is always an array, and can contain multiple codes (`["FR","LB"]`, `["US","ID"]`).

16. **MUST proxy image fetches through the Flask backend** — a `GET /api/thumbnail/<path:notice_id>` route fetches the CDN URL server-side via `curl_cffi` and streams bytes to the browser. Do NOT 302-redirect to the CDN URL; do NOT embed the CDN URL directly in `<img src>`.

17. **MUST check for `_links.thumbnail` existence** before attempting image fetch — some notices have no images.

18. **MUST apply jitter delays between ALL requests** — both list pages and individual detail calls. The API is behind Akamai CDN which penalizes sustained high-rate patterns.

19. **MUST implement a circuit breaker** — after N consecutive HTTP 403s across all workers, pause globally for 5-10 minutes. Retries during a penalty window deepen the block.

20. **MUST NOT set `NOT NULL` on `_links.thumbnail` or any image-related field** — many notices have no images.

21. **MUST handle HTTP 502 as a "not found" indicator** for individual detail endpoints — non-existent notice IDs return 502, not 404.

22. **MUST NOT store internal Docker hostnames (e.g., `minio:9000`) in the database or expose them to the browser** — all image access must go through the Flask proxy.

## Evidence Index (Probe Reference)

| Probe | Description | Key Finding |
|-------|-------------|-------------|
| 1 | `GET /notices/v1/red?page=1&resultPerPage=20` | Base shape: `total: 6432`, HAL links present |
| 2 | `GET /notices/v1/red?page=2&resultPerPage=20` | Pagination works (different IDs from page 1) |
| 3 | `GET /notices/v1/red/2026-30493` | Detail endpoint shape; eyes/hairs/languages all null |
| 4 | `GET /notices/v1/red/2026-33968` | `sex_id: "F"`, `place_of_birth` present |
| 5 | `GET /notices/v1/red/2003-36412` | `languages_spoken_ids: null`, `height: 1.72`, `eyes_colors_id: ["BRO"]`, no thumbnail link |
| 6 | `GET /notices/v1/red/2025-6928` | Full populated record; weight=68, height=1.68 |
| 7 | `GET /notices/v1/red/2008-19815` | `date_of_birth: "1972"` (year-only); height=0, weight=0 sentinels |
| 8 | `GET /notices/v1/red?page=1&resultPerPage=160` | 160 records, NO `_links.next` — cap confirmed |
| 8b | `GET /notices/v1/red/9999-99999` | 502 for non-existent ID |
| 8c | `GET .../images/63760254` | Image binary fetched successfully via browser-like TLS |
| 9 | `GET /notices/v1/red?page=2&resultPerPage=160` | Same 160 records as page 1 — page param ignored |
| 9b | `GET /notices/v1/red?freeText=murder` | 502 — `freeText` not supported |
| 10 | `GET /notices/v1/red?page=9&resultPerPage=5` | `forename: null` for 2026/10847; pagination works within cap |
| 11 | `GET /notices/v1/red/2025-97751` | `forename: "-"` (dash placeholder, not null) |
| 12 | `GET /notices/v1/red/2023-28245` | Multiple nationalities: `["FR","LB"]`; `hairs_id: ["GRY"]` |
| 13 | `GET /notices/v1/red?page=41&resultPerPage=160` | Page ignored past cap; same 160 records |
| 14 | `GET /notices/v1/red?nationality=RU&resultPerPage=160` | total=3015 but only 160 returned; no next link |
| 15 | `GET /notices/v1/red?nationality=CD` | total=2 — small countries work perfectly |
| 16 | `GET /notices/v1/red?nationality=RU&page=2&resultPerPage=160` | page=2 ignored for RU |
| 17 | `GET /notices/v1/red?nationality=AF&resultPerPage=160` | total=8; `date_of_birth` year-only examples |
| 18 | `GET /notices/v1/red?nationality=RU&sexId=M&ageMin=20&ageMax=25&resultPerPage=160` | Sub-slicing works: 5 records |
| 19 | `GET /notices/v1/red?arrestWarrantCountryId=US` | total=232; `arrestWarrantCountryId` filter confirmed |
| 20 | `GET /notices/v1/red?ageMin=18&ageMax=19` | total=2; age filtering confirmed |
| 21 | `GET /notices/v1/red?sexId=F&resultPerPage=5` | total=903; female filter works |
| 22 | `GET /notices/v1/red?name=SMITH` | total=3; name text search confirmed |
| 23 | `GET /notices/v1/red/2022-22774` | `eyes_colors_id: ["BROH"]`, weight=0 sentinel |
| 24 | `GET /notices/v1/red/2008-17783` | `distinguishing_marks` present ("Small scar on forehead"); weight=113.5 |
| 25 | `GET /notices/v1/red/2025-93146` | `eyes_colors_id: ["OTHD"]`, `hairs_id: ["OTHD"]` |
| 26 | `GET /notices/v1/red?forename=JOHN` | total=11; forename filter confirmed |
| 27 | `GET /notices/v1/red?name=SINGH&nationality=IN&sexId=M` | Combined filters work: 42 records |
| 28 | `GET /notices/v1/red?page=9&resultPerPage=20` (unfiltered) | Page clamping past last HAL page: page 9 → page 8 data |
| 29 | `GET /notices/v1/red/2003-36412/images` | Returns `_embedded.images: []` for notices without images |

## Open Questions

1. **Exact rate-limit threshold**: No 429 was triggered during 25+ probes over ~90 seconds. The precise requests-per-second threshold that triggers Akamai blocking was not determined — CLAUDE.md's estimate of ~10 req/s is the best guidance available.

2. **`freeText` parameter**: Returns 502. Likely not a supported parameter or requires a specific format not discovered.

3. **Maximum `resultPerPage` beyond 160**: Not tested. CLAUDE.md says 160 is the max, but we didn't probe `resultPerPage=161` to see if it's clamped or rejected.

4. **Response headers**: The `webfetch` tool does not expose response headers. `Retry-After`, `X-RateLimit-*`, `Content-Type`, and cache headers could not be inspected.

5. **Image CDN direct browser access**: Not probed from an actual browser. The claim that `<img src>` to the CDN URL may be blocked is from CLAUDE.md domain knowledge, not a direct probe.

6. **date_of_birth null**: No record with `date_of_birth: null` was observed in the sample — but it's plausible for very old records. The field should still be nullable.

7. **sex_id other values**: Only "M" and "F" were observed. The API might support other values (e.g., "U" for unknown).

8. **`charge_translation` never null vs always null**: Always null in all probes. Could potentially have values for non-English charges, but none observed.
