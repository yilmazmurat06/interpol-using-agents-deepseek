"""
live_test_runner.py — Local Docker + Pytest runner
---------------------------------------------------
Runs on the ORCHESTRATOR machine (not inside a managed agent sandbox).
This is the core of Option B: the orchestrator can reach localhost, managed
agents cannot. So this module handles:

  1. docker compose up --build -d
  2. Poll until all services are healthy (or timeout)
  3. pip install -r tests/requirements.txt (Playwright etc.)
  4. playwright install chromium --with-deps (idempotent)
  5. BASE_URL=http://localhost:<port> pytest tests/ --tb=short -v
  6. docker compose down
  7. Write results to handoff/live-test-results.md
  8. Return structured result dict

If Docker is not available or docker-compose.yml is missing, returns a
"skipped" result — this is not treated as a failure by the state machine.
"""

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path


class LocalLiveTestRunner:
    """
    Runs the full Docker stack + pytest suite locally.
    Used by orchestrator.py after DevOps has produced docker-compose.yml.
    """

    HEALTH_POLL_INTERVAL = 10   # seconds between health checks
    HEALTH_TIMEOUT       = 180  # total seconds to wait for all services healthy
    PYTEST_TIMEOUT       = 300  # seconds before pytest is killed

    def __init__(self, base_dir: Path, flask_port: int | None = None):
        self.base_dir   = base_dir.resolve()
        self.flask_port = flask_port or self._detect_flask_port()
        self.compose    = ["docker", "compose", "-f", str(base_dir / "docker-compose.yml")]
        self.handoff    = base_dir / "handoff" / "live-test-results.md"

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Returns:
            {
                "success": bool,     # True = all tests passed
                "skipped": bool,     # True = docker/compose unavailable
                "reason":  str,      # human-readable one-liner
                "output":  str,      # full captured output (for handoff file)
            }
        """
        result = self._run_impl()
        self._write_handoff(result)
        return result

    # ── Implementation ────────────────────────────────────────────────────────

    def _run_impl(self) -> dict:
        ts = lambda: datetime.now().strftime("[%H:%M:%S]")

        # Guard: docker available?
        if not self._cmd_exists("docker"):
            return self._skip("docker not found on PATH — live tests skipped")

        # Guard: docker-compose.yml exists?
        if not (self.base_dir / "docker-compose.yml").exists():
            return self._skip("docker-compose.yml not found — DevOps step may not have run yet")

        log_lines: list[str] = []

        def log(msg: str):
            line = f"{ts()} {msg}"
            print(line)
            log_lines.append(line)

        # ── Tear down any leftover stack ──────────────────────────────────────
        log("Tearing down any existing stack...")
        self._run_cmd([*self.compose, "down", "--remove-orphans"], capture=True)

        # ── Build + start ─────────────────────────────────────────────────────
        log("Building and starting stack...")
        build_result = self._run_cmd(
            [*self.compose, "up", "--build", "-d"],
            capture=True, timeout=600,
        )
        log_lines.extend(build_result.get("output", "").splitlines())
        if build_result["returncode"] != 0:
            self._teardown(log)
            return self._fail(
                "docker compose up --build failed",
                "\n".join(log_lines),
            )

        # ── Wait for healthy ──────────────────────────────────────────────────
        log(f"Waiting for services to be healthy (timeout={self.HEALTH_TIMEOUT}s)...")
        healthy, health_log = self._wait_healthy()
        log_lines.extend(health_log)
        if not healthy:
            self._teardown(log)
            return self._fail("Services did not become healthy", "\n".join(log_lines))

        log("All services healthy ✓")

        # ── Install test dependencies ─────────────────────────────────────────
        test_req = self.base_dir / "tests" / "requirements.txt"
        if test_req.exists():
            log("Installing test dependencies...")
            pip = self._run_cmd(
                ["pip", "install", "-r", str(test_req)],
                capture=True, timeout=120,
            )
            log_lines.extend(pip.get("output", "").splitlines()[-10:])  # last 10 lines only
        else:
            log("No tests/requirements.txt found — skipping test dep install")

        # ── Playwright install ────────────────────────────────────────────────
        log("Installing Playwright browser (idempotent)...")
        pw = self._run_cmd(
            ["python", "-m", "playwright", "install", "chromium", "--with-deps"],
            capture=True, timeout=300,
        )
        if pw["returncode"] != 0:
            log(f"  playwright install WARN: {pw['output'][-200:]}")

        # ── Run pytest ────────────────────────────────────────────────────────
        log(f"Running pytest (BASE_URL=http://localhost:{self.flask_port})...")
        env = {**os.environ, "BASE_URL": f"http://localhost:{self.flask_port}"}

        pytest_cmd = [
            "python", "-m", "pytest",
            str(self.base_dir / "tests"),
            "--tb=short", "-v",
            f"--timeout={self.PYTEST_TIMEOUT}",
            "--color=no",
        ]
        pytest_result = self._run_cmd(
            pytest_cmd,
            capture=True,
            timeout=self.PYTEST_TIMEOUT + 30,
            env=env,
            cwd=str(self.base_dir),
        )
        log_lines.append("\n" + "="*60 + "\n PYTEST OUTPUT\n" + "="*60)
        log_lines.extend(pytest_result["output"].splitlines())

        tests_passed = pytest_result["returncode"] == 0

        # ── Capture container logs on failure ────────────────────────────────
        if not tests_passed:
            log("Capturing container logs for debugging...")
            for svc in ["container-a", "container-b", "rabbitmq", "postgres"]:
                svc_logs = self._run_cmd(
                    [*self.compose, "logs", "--tail=50", svc],
                    capture=True, timeout=30,
                )
                if svc_logs["output"].strip():
                    log_lines.append(f"\n--- {svc} logs (last 50) ---")
                    log_lines.extend(svc_logs["output"].splitlines())

        # ── Tear down ─────────────────────────────────────────────────────────
        self._teardown(log)

        full_output = "\n".join(log_lines)
        if tests_passed:
            return {"success": True, "skipped": False, "reason": "all tests passed", "output": full_output}
        else:
            return self._fail("pytest failed — see output above", full_output)

    # ── Health polling ────────────────────────────────────────────────────────

    def _wait_healthy(self) -> tuple[bool, list[str]]:
        """Poll docker compose ps until all services are Up/healthy or timeout."""
        deadline = time.time() + self.HEALTH_TIMEOUT
        log_lines = []
        required = {"container-a", "container-b", "rabbitmq", "postgres", "minio"}
        health_required = {"rabbitmq", "postgres", "minio"}

        while time.time() < deadline:
            ps = self._run_cmd([*self.compose, "ps", "--format", "json"], capture=True)
            status_map = self._parse_ps(ps["output"])
            log_lines.append(f"  health poll: {status_map}")

            # Check for any crashed service
            for svc, state in status_map.items():
                if "exit" in state.lower() or "unhealthy" in state.lower():
                    log_lines.append(f"  FAILED: {svc} is {state}")
                    return False, log_lines

            # Check all required services are running
            all_up = all(
                any(svc in k for k in status_map)
                for svc in required
            )
            # Check healthcheck services are healthy
            health_ok = all(
                any(svc in k and "healthy" in v.lower()
                    for k, v in status_map.items())
                for svc in health_required
            )

            if all_up and health_ok:
                return True, log_lines

            time.sleep(self.HEALTH_POLL_INTERVAL)

        log_lines.append(f"  TIMEOUT after {self.HEALTH_TIMEOUT}s")
        return False, log_lines

    @staticmethod
    def _parse_ps(output: str) -> dict[str, str]:
        """Parse docker compose ps output into {service: status} dict."""
        import json as _json
        result = {}
        for line in output.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
                name   = obj.get("Service", obj.get("Name", "?"))
                state  = obj.get("State",   obj.get("Status", "?"))
                health = obj.get("Health",  "")
                result[name] = f"{state}/{health}" if health else state
            except Exception:
                # Fallback: plaintext "container-a   Up 10s"
                parts = line.split()
                if len(parts) >= 2:
                    result[parts[0]] = " ".join(parts[1:])
        return result

    # ── Teardown ──────────────────────────────────────────────────────────────

    def _teardown(self, log):
        log("Tearing down stack...")
        self._run_cmd([*self.compose, "down", "--remove-orphans"], capture=True, timeout=60)

    # ── Handoff writer ────────────────────────────────────────────────────────

    def _write_handoff(self, result: dict):
        self.handoff.parent.mkdir(parents=True, exist_ok=True)
        status = "✅ PASSED" if result["success"] else ("⚠️ SKIPPED" if result["skipped"] else "❌ FAILED")
        content = f"""# Live Test Results
**Status:** {status}
**Date:** {datetime.now().strftime("%Y-%m-%d %H:%M")}
**Reason:** {result["reason"]}
**Flask port:** {self.flask_port}

## Full Output

```
{result["output"]}
```
"""
        self.handoff.write_text(content, encoding="utf-8")
        print(f"\n  live-test-results.md written → {self.handoff}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _detect_flask_port(self) -> int:
        env_file = self.base_dir / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("FLASK_PORT="):
                    try:
                        return int(line.split("=", 1)[1].strip().strip('"'))
                    except ValueError:
                        pass
        return 8080

    @staticmethod
    def _cmd_exists(name: str) -> bool:
        import shutil
        return shutil.which(name) is not None

    @staticmethod
    def _run_cmd(
        cmd: list[str],
        capture: bool = False,
        timeout: int = 120,
        env: dict | None = None,
        cwd: str | None = None,
    ) -> dict:
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                timeout=timeout,
                env=env,
                cwd=cwd,
            )
            output = ""
            if capture:
                output = (result.stdout or "") + (result.stderr or "")
            return {"returncode": result.returncode, "output": output}
        except subprocess.TimeoutExpired:
            return {"returncode": -1, "output": f"TIMEOUT after {timeout}s"}
        except FileNotFoundError as e:
            return {"returncode": -1, "output": str(e)}

    @staticmethod
    def _skip(reason: str) -> dict:
        print(f"  live tests SKIPPED: {reason}")
        return {"success": True, "skipped": True, "reason": reason, "output": f"SKIPPED: {reason}"}

    @staticmethod
    def _fail(reason: str, output: str) -> dict:
        return {"success": False, "skipped": False, "reason": reason, "output": output}
