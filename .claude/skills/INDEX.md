# Skills Index

> Every skill available to agents in this project. Each agent reads this file at session start and picks the skills relevant to its task. Skills are NOT pre-assigned per agent role — the agent decides based on what it has been asked to do.

## How to use this index

1. Read this index in full before starting work.
2. For your current task, identify which skills apply. A task may use one, several, or zero skills.
3. Read the SKILL.md of each selected skill in full before acting.
4. If multiple skills apply, read them all — they are designed to compose.
5. If no skill applies, proceed using CLAUDE.md and your system prompt alone — do not invent a skill.

## Available skills

| Skill ID | When to use | Path |
|---|---|---|
| `research` | Probing an external target system (API, website, service) BEFORE any code is written. Discovers runtime behavior, undocumented constraints, anti-bot defenses. Produces a structured constraints document downstream agents consume. | `.claude/skills/research/SKILL.md` |
| `web-scraping-skills` | Planning or debugging any scraping/data-extraction workflow. Covers HTTP vs headless browsing, selectors, headers/cookies, anti-bot/WAF issues, pagination patterns, token reduction, stop conditions, incremental runs, structured output validation. | `.claude/skills/web-scraping-skills/SKILL.md` |
| `interpol-full-stack-dev` | Implementing Python application code for a multi-container data pipeline (scraper, queue producer/consumer, persistence layer, web server, templates). | `.claude/skills/interpol-full-stack-dev/SKILL.md` |
| `interpol-frontend` | Designing or improving a data-monitoring web UI: record card grids, filter panels, status/alarm markers, SSE live updates, country flag display. Production-grade HTML/CSS/JS inside a Flask/Jinja2 template. | `.claude/skills/interpol-frontend/SKILL.md` |
| `interpol-devops` | Creating Dockerfiles, docker-compose configuration, environment variable docs, and deployment documentation for a multi-container Python microservice system. | `.claude/skills/interpol-devops/SKILL.md` |
| `interpol-qa` | Writing end-to-end tests, Playwright UI checks, requirements.txt audits, and structured failure reports for a multi-container data pipeline with a web UI. | `.claude/skills/interpol-qa/SKILL.md` |
| `interpol-orchestrator` | Coordinating a multi-agent dev workflow (read CLAUDE.md, run developer → DevOps → QA in sequence, loop back on QA failures, enforce done criteria). Used by humans modifying the pipeline; not normally invoked by agents at runtime. | `.claude/skills/interpol-orchestrator/SKILL.md` |

## Selection examples

- **Asked to build the scraper?** → `interpol-full-stack-dev` + `web-scraping-skills`
- **Asked to build the web UI?** → `interpol-full-stack-dev` + `interpol-frontend`
- **Asked to dockerize?** → `interpol-devops`
- **Asked to validate code?** → `interpol-qa`
- **Asked to probe a new API?** → `research` (and possibly `web-scraping-skills` if the target is a website)
- **Asked to write a planning doc?** → None — use CLAUDE.md and your system prompt.
