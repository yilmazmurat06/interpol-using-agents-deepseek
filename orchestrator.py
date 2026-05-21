"""
orchestrator.py — OpenCode + DeepSeek pipeline entry point
-----------------------------------------------------------
Replaces Anthropic Managed Agents with the OpenCode CLI.
Each agent step calls `opencode run --agent <name>` locally.
Agent system prompts live in .opencode/agents/*.md (OpenCode native format).
Files are read from and written to the local repository directory.

state_machine.py drives the pipeline; this file provides:
  - OpenCodeRunner: wraps `opencode run` subprocess calls per agent step
  - Streaming NDJSON parsing for real-time console output
  - Context injection: CLAUDE.md + working protocol + task (system prompt via --agent)
"""

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    # Manual .env loader as fallback so python-dotenv isn't a hard dependency
    def load_dotenv(path):
        p = Path(path)
        if not p.exists():
            return
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip("\"'"))

try:
    import yaml
except Exception:
    import json as _json

    class _YAMLStub:
        @staticmethod
        def safe_load(s):
            return _json.loads(s)

    yaml = _YAMLStub()

from state_machine import StateMachine


# ── Configuration ─────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent.resolve()
AGENTS_DIR = BASE_DIR / ".claude/agents"
CLAUDE_MD  = BASE_DIR / "CLAUDE.md"

DEFAULT_OPENCODE_BIN = Path.home() / ".opencode/bin/opencode"

# role → (opencode agent name, allow_tools)
# Models are declared in .opencode/agents/*.md frontmatter — no need to repeat here.
AGENTS: dict[str, tuple[str, bool]] = {
    "orchestrator": ("interpol-orchestrator", False),
    "research":     ("interpol-research",     True),
    "developer":    ("interpol-developer",    True),
    "devops":       ("interpol-devops",       True),
    "qa":           ("interpol-qa",           True),
}


def _make_working_protocol(base_dir: Path) -> str:
    return f"""\
## Working environment

### Repository location
The repository is at: {base_dir}

### CLAUDE.md
Read the project constitution at: {base_dir}/CLAUDE.md
(Your system prompt says it is pre-loaded — in this environment you must read it explicitly as your first action.)

### Reading files
Use the `read` tool with absolute paths. Examples:
  {base_dir}/container_a/scraper.py
  {base_dir}/research/interpol-api-constraints.md

### Skills
Load skill instructions via the native `skill` tool — do NOT read SKILL.md files manually.
Call `skill("skill-name")` to load any skill. Available skills in this project:
  - interpol-full-stack-dev   → developer methodology, PSC patterns, self-verification steps
  - interpol-devops           → Dockerfile + compose methodology, verification scripts
  - interpol-qa               → QA validation procedure, evidence ledger format
  - interpol-orchestrator     → pipeline coordination rules
  - interpol-frontend         → web UI design and SSE patterns
  - research                  → external system probe methodology
  - web-scraping-skills       → anti-bot / curl_cffi guidance

Skill scripts (in .claude/skills/<name>/scripts/) are still run via `bash` with their absolute path:
  bash {base_dir}/.claude/skills/<skill-name>/scripts/<script>.sh

### Path mapping (your system prompt uses legacy cloud paths — translate as follows)
  /workspace/repo/          →  {base_dir}/
  /mnt/session/outputs/     →  {base_dir}/   (write directly to the repo)
There is NO output archive step. Skip any instruction to create outputs.tar.gz.

### Writing files
Write new and modified files directly to their correct absolute paths under {base_dir}/.
Use the exact relative paths defined in CLAUDE.md → File Layout.

### Files you MUST NOT modify (orchestrator-owned)
  {base_dir}/feature-list.json
  {base_dir}/CLAUDE.md
  {base_dir}/handoff/*.md
  {base_dir}/.claude/**
"""


# ── Console formatting ────────────────────────────────────────────────────────

_W = 64

def _banner(role: str, agent: str):
    ts  = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'─' * _W}")
    print(f"  {role.upper()}  ·  {agent}  ·  {ts}")
    print(f"{'─' * _W}\n")

def _footer(role: str, elapsed: float):
    mins, secs = divmod(int(elapsed), 60)
    duration   = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
    print(f"\n{'─' * _W}")
    print(f"  ✓ {role.upper()} done  ·  {duration}")
    print(f"{'─' * _W}\n")


# ── OpenCode runner ───────────────────────────────────────────────────────────

class OpenCodeRunner:
    """
    Drives the OpenCode CLI for each agent step.
    System prompts are loaded from .opencode/agents/*.md via --agent flag.
    No cloud infrastructure — agents work directly in BASE_DIR.
    """

    def __init__(self, opencode_bin: Path):
        if not opencode_bin.exists():
            raise SystemExit(
                f"opencode binary not found at: {opencode_bin}\n"
                f"Install OpenCode or set OPENCODE_BIN env var."
            )
        self.opencode_bin      = opencode_bin
        self._working_protocol = _make_working_protocol(BASE_DIR)

    # ── Public surface consumed by state_machine.py ───────────────────────────

    def agent_runner(self, role: str, prompt: str) -> str:
        if role not in AGENTS:
            raise ValueError(f"Unknown agent role: {role}")
        agent_name, allow_tools = AGENTS[role]
        return self._run(role, agent_name, prompt, allow_tools=allow_tools)

    def orchestrator_agent(self, prompt: str) -> str:
        agent_name, allow_tools = AGENTS["orchestrator"]
        return self._run("orchestrator", agent_name, prompt, allow_tools=allow_tools)

    # ── Core runner ───────────────────────────────────────────────────────────

    def _run(self, role: str, agent_name: str, task_prompt: str, allow_tools: bool) -> str:
        message = self._build_message(task_prompt, include_workspace=allow_tools)

        _banner(role, agent_name)
        t0 = time.time()

        # Model is declared in .opencode/agents/<agent>.md — no --model override needed.
        cmd = [
            str(self.opencode_bin),
            "run",
            "--agent", agent_name,
            "--dir", str(BASE_DIR),
            "--format", "json",
            "--dangerously-skip-permissions",  # non-interactive subprocess; tool access governed by agent file
            message,
        ]

        final_text = ""
        try:
            final_text = self._stream(cmd)
        except Exception as exc:
            print(f"\n  ✗ opencode error: {exc}")

        _footer(role, time.time() - t0)
        return final_text

    def _build_message(self, task_prompt: str, include_workspace: bool) -> str:
        """
        Build the user message sent to the agent.
        System prompt comes from the agent file via --agent; not duplicated here.
        """
        parts: list[str] = []

        if include_workspace:
            parts.append(self._working_protocol)

        parts.append("## Your task\n" + task_prompt)

        return "\n\n---\n\n".join(parts)

    # ── Subprocess streaming + NDJSON parsing ─────────────────────────────────

    def _stream(self, cmd: list[str]) -> str:
        """Run opencode, stream text to console, return full collected response."""
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        collected: list[str] = []
        display_buf = ""

        def _flush(final: bool = False):
            nonlocal display_buf
            while "\n" in display_buf:
                line, display_buf = display_buf.split("\n", 1)
                print(line)
            if final and display_buf:
                print(display_buf)
                display_buf = ""

        try:
            for raw_line in proc.stdout:
                collected.append(raw_line)
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                    text  = self._extract_text(event)
                    if text:
                        display_buf += text
                        _flush()
                except json.JSONDecodeError:
                    print(raw_line, end="", flush=True)
        finally:
            _flush(final=True)
            proc.stdout.close()
            stderr_out = proc.stderr.read()
            proc.wait()
            if proc.returncode != 0 and stderr_out.strip():
                print(f"\n  ✗ opencode stderr:\n{stderr_out[:2000]}", flush=True)

        return self._parse_events(collected)

    @staticmethod
    def _extract_text(event: dict) -> str:
        """Extract displayable text from a single NDJSON event."""
        # Primary: {part: {type: "text", text: "..."}}
        part = event.get("part", {})
        if isinstance(part, dict) and part.get("type") == "text":
            return part.get("text", "")

        # Fallback: delta format
        delta = event.get("delta", {})
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            return delta.get("text", "")

        # Fallback: flat text field
        if event.get("type") == "text":
            return event.get("text", "")

        return ""

    @staticmethod
    def _parse_events(lines: list[str]) -> str:
        """Reconstruct the full text response from all collected NDJSON lines."""
        parts: list[str] = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            text = OpenCodeRunner._extract_text(event)
            if text:
                parts.append(text)
        return "".join(parts).strip()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    (BASE_DIR / "handoff").mkdir(exist_ok=True)

    load_dotenv(BASE_DIR / ".env")

    opencode_bin = Path(os.environ.get("OPENCODE_BIN", str(DEFAULT_OPENCODE_BIN)))
    print(f"Using opencode: {opencode_bin}")
    print(f"Repository:     {BASE_DIR}")
    print(f"Agents dir:     {BASE_DIR / '.opencode/agents'}\n")

    runner = OpenCodeRunner(opencode_bin)

    sm = StateMachine(
        agent_runner       = runner.agent_runner,
        orchestrator_agent = runner.orchestrator_agent,
    )
    sm.run_session()


if __name__ == "__main__":
    main()
