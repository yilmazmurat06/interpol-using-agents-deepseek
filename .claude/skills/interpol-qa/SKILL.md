---
name: interpol-qa
description: 'Use this skill when writing end-to-end tests, Playwright UI checks, requirements.txt verification, and structured failure reports for a multi-container data pipeline with a web UI.'
argument-hint: 'Write QA tests and produce structured failure reports for a pipeline + web UI project.'
---

# QA Engineer — Multi-Container Pipeline

## When to Use
- Writing end-to-end tests for a producer → queue → consumer → database → web UI flow
- Testing a server-rendered and SSE-updated web UI with Playwright
- Verifying `requirements.txt` alignment with Python source imports
- Producing structured JSON failure reports for the development team

## Inputs
- Base URL of the web UI under test
- Connection details for the message broker and database (for test setup/teardown)
- Error report endpoint or handoff file path

## Procedure

### 0. Run verification scripts (BLOCKING — before anything else)

```bash
bash .claude/skills/interpol-qa/scripts/run_all.sh
```

Runs static checks (PSC patterns, env consistency, requirements, engineering decisions) AND runtime checks (container health, API smoke, log scan — gracefully skipped if stack not running). Paste full output in the Evidence ledger.

**Any static FAIL = immediate Verdict: FAIL. Do not proceed.**

#### 0b. Manual hard-rule spot-check (supplement to scripts)

For each rule in `research/*-constraints.md` (anything labelled "Hard Rule", "Design rule", or MUST/MUST NOT) AND each pattern in `CLAUDE.md` → "Implementation Patterns":

1. Extract a unique grep-able token from the rule (a function name, an import, a config key, a string literal).
2. Run `grep -rn '<token>' container_*/ tests/` via the Bash tool.
3. Record one row in the Evidence Ledger:
   - The rule quoted verbatim (plus file:line of the source rule)
   - The exact grep command run
   - The result: file:line if found, or `NOT FOUND` if missing
   - Per-row verdict: `OBEYED` | `VIOLATED` | `NOT APPLICABLE`

Common tokens to grep for (project-agnostic examples):

| Rule type | Token(s) to grep |
|---|---|
| TLS-fingerprinting workaround | `curl_cffi`, `impersonate=` |
| Filter-sweep workaround for hard caps | the sweep function name, the country list constant |
| psycopg2 rollback containment | `rollback()` in the SAME try/except as `commit()` |
| Pika heartbeat configured on BOTH sides | `params.heartbeat` in code AND `RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS` in compose |
| Server-side pagination | `offset`, `count_notices`, `page_size`, `pages` |
| Healthcheck binary present in image | the binary name in the official image documentation |
| Circuit breaker for sustained errors | a threshold constant, a `_circuit_open_until` style variable |
| Streaming publish (per-record) | an `on_record` / callback parameter on the scrape function |
| Image proxy via curl_cffi (not 302) | `_image_session`, `Response(upstream.content, ...)` (NOT `redirect(...)`) |

**If any row is `VIOLATED` or `NOT FOUND` for a MUST/Hard rule, the verdict is FAIL.** The Evidence Ledger plus the violated-rule list IS the failure report.

### 1. Define end-to-end test cases
Cover the primary data flow:
- A new record published to the queue appears in the web UI after consumption
- An updated record that triggers an alarm/status change is visually marked in the UI
- Filter controls on the UI correctly narrow the displayed results
- SSE live updates arrive without a full page reload

### 2. Implement Playwright tests
- Load the UI and assert that the expected elements are present (record count, key field values, status badges)
- Use `page.wait_for_selector` or `expect(locator).to_be_visible()` rather than fixed `sleep` calls
- Add explicit waits or retries for asynchronous SSE updates — they may arrive after the initial render
- Test alarm/status badge visibility for records that meet the trigger condition
- Test filter interactions: apply a filter, assert the grid updates, clear the filter, assert reset

### 3. Verify `requirements.txt`
- Parse all Python `import` and `from ... import` statements in the source tree
- Check that each third-party package is listed in `requirements.txt`
- Report any packages that are imported but not listed (missing), and any that are listed but never imported (unused)
- Flag version pins that are unpinned (`package` vs `package==1.2.3`) — unpinned dependencies are a CI risk

### 4. Produce structured failure reports
When a test fails or a dependency issue is found, write a structured report to the handoff file or POST it to the error report endpoint:

```json
{
  "severity": "critical | high | medium | low",
  "component": "scraper | consumer | web_ui | database | queue | devops",
  "message": "Short description of the failure",
  "repro_steps": ["Step 1", "Step 2", "..."],
  "evidence": "Relevant log output, assertion error, or screenshot path",
  "suggested_fix": "Optional — only include if the fix is obvious"
}
```

Report one JSON object per distinct failure. Do not bundle multiple failures into a single report.

## Done Criteria
- **Hard-Rule audit (step 0) executed end-to-end with every Hard Rule cited and a per-row verdict.** This is the most load-bearing check; ~50% of historical FAIL-after-PASS bugs come from skipped or hand-waved rules here.
- Playwright tests cover: record appears after publish, alarm/status badge on update, filter behavior, SSE live update, **pagination next/prev**, **server-side total counter visible and live**
- E2E tests cover the full producer → queue → consumer → UI flow
- `requirements.txt` discrepancies (missing and unused packages) are detected and reported. Also flag any `requests` import in code that talks to an Akamai-fronted host — should be `curl_cffi` instead.
- Cross-check: every variable used as `${VAR}` in `docker-compose.yml` MUST exist in `.env.example` (one direction), AND every variable in `.env.example` MUST be referenced somewhere in `docker-compose.yml` or directly via `os.environ` (other direction). Name mismatches like `JITTER_MIN` (env) vs `JITTER_MIN_SECONDS` (code) are common failure modes.
- Every test failure produces a structured JSON report with severity, component, repro steps, and evidence

## Constraints
- QA does not run `docker-compose up` or manage live containers — tests assume the environment is already running or use fixtures/mocks
- QA does not modify application code — failures are reported, not fixed
- Static analysis and unit/integration tests are in scope; live E2E cluster tests are out of scope unless the environment is confirmed running

## Outputs
- Test code (`tests/test_*.py`, Playwright scripts) and structured JSON failure reports only
