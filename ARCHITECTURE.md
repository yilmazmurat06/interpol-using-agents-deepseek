# Architecture — Interpol Red Notice Scraper Pipeline

> Multi-agent system targeting **one-shot production readiness**.
> Research replaces lessons-learned. QA loop preserved as insurance.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              YOU (operator)                                  │
│                          $ python orchestrator.py                            │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  orchestrator.py                     │  state_machine.py                     │
│  ─────────────────────────           │  ─────────────────────────            │
│  • ManagedSetup     (agent create)   │  • FeatureManager (feature-list.json) │
│  • ManagedRunner    (run + upload)   │  • HandoffManager (handoff/*.md)      │
│  • upload workspace → /workspace/    │  • StateMachine   (drives pipeline)   │
│  • download outputs ← /mnt/session/  │  • MAX_ITERATIONS = 3                 │
│  • run Docker tests locally          │  • _step_local_docker_tests()         │
└──────────────────────────────────────┴───────────────────────────────────────┘
                     │                                    │
                     │ Anthropic Managed Agents API       │ subprocess (local)
                     ▼                                    ▼
┌───────────────────────────────────┐  ┌─────────────────────────────────────┐
│    ANTHROPIC CLOUD ENVIRONMENT    │  │         LOCAL MACHINE               │
│                                   │  │                                     │
│  ┌──────────┐  ┌──────────┐       │  │  docker compose up --build -d       │
│  │Orchestrat│  │ Research │       │  │  pytest tests/ -v (+ Playwright)    │
│  │ (haiku)  │  │(opus-4-7)│       │  │  docker compose down -v             │
│  └──────────┘  └──────────┘       │  │           │                         │
│  ┌──────────┐  ┌──────────┐       │  │           ▼                         │
│  │Developer │  │  DevOps  │       │  │  handoff/docker-test-results.md     │
│  │(sonnet)  │  │(sonnet)  │       │  └─────────────────────────────────────┘
│  └──────────┘  └──────────┘       │
│  ┌──────────┐                     │
│  │    QA    │  (haiku)            │
│  └──────────┘                     │
└───────────────────────────────────┘
```

---

## Session Pipeline

```
START
  │
  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ ORCHESTRATOR LLM  (call #1)                                                  │
│ Reads feature-list.json → returns session plan                               │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 0 — RESEARCH                                                            │
│ ────────────────────                                                         │
│ Skipped if research/*-constraints.md already exists                          │
│                                                                              │
│ Probes every external system in CLAUDE.md (APIs, CDNs, auth endpoints)       │
│ Output: research/<target>-constraints.md  (Hard Rules for downstream agents) │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 1 — DEVELOPER BATCH                                                     │
│ ────────────────────                                                         │
│ ALL pending developer features in one session.                               │
│                                                                              │
│ Self-verification (mandatory before handoff):                                │
│   1. python -m py_compile <file>         (per file, after each write)        │
│   2. check_imports.sh                    (import-level runtime check)        │
│   3. dev_static_audit.sh                 (patterns + hard rules + ruff)      │
│                                                                              │
│ Output: container_a/*.py, container_b/*.py, templates/, tests/*.py           │
│ Features: pending → dev-complete                                             │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 1.5 — CROSS-FUNCTIONAL REVIEW LOOP  (max 3 rounds)                      │
│ ────────────────────                                                         │
│ DevOps and QA each independently review the developer's code.                │
│ Developer addresses consolidated findings.                                   │
│ Loop exits early on convergence (both reviewers report no findings).         │
│                                                                              │
│  Round N:                                                                    │
│    DevOps review → findings (infra readiness, env vars, Docker patterns)     │
│    QA review    → findings (correctness, Hard Rules, PSC-* patterns)         │
│    if both clean → EXIT LOOP (converged)                                     │
│    else → Developer fix pass → Round N+1                                     │
│                                                                              │
│ Purpose: catch issues before the formal QA verdict loop.                     │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 2 — QA BATCH  (loop, max 3 iterations)                                  │
│ ────────────────────                                                         │
│                                                                              │
│  ┌─ attempt N/3 ──────────────────────────────────────────────────────────┐  │
│  │ Runs: bash .claude/skills/interpol-qa/scripts/run_all.sh               │  │
│  │   ├── audit_hard_rules.sh      (static: Hard Rules)                    │  │
│  │   ├── check_env_consistency.sh (env-var bidirectional cross-check)     │  │
│  │   ├── check_requirements.sh    (imports vs requirements.txt)           │  │
│  │   ├── check_engineering_decisions.sh                                   │  │
│  │   ├── check_container_health.sh  (runtime, skipped if stack not up)    │  │
│  │   ├── check_api_smoke.sh         (runtime, skipped if stack not up)    │  │
│  │   └── check_container_logs.sh    (runtime, skipped if stack not up)    │  │
│  │ Writes and runs pytest (unit + integration tests)                      │  │
│  │ Fills Evidence ledger → Verdict: PASS or Verdict: FAIL                 │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│       PASS ─────────────────────────────────────────────► STEP 3             │
│       FAIL & attempt < 3 ──► DEVELOPER FIX BATCH ──────► back to QA          │
│       FAIL & attempt == 3 ──► features marked BLOCKED                        │
│                                   ↓                                          │
│                           ORCHESTRATOR LLM — blocked analysis                │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ all QA-passed
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 3 — DEVOPS BATCH                                                        │
│ ────────────────────                                                         │
│ Inputs: all qa-pass dev features + devops-owned features (F007, F008)        │
│                                                                              │
│ Runs before finalizing:                                                      │
│   bash .claude/skills/interpol-devops/scripts/run_all.sh                     │
│   ├── check_image_pinning.sh         (no :latest tags)                       │
│   ├── check_env_bidirectional.sh     (compose ↔ .env.example ↔ os.environ)   │
│   ├── check_no_hardcoded.sh                                                  │
│   ├── check_healthcheck_binaries.sh  (pg_isready, rabbitmq-diagnostics, mc)  │
│   ├── check_protocol_negotiation.sh  (RabbitMQ heartbeat both sides)         │
│   └── check_readme.sh                                                        │
│                                                                              │
│ Output: Dockerfile×2, docker-compose.yml, .env.example, README.md            │
│ Features: qa-pass → devops-complete                                          │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 4a — LOCAL DOCKER TESTS  (orchestrator, runs on this machine)           │
│ ────────────────────                                                         │
│ The Managed Agents cloud environment has no Docker daemon, so the            │
│ orchestrator runs the full test suite locally via subprocess.                │
│                                                                              │
│ Script: .claude/skills/interpol-qa/scripts/run_docker_tests.sh               │
│   1. cp .env.example .env                                                    │
│   2. docker compose up --build -d                                            │
│   3. wait for all 5 services healthy (max 3 min)                             │
│   4. pip install tests/requirements.txt + playwright install chromium        │
│   5. BASE_URL=http://localhost:8080 pytest tests/ -v --timeout=60            │
│      (includes test_ui.py — Playwright browser tests against live Flask)     │
│   6. docker compose down -v                                                  │
│                                                                              │
│ Output: handoff/docker-test-results.md(✅ PASSED / ❌ FAILED / ⚠️ SKIPPED)   │
│                                                                              │
│ Skipped gracefully if:                                                       │
│   • SKIP_DOCKER_TESTS=1 env var is set                                       │
│   • docker binary not found on the machine                                   │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 4b — QA DOCKER VERDICT  (cloud agent)                                   │
│ ────────────────────                                                         │
│ QA agent reads handoff/docker-test-results.md (uploaded with workspace)      │
│ Runs static checks (run_all.sh) + incorporates Docker results                │
│ Fills Evidence ledger → issues final Verdict: PASS or Verdict: FAIL          │
│                                                                              │
│   ❌ FAILED results → verdict MUST be FAIL → features BLOCKED                │
│   ⚠️ SKIPPED results → verdict based on static analysis only                 │
│   ✅ PASSED results → verdict based on static + runtime evidence             │
│                                                                              │
│ Features: devops-complete → done (PASS) or blocked (FAIL)                    │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ ORCHESTRATOR LLM  (call #2 of session)                                       │
│ Reads final state → returns session summary                                  │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
                                      END
```

---

## State Transitions (per feature)

```
  ┌─────────┐    ┌───────────┐    ┌─────────────┐    ┌──────────┐
  │ pending │───▶│in-progress│───▶│dev-complete │───▶│ qa-pass  │
  └─────────┘    └───────────┘    └─────────────┘    └────┬─────┘
                                         │                │
                                         │ QA FAIL        │
                                         │ (attempt < 3)  ▼
                                         │          ┌──────────────────┐
                                         │          │ devops-complete   │
                                         │          └────────┬─────────┘
                                         ▼                   │
                                  ┌─────────────┐            │ Docker PASS
                                  │ developer   │            ▼
                                  │   fix       │       ┌──────────┐
                                  └──────┬──────┘       │   done   │
                                         │              └──────────┘
                                         └──────────────────────────────────┐
                                                                            │
                                                                            ▼
  ┌─────────┐                                                         (back to QA)
  │ blocked │ ← QA FAIL after 3 attempts, or Docker verdict FAIL
  └─────────┘     (orchestrator LLM analyzes, suggests recovery)
```

---

## File-System Topology

```
LOCAL REPO                        UPLOADED TO AGENT                EXPECTED OUTPUTS
──────────                        ─────────────────                ────────────────

CLAUDE.md           ──upload──►   /workspace/repo/CLAUDE.md
feature-list.json   ──upload──►   /workspace/repo/feature-list.json
research/*.md       ──upload──►   /workspace/repo/research/        ◄── written by Research
container_a/*       ──upload──►   /workspace/repo/container_a/     ◄── written by Developer
container_b/*       ──upload──►   /workspace/repo/container_b/     ◄── written by Developer
tests/*             ──upload──►   /workspace/repo/tests/           ◄── written by Developer/QA
handoff/*.md        ──upload──►   /workspace/repo/handoff/
docker-compose.yml  ──upload──►   /workspace/repo/...              ◄── written by DevOps
.env.example        ──upload──►   /workspace/repo/...              ◄── written by DevOps
README.md           ──upload──►   /workspace/repo/...              ◄── written by DevOps
.claude/skills/**   ──upload──►   /workspace/repo/.claude/skills/

                                                       Agent writes to:
                                                       /mnt/session/outputs/<path>
                                                                  │
                                                                  │ tar czf outputs.tar.gz
                                                                  ▼
                                                       Orchestrator downloads,
                                                       extracts tarball,
                                                       places files at relative paths
                                                       back into LOCAL REPO

PROTECTED (orchestrator-only, never overwritten by agents):
  feature-list.json
  CLAUDE.md
  handoff/*.md
  .claude/**
```

---

## Communication Pattern (handoff files)

```
                              handoff/
                              ────────
   Developer ──────► dev-to-qa.md ──────────────────► QA
                                                        │
                                                        │  on PASS:
                                                        ▼
                              qa-to-devops.md ─────────► DevOps
                                                                │
                                                                ▼
                                                     devops-to-qa.md
                                                                │
                              [orchestrator runs Docker tests]  │
                                                                ▼
                              docker-test-results.md ──────────► QA (Docker verdict)
                                                        │
                                                        │  on FAIL:
                                                        ▼
                              qa-to-dev.md ──────────────► Developer (fix loop)


Rules:
  • Agents NEVER write handoff files directly
  • Orchestrator captures each agent's final message and writes the file
  • docker-test-results.md is written by the orchestrator's local subprocess
  • Agents only READ handoff/*.md as input context
```

---

## Where LLM Calls Happen

```
PER SESSION:
  ├── Orchestrator LLM        × 2     (plan + summary)            tiny, planning-only
  ├── Research Agent           × 1     (opus-4-7, tools)           probes external systems
  ├── Developer Agent          × 1-4   (sonnet-4-6, tools)         batch + fix loops
  ├── DevOps Agent (review)    × 1-3   (sonnet-4-6, tools)         cross-review rounds
  ├── QA Agent (review)        × 1-3   (haiku, tools)              cross-review rounds
  ├── QA Agent (batch)         × 1-3   (haiku, tools)              formal verdict loop
  ├── DevOps Agent (batch)     × 1     (sonnet-4-6, tools)         dockerize + docs
  ├── QA Agent (Docker verdict)× 1     (haiku, tools)              reads docker-test-results.md
  └── Orchestrator LLM         × N     (one per blocked feature)   blocked analysis

  Note: prompt caching is NOT supported by the Managed Agents sessions API.
  Research runs ONCE per project (cached via filesystem check on research/).
```

---

## Key Architectural Decisions

| Decision | Why |
|---|---|
| **Research replaces lessons-learned** | One-shot production readiness; no learning-from-failure crutch |
| **Research is one-shot, not per-feature** | API constraints don't change between features |
| **Research output is cached on disk** | Re-running the pipeline doesn't re-probe; delete research/ to force re-probe |
| **Cross-functional review loop** | DevOps and QA catch issues before formal verdict; reduces QA fail iterations |
| **QA loop kept (3 attempts)** | Insurance against gaps in research; can be removed when pipeline proves reliable |
| **Batch dev + batch QA** | Agents see cross-feature context, produce cohesive code |
| **Docker tests run locally by orchestrator** | Managed Agents cloud env has no Docker daemon; orchestrator runs on operator machine which does |
| **QA issues Docker verdict, not orchestrator** | Authoritative pass/fail judgment belongs to QA; orchestrator only runs the mechanics |
| **Orchestrator LLM is plan-only** | Determinism in pipeline control; LLM only for soft judgments |
| **Handoff via files, not direct calls** | Agents are stateless sessions; files are the only persistent channel |
| **No prompt caching** | Managed Agents sessions API rejects cache_control at all levels; not supported yet |

---

## Agent Roster

| Agent | Model | Tools | Purpose |
|---|---|---|---|
| Orchestrator | haiku-4-5 | none | Plan / summarize / analyze blocked features |
| Research | **opus-4-7** | bash, web_fetch, web_search, read/write/edit | Probe external systems; produce constraints docs |
| Developer | sonnet-4-6 | bash, web_fetch, web_search, read/write/edit | Implement all Python code in one batch |
| DevOps | sonnet-4-6 | read/write/edit, bash, grep, glob | Dockerize + write README |
| QA | **haiku-4-5** | read/write/edit, bash, grep, glob | Write/run tests, audit deps, issue verdict |

---

## Skills & Scripts

| Skill | Scripts | Read by | Purpose |
|---|---|---|---|
| `research/` | `run_all.sh`, `verify_hard_rules_present.sh`, `verify_probe_evidence.sh` | Research | Probe checklist + constraints doc format |
| `interpol-full-stack-dev/` | `run_all.sh`, `check_imports.sh`, `dev_static_audit.sh`, pattern checks | Developer | Implementation procedure; self-verification |
| `interpol-frontend/` | — | Developer | UI design + component specs |
| `interpol-devops/` | `run_all.sh`, `check_env_bidirectional.sh`, `check_image_pinning.sh`, etc. | DevOps | Multi-container Docker patterns; self-verification |
| `interpol-qa/` | `run_all.sh`, `run_docker_tests.sh`, `audit_hard_rules.sh`, smoke checks | QA | E2E + Playwright + dep audit + structured reports |
| `interpol-orchestrator/` | — | Orchestrator | Multi-agent workflow coordination |
| `web-scraping-skills/` | — | Developer (via main skill) | Pagination, anti-bot, stop conditions |

Skills are deliberately **generic** (no project-specific bug fixes baked in).
The Research agent's output is what makes each run project-specific.

---

## Environment Controls

| Variable | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | API authentication |
| `SKIP_DOCKER_TESTS` | unset | Set to `1` to skip Step 4a; QA verdicts on static analysis only |
| `FLASK_PORT` | `8080` | Port used by `BASE_URL` in pytest / smoke tests |
