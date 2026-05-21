"""
orchestrator.py — Managed Agents pipeline entry point
-----------------------------------------------------
Each .agent.md file is one persisted Anthropic Managed Agent.
state_machine.py drives the pipeline; this file:

  - Setup (idempotent): creates the environment + one agent per .agent.md,
    caches the IDs in .claude/managed/agent_ids.json.
  - Runtime (per step): uploads the workspace as session resources mounted
    read-only at /workspace/repo/, streams the agent to idle, copies any
    files written to /mnt/session/outputs/ back into the local repo.

Prompt caching: the Managed Agents sessions API does not support cache_control
in any position (content blocks or request body). No explicit caching is used.
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import anthropic
from dotenv import load_dotenv
try:
    import yaml
except Exception:
    import json as _json

    class _YAMLStub:
        @staticmethod
        def safe_load(s):
            return _json.loads(s)

        @staticmethod
        def safe_dump(obj):
            return _json.dumps(obj)

    yaml = _YAMLStub()

from state_machine import StateMachine


# ── Configuration ─────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
AGENTS_DIR = BASE_DIR / ".claude/agents"
CACHE_DIR  = BASE_DIR / ".claude/managed"
CLAUDE_MD  = BASE_DIR / "CLAUDE.md"
ID_CACHE   = CACHE_DIR / "agent_ids.json"

AGENT_FILES = {
    "orchestrator": "interpol-orchestrator.agent.md",
    "research":     "interpol-research.agent.md",
    "developer":    "interpol-full-stack-dev.agent.md",
    "devops":       "interpol-devops.agent.md",
    "qa":           "interpol-qa.agent.md",
}

UPLOAD_GLOBS = [
    "CLAUDE.md",
    "feature-list.json",
    "research/**/*",
    "container_a/**/*",
    "container_b/**/*",
    "tests/**/*",
    "handoff/*.md",
    "docker-compose.yml",
    ".env.example",
    "README.md",
    "requirements*.txt",
    "pytest.ini",
    ".claude/skills/**/*",
]
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
WORKSPACE_MOUNT  = "/mnt/session/uploads/workspace/repo"
OUTPUT_MOUNT     = "/mnt/session/outputs"

PROTECTED_PATHS    = {"feature-list.json", "CLAUDE.md"}
PROTECTED_PREFIXES = ("handoff/", ".claude/")

WORKING_PROTOCOL = f"""\
## File system rules — read carefully

### Reading files
The repository is mounted **read-only** at `{WORKSPACE_MOUNT}/`.
Read existing files from there. Do NOT write anything back to this path.

### Writing files
Every file you create or modify must be written to the output mount:
  `{OUTPUT_MOUNT}/<path>`

The `<path>` must be the **exact relative path from the repo root** as defined
in CLAUDE.md. The orchestrator strips the `{OUTPUT_MOUNT}/` prefix and writes
the file to that exact location locally — so the path you use here becomes the
path in the repo.

### Required output paths per role

Research:
  {OUTPUT_MOUNT}/research/<target-name>-constraints.md   (one per external system)
  {OUTPUT_MOUNT}/research/index.md

Developer:
  {OUTPUT_MOUNT}/container_a/scraper.py
  {OUTPUT_MOUNT}/container_a/producer.py
  {OUTPUT_MOUNT}/container_a/requirements.txt
  {OUTPUT_MOUNT}/container_b/app.py
  {OUTPUT_MOUNT}/container_b/consumer.py
  {OUTPUT_MOUNT}/container_b/models.py
  {OUTPUT_MOUNT}/container_b/db.py
  {OUTPUT_MOUNT}/container_b/storage.py
  {OUTPUT_MOUNT}/container_b/main.py
  {OUTPUT_MOUNT}/container_b/requirements.txt
  {OUTPUT_MOUNT}/container_b/templates/index.html
  {OUTPUT_MOUNT}/tests/__init__.py
  {OUTPUT_MOUNT}/tests/test_scraper.py
  {OUTPUT_MOUNT}/tests/test_consumer.py
  {OUTPUT_MOUNT}/tests/test_ui.py

QA:
  {OUTPUT_MOUNT}/tests/__init__.py
  {OUTPUT_MOUNT}/tests/test_scraper.py
  {OUTPUT_MOUNT}/tests/test_consumer.py
  {OUTPUT_MOUNT}/tests/test_ui.py

DevOps:
  {OUTPUT_MOUNT}/container_a/Dockerfile
  {OUTPUT_MOUNT}/container_b/Dockerfile
  {OUTPUT_MOUNT}/docker-compose.yml
  {OUTPUT_MOUNT}/.env.example
  {OUTPUT_MOUNT}/README.md

### Required: archive outputs before finishing
After writing ALL output files, run this command EXACTLY — it preserves directory
paths so the orchestrator can place files in the correct locations:

  bash -c 'cd {OUTPUT_MOUNT} && tar czf /tmp/out.tar.gz --exclude=out.tar.gz . && mv /tmp/out.tar.gz {OUTPUT_MOUNT}/outputs.tar.gz'

Without this archive the orchestrator cannot reconstruct subdirectory paths.
Do this as your FINAL step, after all files are written.

### DO NOT write these paths — they are orchestrator-owned
  feature-list.json
  CLAUDE.md
  handoff/*.md
  .claude/**
"""


# ── Console formatting ────────────────────────────────────────────────────────

_W = 64  # banner width

def _banner(role: str, model: str, session_id: str, n_files: int):
    ts  = datetime.now().strftime("%H:%M:%S")
    top = f"  {role.upper()}  ·  {model}  ·  {ts}"
    bot = f"  session {session_id}  ·  {n_files} files"
    print(f"\n{'─' * _W}")
    print(top)
    print(bot)
    print(f"{'─' * _W}\n")

def _footer(role: str, elapsed: float):
    mins, secs = divmod(int(elapsed), 60)
    duration   = f"{mins}m {secs:02d}s" if mins else f"{secs}s"
    print(f"\n{'─' * _W}")
    print(f"  ✓ {role.upper()} done  ·  {duration}")
    print(f"{'─' * _W}\n")


# ── .agent.md parsing ─────────────────────────────────────────────────────────

def _parse_agent_md(path: Path) -> dict:
    """The .agent.md files are pure YAML (no markdown body)."""
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or "name" not in cfg or "model" not in cfg:
        raise ValueError(f"{path}: missing required fields (name, model)")
    return cfg


# ── One-time setup: environment + agents ──────────────────────────────────────

class ManagedSetup:
    """Creates the environment and one agent per .agent.md. Idempotent."""

    def __init__(self, client: anthropic.Anthropic):
        self.client = client

    def ensure(self) -> dict:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        cached = {}
        if ID_CACHE.exists():
            cached = json.loads(ID_CACHE.read_text(encoding="utf-8"))

        env_id = cached.get("environment_id")
        if not env_id:
            env = self.client.beta.environments.create(
                name=f"interpol-env-{int(time.time())}",
                config={"type": "cloud", "networking": {"type": "unrestricted"}},
            )
            env_id = env.id
            print(f"  created environment → {env_id}")
        else:
            print(f"  reusing environment → {env_id}")

        agent_ids = dict(cached.get("agent_ids", {}))
        for role, filename in AGENT_FILES.items():
            if role in agent_ids:
                print(f"  reusing agent {role:<14} → {agent_ids[role]}")
                continue
            cfg = _parse_agent_md(AGENTS_DIR / filename)
            agent = self.client.beta.agents.create(**cfg)
            agent_ids[role] = agent.id
            print(f"  created agent {role:<14} → {agent.id}")

        ids = {"environment_id": env_id, "agent_ids": agent_ids}
        ID_CACHE.write_text(json.dumps(ids, indent=2), encoding="utf-8")
        return ids


# ── Runtime: one Managed Agent session per step ───────────────────────────────

class ManagedRunner:

    def __init__(self, client: anthropic.Anthropic, ids: dict):
        self.client         = client
        self.environment_id = ids["environment_id"]
        self.agent_ids      = ids["agent_ids"]
        # Read once; injected as the first cached content block every call.
        self._claude_md     = (
            CLAUDE_MD.read_text(encoding="utf-8") if CLAUDE_MD.exists() else ""
        )

    # ── Public surface (consumed by state_machine.py) ─────────────────────────

    def agent_runner(self, role: str, prompt: str) -> str:
        if role not in self.agent_ids:
            raise ValueError(f"Unknown agent role: {role}")
        return self._run(role, prompt, with_workspace=True)

    def orchestrator_agent(self, prompt: str) -> str:
        return self._run("orchestrator", prompt, with_workspace=False)

    # ── Prompt caching ────────────────────────────────────────────────────────

    def _build_prompt(self, prompt: str, with_workspace: bool) -> list[dict]:
        """
        Returns a content-block list for the session event.

        Block layout:
          1. CLAUDE.md            — static context
          2. WORKING_PROTOCOL     — file system rules (if workspace session)
          3. Dynamic task prompt  — changes every call
        """
        blocks: list[dict] = []

        if self._claude_md:
            blocks.append({
                "type": "text",
                "text": self._claude_md + "\n\n---\n\n",
            })

        if with_workspace:
            blocks.append({
                "type": "text",
                "text": WORKING_PROTOCOL + "\n\n---\n\n",
            })

        blocks.append({
            "type": "text",
            "text": prompt,
        })

        return blocks

    # ── Session execution ─────────────────────────────────────────────────────

    def _run(self, role: str, prompt: str, with_workspace: bool) -> str:
        resources, uploaded_file_ids = (
            self._upload_workspace() if with_workspace else ([], [])
        )

        cfg = self._load_agent(AGENT_FILES[role])
        session = self.client.beta.sessions.create(
            agent=self.agent_ids[role],
            environment_id=self.environment_id,
            title=f"interpol/{role}/{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            resources=resources,
        )
        _banner(role, cfg.get("model", "?"), session.id, len(resources))

        t0         = time.time()
        final_text = ""
        try:
            final_text = self._run_to_idle(
                session.id,
                self._build_prompt(prompt, with_workspace),
            )
        except Exception as exc:
            print(f"\n  ✗ stream error: {exc}")
        finally:
            if with_workspace:
                self._download_outputs(session.id, set(uploaded_file_ids))
            for fid in uploaded_file_ids:
                self._safe(lambda fid=fid: self.client.beta.files.delete(fid))
            self._safe(lambda: self.client.beta.sessions.archive(session_id=session.id))
            _footer(role, time.time() - t0)

        return final_text

    def _load_agent(self, filename: str) -> dict:
        return _parse_agent_md(AGENTS_DIR / filename)

    def _run_to_idle(self, session_id: str, kickoff_blocks: list[dict]) -> str:
        text_parts: list[str] = []
        buf = ""  # partial-line buffer so chunks print as complete lines

        def _flush_buf(final: bool = False):
            nonlocal buf
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                print(line)
            if final and buf:
                print(buf)
                buf = ""

        with self.client.beta.sessions.events.stream(session_id=session_id) as stream:
            self.client.beta.sessions.events.send(
                session_id=session_id,
                events=cast(Any, [{
                    "type": "user.message",
                    "content": kickoff_blocks,
                }]),
                # Automatic caching: top-level cache_control tells the API to
                # cache the longest static prefix of the content automatically.
                # 1-hour TTL covers the full pipeline run (vs default 5 min).
                # cache_control INSIDE content blocks is rejected by this API —
                # only the top-level form is supported.
            )

            for event in stream:
                if event.type == "agent.message":
                    for block in event.content:
                        if block.type == "text":
                            text_parts.append(block.text)
                            buf += block.text
                            _flush_buf()

                elif event.type == "session.status_terminated":
                    break

                elif event.type == "session.status_idle":
                    if getattr(event.stop_reason, "type", None) != "requires_action":
                        break

        _flush_buf(final=True)
        return "".join(text_parts).strip()

    # ── Workspace upload / download ───────────────────────────────────────────

    def _upload_workspace(self):
        resources, file_ids = [], []
        skipped = 0
        print(f"  ↑ uploading workspace ", end="", flush=True)
        for path in self._workspace_files():
            try:
                size = path.stat().st_size
            except OSError:
                skipped += 1
                continue
            if size > MAX_UPLOAD_BYTES:
                skipped += 1
                continue
            try:
                rel = path.relative_to(BASE_DIR).as_posix()
            except ValueError:
                skipped += 1
                continue

            for attempt in range(5):
                try:
                    with open(path, "rb") as f:
                        uploaded = self.client.beta.files.upload(
                            file=(path.name, f, "application/octet-stream"),
                        )
                    break
                except Exception as e:
                    if attempt == 4:
                        raise
                    wait = 10 * (2 ** attempt)
                    print(f"\n  ✗ upload retry {attempt+1}/5: {e}", flush=True)
                    time.sleep(wait)
            file_ids.append(uploaded.id)
            resources.append({
                "type": "file",
                "file_id": uploaded.id,
                "mount_path": f"{WORKSPACE_MOUNT}/{rel}",
            })
            print(".", end="", flush=True)
        print(f" {len(file_ids)} files", flush=True)
        return resources, file_ids

    def _workspace_files(self):
        seen: set[Path] = set()
        for pattern in UPLOAD_GLOBS:
            for p in BASE_DIR.glob(pattern):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    yield p

    def _download_outputs(self, session_id: str, uploaded_file_ids: set[str]):
        print(f"  ↓ waiting for outputs ", end="", flush=True)
        files = []
        for _ in range(15):
            files = list(self.client.beta.files.list(
                scope_id=session_id,
                betas=["managed-agents-2026-04-01"],
            ))
            if any(f.id not in uploaded_file_ids for f in files):
                break
            print(".", end="", flush=True)
            time.sleep(2)
        print(flush=True)

        if not files:
            print("  ✗ no output files found")
            return

        repo_root = BASE_DIR.resolve()

        tar_entry = next(
            (f for f in files
             if f.filename == "outputs.tar.gz" and f.id not in uploaded_file_ids),
            None,
        )
        if tar_entry:
            print(f"  ↓ extracting outputs.tar.gz", flush=True)
            self._extract_tar(tar_entry, repo_root)
            return

        print("  ⚠  no outputs.tar.gz — falling back to basename download")

        JUNK_SUFFIXES  = (".pyc", ".pyo")
        JUNK_NAMES     = {"CACHEDIR.TAG", "lastfailed", "nodeids"}
        JUNK_FRAGMENTS = ("__pycache__", ".pytest_cache", "-pytest-")

        downloaded, skipped = 0, 0
        for f in files:
            if f.id in uploaded_file_ids:
                continue

            rel = f.filename or ""
            if rel.startswith(f"{OUTPUT_MOUNT}/"):
                rel = rel[len(OUTPUT_MOUNT) + 1:]
            rel = rel.lstrip("/")
            if not rel:
                skipped += 1
                continue

            if (rel in JUNK_NAMES
                    or any(rel.endswith(s) for s in JUNK_SUFFIXES)
                    or any(s in rel for s in JUNK_FRAGMENTS)):
                skipped += 1
                continue

            if rel in PROTECTED_PATHS or any(rel.startswith(p) for p in PROTECTED_PREFIXES):
                skipped += 1
                continue

            local_path = (BASE_DIR / rel).resolve()
            try:
                local_path.relative_to(repo_root)
            except ValueError:
                skipped += 1
                continue

            local_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.client.beta.files.download(f.id).write_to_file(str(local_path))
                downloaded += 1
            except anthropic.BadRequestError:
                skipped += 1

        print(f"  ↓ {downloaded} files saved, {skipped} skipped")

    def _extract_tar(self, tar_entry, repo_root: Path):
        import tarfile
        import tempfile

        JUNK_SUFFIXES  = (".pyc", ".pyo")
        JUNK_FRAGMENTS = ("__pycache__", ".pytest_cache", "-pytest-", "CACHEDIR.TAG")

        tmp_path = None
        downloaded = skipped = 0
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp_path = tmp.name
            self.client.beta.files.download(tar_entry.id).write_to_file(tmp_path)

            with tarfile.open(tmp_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if not member.isfile():
                        continue
                    rel = member.name.removeprefix("./").lstrip("/")
                    if not rel or rel == "outputs.tar.gz":
                        continue

                    if (any(rel.endswith(s) for s in JUNK_SUFFIXES)
                            or any(s in rel for s in JUNK_FRAGMENTS)):
                        skipped += 1
                        continue

                    if rel in PROTECTED_PATHS or any(rel.startswith(p) for p in PROTECTED_PREFIXES):
                        print(f"  → skipped (protected: {rel})")
                        skipped += 1
                        continue

                    local_path = (BASE_DIR / rel).resolve()
                    try:
                        local_path.relative_to(repo_root)
                    except ValueError:
                        print(f"  → refused (outside repo: {rel})")
                        skipped += 1
                        continue

                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    f_in = tar.extractfile(member)
                    if f_in:
                        local_path.write_bytes(f_in.read())
                        downloaded += 1

            print(f"  ↓ {downloaded} files extracted, {skipped} skipped")
        except Exception as exc:
            print(f"  ✗ tar extraction error: {exc}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception as exc:
            print(f"  cleanup warning: {exc}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    (BASE_DIR / "handoff").mkdir(exist_ok=True)

    load_dotenv(BASE_DIR / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not found in .env or environment.")
    client = anthropic.Anthropic(api_key=api_key)

    print("Ensuring managed agents…")
    ids = ManagedSetup(client).ensure()
    print(f"Environment: {ids['environment_id']}")
    for role, aid in ids["agent_ids"].items():
        print(f"  {role:<14} → {aid}")
    print()

    runner = ManagedRunner(client, ids)

    sm = StateMachine(
        agent_runner       = runner.agent_runner,
        orchestrator_agent = runner.orchestrator_agent,
    )
    sm.run_session()


if __name__ == "__main__":
    main()
