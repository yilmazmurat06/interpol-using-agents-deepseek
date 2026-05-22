# Research Index

> Generated: 2026-05-22
> All documents in this directory are authoritative ground truth for downstream agents.

## Constraints Documents

| Target System | Document | Status |
|---------------|----------|--------|
| Interpol Public API (`ws-public.interpol.int`) | [interpol-api-constraints.md](interpol-api-constraints.md) | Complete — 29 probes, all checklist items covered |

## External Systems in Scope

From `CLAUDE.md`, the project's external systems are:

1. **Interpol Public API** (`ws-public.interpol.int`) — The sole external dependency. Includes:
   - List endpoint: `GET /notices/v1/red`
   - Detail endpoint: `GET /notices/v1/red/{entity_id}`
   - Image CDN: `GET /notices/v1/red/{entity_id}/images/{image_id}`

All other services (RabbitMQ, PostgreSQL, MinIO) are internal Docker services and are not external targets for research probing.

## Document Summary

### interpol-api-constraints.md
- **Headline gotchas:** TLS fingerprinting blocks all non-browser HTTP clients (must use `curl_cffi`); 160-record cap per query regardless of `page`/`resultPerPage`; entity_ids contain slashes but API URLs use dashes; HAL links are unreliable; image CDN must be proxied not hotlinked.
- **Hard rules:** 22 numbered MUST/MUST NOT rules covering TLS, Flask routing, URL construction, pagination, schema design, image proxying, and rate limiting.
- **Open questions:** 8 items — rate limit threshold, `freeText` parameter, response headers, CDN browser access, null date_of_birth, sex_id values, `charge_translation`.
- **Evidence:** 29 distinct probes with request details and response snippets.
