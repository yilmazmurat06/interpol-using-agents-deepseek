---
name: web-scraping-skills
description: 'Use this skill when the user is planning, teaching, or debugging a web scraping or data extraction workflow, including choosing HTTP vs headless browsing, mapping selectors, handling headers/cookies, dealing with anti-bot/WAF issues, pagination patterns, token reduction, stop conditions, incremental runs, and structured output validation.'
argument-hint: 'Describe the scraper scenario and target constraints (stack, site type, auth, scale).'
user-invocable: true
---

# Web Scraping Skills

## What This Skill Does
- Turns a scraping goal into a concrete, ordered checklist of decisions and validation steps.
- Diagnoses why a scraper fails (content missing, blocked, wrong pagination, token overrun, unstable selectors).
- Selects the right tooling and architecture based on actual site behavior — not assumptions.

---

## Core Principle: Token Waste Is the Structural Problem

Most scrapers fail not because they can't fetch a page, but because they fetch too much of it. A typical news article converted to plain text runs 8,000–15,000 tokens. Multiply by 10 pages and you've consumed 100,000+ tokens — mostly on navigation bars, footers, cookie banners, and related article previews.

Stuffing irrelevant content into the context also degrades model reasoning. The signal is buried in noise.

**The fix is a smarter scraper, not a faster one.** Every decision below is oriented around getting the right data into the model with the fewest tokens.

---

## Pre-Implementation Checklist (complete BEFORE writing any code)

- [ ] **Define your output schema first.** What exact fields does the agent need? In what shape? (See Step 1)
- [ ] Open DevTools → Network → XHR/Fetch and reload the page. Is there a hidden JSON API?
  - If yes: use that API directly — almost always simpler than HTML parsing.
- [ ] Does the API require authentication? (cookie, Bearer token, OAuth, session)
- [ ] What HTTP method does the endpoint use? (GET, POST with body, GraphQL)
- [ ] What does the first response look like? (JSON structure, top-level keys, total count)
- [ ] How does pagination work? (see Pagination Patterns section)
- [ ] Do record IDs contain special characters? (slashes, Unicode, spaces)
  - Slashes in URL path segments require `<path:var>` in Flask, `**` in Express, etc.
- [ ] Are images or assets loaded from a CDN? Can they be fetched server-side?
  - If the CDN uses TLS fingerprinting, `requests` will get 403 even with browser headers.
- [ ] What is the expected total record count vs what a single page returns?
- [ ] Does the target apply rate limiting? What is the safe request rate?
- [ ] What percentage of failed extractions is acceptable? (define the error budget upfront)

---

## Step 1 — Define Output Schema Before Writing Code

**This is the most important step.** Treat scraping as an output problem, not an input problem.

Start with the exact JSON structure the agent needs. Every subsequent decision — what to extract, how to prompt the model, what to validate — follows from this schema.

```json
{
  "product_name": "string or null",
  "price": "number or null",
  "currency": "string or null",
  "in_stock": "boolean or null",
  "scraped_at": "ISO 8601 timestamp"
}
```

Schema-first reduces tokens because:
1. Extraction logic only looks for specific elements (price tag, stock indicator) — not the whole page.
2. The model prompt is constrained: "fill in these five fields" rather than "summarize this page."
3. Retries target only the failed field, not the full page re-fetch.
4. Constrained tasks produce more reliable, less hallucinated results.

---

## Step 2 — Identify the Data Source

Always check for a hidden API before writing an HTML scraper:

1. Open the target page in Chrome.
2. DevTools → Network → filter by `Fetch/XHR`.
3. Reload and interact (scroll, click "Load More", change page).
4. Look for JSON responses containing the data you need.

**If a JSON API exists:** use it. Skip HTML parsing entirely.  
**If the page is static HTML:** use `requests` + `BeautifulSoup` / `lxml`.  
**If content is JS-rendered with no usable API:** use Playwright or Puppeteer.

---

## Step 3 — Reduce Tokens at the Extraction Layer

Strip noise **before** content reaches the LLM. This is the extraction layer.

**Strip from HTML:**
- `<script>` and `<style>` tags
- Navigation menus (`<nav>`, `<header>`, `<footer>`)
- Cookie consent banners and sidebars
- Social sharing widgets and comment sections

**Use selector-based extraction:** Instead of converting the whole DOM to text, extract only the relevant container:

```javascript
// Instead of this
const fullPageText = convertToMarkdown(html);

// Do this
const priceSection = document.querySelector('.product-price-container');
const priceText = priceSection ? priceSection.innerText : '';
```

The difference in token consumption can be 10x.

**For article content:** Run through a Readability-style parser (Mozilla's Readability, used in Firefox Reader Mode). Open-source ports exist for Python and Node.js. Typically cuts token count by 60–80% for article pages by stripping everything except the main article body.

**Truncate, don't summarize:** If a page is genuinely long, set a hard token budget (e.g., 3,000 tokens) and cut at a clean sentence boundary. Log what was truncated. Summarization is expensive; truncation is free. Use truncation unless summarization is actually required by the task.

---

## Step 4 — Understand Pagination

Pagination is the most common source of scraping bugs. Identify the pattern before coding:

| Pattern | How to detect | How to paginate |
|---------|--------------|-----------------|
| `?page=N` (1-based) | URL or request param | Increment from 1; stop when empty or `page > total_pages` |
| `?page=N` (0-based) | `page=0` returns first results | Increment from 0 |
| `?offset=N&limit=M` | Numeric offset in params | Add `limit` each step; stop when `results < limit` |
| Cursor / `forwardKey` | Token in response body | Extract token and pass as next request param |
| HAL `_links.next` | `_links.next.href` in JSON | Follow URL; stop when `_links.next` is absent |
| Filter-based (no real pagination) | Same results regardless of `page` value | Sweep over filter dimensions (see below) |
| Infinite scroll | JS scroll event, no URL change | Playwright: scroll to bottom, wait for network idle |

**Warning — API ignores `page` parameter:** Some APIs always return the same result set regardless of `page`. Detect this by checking if page 1 and page 2 return identical IDs. Fix: use filter-based sweeping — query per country code, per category, per date range — and deduplicate with a `seen` set across all queries.

**Warning — 0 vs 1 indexing:** Verify whether the API uses `page=0` or `page=1` as the first page. `page=0` often returns empty results on 1-based APIs.

**Warning — Short-batch stop condition fails:** Some APIs always return exactly `PAGE_SIZE` results even on the last page. Never rely solely on `len(results) < PAGE_SIZE` to detect the end. Use the `total` count from the first response to compute expected page count.

---

## Step 5 — Implement Stop Conditions

Without stop conditions, agents run until they fail or run out of budget. Define all four types before building:

**Data-found stop:** Halt as soon as required data is successfully extracted and validated. Don't continue scraping related pages once the goal is met.

**Budget stop:** Define maximum page count or token budget before the run starts. Return a structured error if the budget is exhausted without finding data.

```javascript
const MAX_PAGES = 5;
let pagesVisited = 0;
let result = null;

while (pagesVisited < MAX_PAGES && result === null) {
  const pageContent = await fetchAndClean(urlQueue.shift());
  result = await extractStructured(pageContent, schema);
  pagesVisited++;
}

return result === null
  ? { status: 'not_found', pages_checked: pagesVisited }
  : { status: 'success', data: result, pages_checked: pagesVisited };
```

**Quality threshold stop:** Stop when field completeness meets a defined threshold (e.g., all 5 fields populated with non-null values).

**Time-based stop:** Set an absolute wall-clock timeout for background runs to prevent runaway execution.

Always return structured metadata alongside results: how many pages checked, which stop condition triggered, and why.

**Stop conditions vs retry logic:** Stop conditions say "we're done." Retry logic says "try again differently." Transient failures (rate limit, network error) warrant retry with backoff. Structural failures (page will never have this data) should update stop condition logic — add the URL pattern to a blocklist or adjust the selector strategy. Conflating these leads to infinite retries on pages that will never yield good data.

---

## Step 6 — Design for Incremental Runs

One-shot scraping breaks on large datasets. Design skills to run incrementally: a little at a time, storing progress, resuming from a checkpoint.

**Checkpoint pattern:**
```javascript
const visited = await loadCheckpoints(jobId);
const pendingUrls = urlList.filter(url => !visited.has(url));
const batch = pendingUrls.slice(0, BATCH_SIZE);
// ... process batch ...
await saveCheckpoints(jobId, batch.map(url => url));
```

Track in the checkpoint store:
- Which URLs have been visited
- Which extractions succeeded / failed and why
- Timestamp of the last run

**Deduplication:** Always deduplicate before processing. Normalize URLs first: strip irrelevant query parameters, normalize trailing slashes, lowercase the domain. Same URL appearing twice wastes tokens and can corrupt results.

**Stream, don't batch:** Yield and publish records incrementally as they're extracted rather than collecting the full dataset first. This lets downstream consumers start processing immediately.

```python
for record in scraper.scrape_streaming():
    queue.publish(record)
```

---

## Step 7 — Assess Anti-Bot Defenses

Different blocking mechanisms require different solutions:

| Defense | Symptom | `requests` enough? | Solution |
|---------|---------|-------------------|---------|
| User-Agent filter | 403 / empty body | Yes | Add realistic `User-Agent` header |
| Referer / Origin check | 403 | Yes | Add `Referer` and `Origin` headers |
| IP rate limiting | 429 / slow responses | Yes | Jitter delays + exponential backoff |
| TLS fingerprinting | 403 even with full browser headers | **No** | Use Playwright (real Chrome TLS) |
| Cloudflare JS challenge | Redirect to challenge page | **No** | Playwright + `playwright-stealth` |
| CAPTCHA | Challenge image or slider | **No** | 2captcha / Anti-Captcha, or manual |
| Cookie-based session | Redirect to login | Sometimes | Extract and replay session cookies |

**TLS fingerprinting:** Python's `requests` library has a different TLS handshake signature than a real browser. Adding `User-Agent`, `Referer`, `Sec-Fetch-*` headers does **not** bypass this — the block happens at the TLS layer before HTTP headers are read. Playwright is the only reliable fix because it uses a real Chromium TLS stack.

**Apply jitter between ALL requests** — not just between pages, but between individual record fetches too. Make `JITTER_MIN` and `JITTER_MAX` configurable via environment variables. Respect `Retry-After` headers on 429 responses. Log 429s separately from 404s — they mean different things.

---

## Step 8 — Select Libraries

**Python:**
- Static HTML or JSON API: `requests` + `BeautifulSoup` / `lxml`
- JS-rendered / TLS-blocked: `playwright` (`sync_playwright` or `async_playwright`)

**JavaScript / Node:**
- Static HTML or JSON API: `axios` / `node-fetch` + `cheerio`
- JS-rendered: `playwright` or `puppeteer`

**When Playwright is required:**
- Page content is JS-rendered and no underlying API exists
- CDN or API uses TLS fingerprinting to block non-browser clients
- User interactions required (login, scroll, click)

---

## Step 9 — Handle IDs and URLs Safely

- Record IDs may contain slashes (e.g., `2026/10847`). Never assume IDs are URL-safe.
  - Flask: use `<path:notice_id>` converter.
  - URL path construction: convert slashes to dashes or percent-encode as needed.
- IDs may also contain spaces, Unicode, or special characters — always encode before inserting into URLs.

---

## Step 10 — Storage and Data Model

- Every field from an external API must be **nullable** in the DB schema. APIs frequently omit fields for valid records.
- Never add `NOT NULL` to external API fields without confirming the API always returns a value.
- Store arrays (nationalities, tags) as `TEXT[]` in PostgreSQL or as JSON.
- Store nested objects (e.g., arrest warrants, line items) as `JSONB` — enables server-side filtering without extra joins.
- Never expose internal storage hostnames (e.g., `minio:9000`) to the browser. Proxy through a backend route.

---

## Step 11 — Structured Output and Validation

**Prompt for structured output explicitly:**

```
Extract the following fields from the page content below.
Return ONLY valid JSON matching this schema. If a field cannot be
found, return null for that field. Do not include any explanation
or text outside the JSON object.

Schema: { "product_name": "string or null", "price": "number or null", ... }

Page content: [CONTENT]
```

Use model structured output / constrained JSON modes (available in Claude, GPT-4o, Gemini) when possible — more reliable than prompting alone.

**Validate after extraction:**
- All required fields present
- Field types match schema (price is a number, not `"$19.99"`)
- Values within reasonable ranges (price of -1 or 999999 is a parsing error)

**Return validation metadata alongside data:**
```json
{
  "status": "success",
  "data": { "product_name": "...", "price": 29.99 },
  "validation": {
    "fields_populated": 4,
    "fields_null": 0,
    "warnings": []
  },
  "pages_checked": 1,
  "stop_condition": "data_found"
}
```

---

## Quality Checks

- [ ] Page 1 and page 2 return different record IDs (pagination actually works)
- [ ] Total records in DB matches expected total from API `total` field after a full cycle
- [ ] No records rejected by DB NOT NULL constraints
- [ ] Jitter applied between ALL API calls, not just page transitions
- [ ] No internal hostnames (Docker service names) exposed to the browser
- [ ] Images load in browser without server-side fetching
- [ ] Full cycle completes without crashing even when individual records fail
- [ ] Stop conditions defined and tested for all four types
- [ ] Checkpoints written and resumed correctly across runs
- [ ] Structured output validated against schema before returning

---

## Common Failure Modes (Quick Reference)

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Every page returns same records | API ignores `page` param | Filter-based sweep |
| 403 even with browser headers | TLS fingerprinting | Use Playwright |
| DB NOT NULL violation on 3–5% of records | Nullable field declared NOT NULL | Remove NOT NULL from schema |
| Scraper loops forever past last page | Short-batch stop condition fails | Compute page count from `total` field |
| DB never grows past first page | `page=0` on 1-based API | Start at `page=1` |
| Images disappear after live update | `<img>` element replaced in DOM | Update fields in-place — never replace img |
| Queue idle during entire scrape | Batch publish after full collection | Switch to streaming generator |
| High token cost, low extraction quality | Full page sent to model | Strip HTML noise; use selector-based extraction |
| Agent runs indefinitely | No stop conditions | Add budget + data-found + time-based stops |
| Partial results lost on failure | No checkpointing | Add checkpoint store; resume from last saved state |
| Dynamic CSS selectors break | Class names regenerated on deploy | Use semantic selectors (ARIA roles, data attributes) |
| Retry loop on structural failures | Transient vs structural errors conflated | Separate retry logic from stop condition logic |
