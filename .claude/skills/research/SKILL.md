---
name: research
description: 'Use this skill when probing an external target system (API, website, service) BEFORE any code is written. Discovers runtime behavior, undocumented constraints, and assumption-breaking quirks, then publishes a structured constraints document that downstream developer agents consume to build production-ready code in one shot.'
argument-hint: 'Identify the target system to probe and the assumptions to verify.'
---

# System Research / Reconnaissance

## Purpose

Eliminate discovery-at-runtime. Every constraint a developer would otherwise hit by failing (pagination broken, auth missing, fields nullable, IDs unsafe, server-side fetch blocked) is discovered here, before a single line of application code exists.

Output is a single document: `research/<target-name>-constraints.md`. Downstream developer agents read it as authoritative ground truth.

---

## When to Use
- A new external system (REST API, GraphQL, scraped website, third-party service) is being integrated for the first time
- Existing assumptions need re-verification after the target system changes
- A previous build failed at runtime due to an undocumented behavior — re-probe instead of patching blindly

---

## Core Principle: Probe, Don't Assume

Documentation lies. SDKs are incomplete. Stack Overflow is outdated. The only authoritative source is the live system. Hit it with real requests and observe what it actually does.

Every claim in the output document must be backed by an actual probe — never inferred from documentation or prior knowledge.

---

## Mandatory Probe Checklist

For every external system, verify each of the following with a real request:

### Connectivity & Auth
- [ ] Minimal request that returns 200 (no auth, no headers) — does it work?
- [ ] Required headers to avoid 403 (User-Agent, Referer, Origin, Sec-Fetch-*)
- [ ] Required authentication (none, API key, Bearer token, OAuth, session cookie)
- [ ] TLS fingerprinting (does `requests` get 403 where curl/browser succeeds?) — if yes, server-side scraping requires Playwright

### Response Shape
- [ ] Top-level JSON structure (keys, types)
- [ ] Which fields are always present vs sometimes null
- [ ] Which fields are arrays vs scalars
- [ ] Nested structures (e.g., `_embedded`, `data.items`)
- [ ] Total record count (look for `total`, `count`, `_meta.total`)

### Pagination
- [ ] Does the `page` parameter actually change results? (compare page=1 vs page=2 IDs)
- [ ] Is it 0-indexed or 1-indexed?
- [ ] Is there a `_links.next` HAL cursor?
- [ ] Maximum `resultPerPage` / `limit` allowed
- [ ] What does the API return past the last page? (empty, error, repeat?)
- [ ] **Critical: if `page` is ignored**, find the alternate sweep dimension (filter by category, region, date range)

### Detail vs List Endpoints
- [ ] Does the list endpoint return full records, or only summaries?
- [ ] If summaries: which fields are missing and only available from `GET /{id}`?
- [ ] URL format for detail endpoint (does the ID need escaping or character substitution?)

### ID Format
- [ ] Format of record IDs (numeric, slug, contains slashes, contains spaces, contains Unicode)
- [ ] Are IDs URL-safe as-is, or do they need encoding?
- [ ] **If IDs contain slashes**: Flask routes need `<path:var>`, not `<var>`

### Rate Limiting & Anti-Bot
- [ ] Observed rate limit (requests per second before 429)
- [ ] Does the API send `Retry-After` headers?
- [ ] Are there per-IP, per-user, or per-endpoint limits?
- [ ] Recommended safe jitter range between requests

### Assets (Images, Files)
- [ ] CDN URLs — do they work from `curl`/`requests`, or only from a browser?
- [ ] If browser-only: TLS fingerprinting is the cause; only Playwright bypasses it
- [ ] Are URLs stable, or do they expire / use signed tokens?

### Storage / Routing Hazards
- [ ] Internal hostnames (e.g., `service:9000`) that must NEVER be exposed to the browser
- [ ] Endpoints that need a backend proxy to be browser-reachable

---

## Procedure

1. **Read the project description** (CLAUDE.md, requirements, task brief) to identify every external system in scope.
2. **For each system**, run the full probe checklist above. Use `curl`, `requests`, or the web_fetch tool to actually hit endpoints — never speculate.
3. **Record raw evidence** for each finding: the exact request, the response shape, the observed behavior. A constraint without evidence is a guess.
4. **Write the constraints document** to `research/<target-name>-constraints.md` using the format below.
5. **Flag unknowns**: if a probe is impossible (auth not available, requires production data, etc.), say so explicitly. Do not paper over gaps.

---

## Output Format

One markdown file per target system at `research/<target-name>-constraints.md`:

```markdown
# <Target Name> — Runtime Constraints
> Probed: YYYY-MM-DD
> Source of truth for downstream agents. Every claim here is backed by a real probe.

## Summary
One paragraph: what this system is, what data we need from it, the headline gotchas.

## Endpoints
| Method | Path | Purpose | Auth | Notes |
|--------|------|---------|------|-------|
| GET    | /... | ...     | none | ...   |

## Authentication
What works, what doesn't, evidence.

## Response Shape
Example response (trimmed). Which fields are always present. Which are nullable. Which are arrays.

## Pagination
Exact behavior. If broken, the workaround (e.g., "use ?nationality= filter sweep over ISO country codes").

## ID Format
Example IDs. Special character handling. URL escaping rules.

## Rate Limits
Observed rate. Recommended jitter range.

## Assets (Images / Files)
What can be fetched server-side. What requires a browser. Recommended approach.

## Hard Rules for Downstream Code
- Numbered list of MUST/MUST NOT rules derived from the probes.
- Example: "MUST use `<path:notice_id>` in Flask routes — IDs contain slashes."
- Example: "MUST NOT fetch CDN images via `requests` — TLS fingerprinting; serve URL directly."

## Open Questions
Things that could not be verified and why.
```

---

## Quality Bar

- Every section is filled in or explicitly marked "N/A — reason"
- Every constraint has evidence (request + response snippet) in an appendix or inline
- Hard rules are phrased so a developer can act on them without reading the appendix
- No copy-pasted documentation — only first-hand observations

---

## What This Skill Does NOT Do

- Does not write application code
- Does not design the database schema
- Does not write tests
- Does not specify implementation details — only the constraints the implementation must obey
