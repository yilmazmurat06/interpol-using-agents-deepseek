---
name: interpol-orchestrator
description: 'Use this skill when coordinating a multi-agent development workflow: read the project constitution (CLAUDE.md), run developer → DevOps → QA agents in sequence, loop back on QA failures, and enforce done criteria before advancing.'
argument-hint: 'Orchestrate a developer/devops/qa agent workflow for a software project.'
---

# Orchestrator — Multi-Agent Development Pipeline

## When to Use
- Managing the order and handoffs between developer, DevOps, and QA agents
- Ensuring each agent's output meets its done criteria before the next agent starts
- Handling QA failure reports and routing them back to the responsible agent
- Tracking feature progress and enforcing iteration limits

## Procedure

1. **Read the project constitution** (`CLAUDE.md` or equivalent) and internalize all project-specific rules, constraints, and agent responsibilities before issuing any tasks.

2. **Run agents in the correct order:**
   - **Developer** → writes application code (backend, frontend, data pipeline)
   - **DevOps** → containerizes the application, configures compose/orchestration, documents setup
   - **QA** → writes and runs tests, verifies requirements files, validates done criteria

3. **Enforce done criteria** at each handoff. An agent's output is not accepted until all its done criteria are met. Do not advance to the next agent until the current one has passed.

4. **Handle QA failures:**
   - Route the QA failure report back to the responsible agent (developer or DevOps)
   - Pause other work until the fix is complete
   - Re-run QA checks after each fix
   - Track the iteration count per feature — after the maximum allowed iterations (defined in CLAUDE.md), mark the feature as blocked and escalate

5. **Update the progress log** (`claude-progress.txt` or equivalent) after each agent step with: agent name, feature ID, status, summary, and next step.

6. **DevOps-only features** (e.g., Docker infrastructure, documentation) go directly to DevOps without a prior developer step and do not require a QA review cycle.

## Done Criteria (per agent type)
- **Developer:** Application code runs end-to-end; all env config read from environment variables; no hardcoded credentials
- **DevOps:** All containers start with correct networking; environment is fully configurable; README explains setup and troubleshooting
- **QA:** Tests cover the primary data flow and any alert/status behavior; dependency files are clean; structured failure reports produced for any issues found

## Constraints
- No agent may revert a feature status — only the orchestrator may change `feature-list.json`
- Agents never communicate directly — all handoffs go through files in the `handoff/` directory
- Maximum iterations per feature is defined in CLAUDE.md; the orchestrator enforces this limit
- Every session ends with a git commit; incomplete work goes to a branch, never to main

## Outputs
- Orchestration decisions, status updates, and handoff routing only — no application code
