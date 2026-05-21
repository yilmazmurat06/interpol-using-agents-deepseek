"""
state_machine.py — Pure deterministic pipeline controller
----------------------------------------------------------
No LLM calls here. Every decision is code-level logic.
Responsibilities:
  - Read / update feature-list.json
  - Decide which agent runs next based on current state
  - Count iterations, enforce MAX_ITERATIONS
  - Manage handoff files between agents
  - Write progress log entries
  - Call orchestrator_agent ONLY for: session start, session end, blocked features
"""

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

_W = 64  # separator width

def _section(label: str = ""):
    """Print a labelled section separator."""
    if label:
        pad  = max(_W - len(label) - 4, 2)
        left = pad // 2
        print(f"\n{'─' * left}  {label}  {'─' * (pad - left)}")
    else:
        print(f"\n{'─' * _W}")


# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR     = Path(__file__).parent
FEATURE_LIST = BASE_DIR / "feature-list.json"
PROGRESS_LOG = BASE_DIR / "claude-progress.txt"
HANDOFF_DIR  = BASE_DIR / "handoff"

MAX_ITERATIONS = 3
MAX_REVIEW_ROUNDS = 3   # cross-functional review loop: max rounds before fallthrough to QA


# ── Feature Manager ───────────────────────────────────────────────────────────

class FeatureManager:
    """
    Single source of truth for feature states.
    Only StateMachine calls update_status() — no agent touches this directly.
    """

    def __init__(self):
        with open(FEATURE_LIST, "r", encoding="utf-8") as f:
            self.data = json.load(f)

    def _save(self):
        self.data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(FEATURE_LIST, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def pending(self) -> list:
        return sorted(
            [f for f in self.data["features"] if f["status"] == "pending"],
            key=lambda f: f["priority"]
        )

    def in_progress(self) -> list:
        return sorted(
            [f for f in self.data["features"] if f["status"] == "in-progress"],
            key=lambda f: f["priority"]
        )

    def blocked(self) -> list:
        return [f for f in self.data["features"] if f["status"] == "blocked"]

    def all_done(self) -> bool:
        return all(
            f["status"] in ("done", "blocked")
            for f in self.data["features"]
        )

    def get(self, feature_id: str) -> dict | None:
        return next(
            (f for f in self.data["features"] if f["id"] == feature_id), None
        )

    def update_status(self, feature_id: str, status: str,
                      qa_verdict: str | None = None, git_commit: str | None = None):
        for f in self.data["features"]:
            if f["id"] == feature_id:
                f["status"] = status
                if qa_verdict:
                    f["qa_verdict"] = qa_verdict
                if git_commit:
                    f["git_commit"] = git_commit
        self._save()

    def increment_iteration(self, feature_id: str):
        for f in self.data["features"]:
            if f["id"] == feature_id:
                f["iteration"] += 1
        self._save()

    def get_iteration(self, feature_id: str) -> int:
        f = self.get(feature_id)
        return f["iteration"] if f else 0

    def summary(self) -> str:
        return "\n".join(
            f"[{f['status'].upper():>16}]  {f['id']}  {f['description']}"
            for f in self.data["features"]
        )


# ── Handoff Manager ───────────────────────────────────────────────────────────

class HandoffManager:
    """
    File-based communication between agents.
    Agents never write to each other directly.
    StateMachine routes messages by writing/reading these files.
    """

    def __init__(self):
        HANDOFF_DIR.mkdir(exist_ok=True)

    def _path(self, sender: str, receiver: str) -> Path:
        return HANDOFF_DIR / f"{sender}-to-{receiver}.md"

    def write(self, sender: str, receiver: str, feature_id: str,
              iteration: int, content: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        body = (
            f"# Handoff: {sender} → {receiver}\n"
            f"**Feature:** {feature_id}\n"
            f"**Date:** {timestamp}\n"
            f"**Iteration:** {iteration}\n\n"
            f"{content}"
        )
        self._path(sender, receiver).write_text(body, encoding="utf-8")

    def read(self, sender: str, receiver: str) -> str:
        path = self._path(sender, receiver)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def exists(self, sender: str, receiver: str) -> bool:
        return self._path(sender, receiver).exists()

    def clear(self, sender: str, receiver: str):
        path = self._path(sender, receiver)
        if path.exists():
            path.unlink()


# ── Progress Logger ───────────────────────────────────────────────────────────

def log(agent: str, feature_id: str, status: str,
        summary: str, next_step: str = ""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = (
        f"[{timestamp}] [{agent.upper()}] [{feature_id}] [{status}]\n"
        f"Summary: {summary}\n"
        f"Next step: {next_step}\n"
        f"---\n"
    )
    with open(PROGRESS_LOG, "a", encoding="utf-8") as f:
        f.write(entry)


# ── State Machine ─────────────────────────────────────────────────────────────

class StateMachine:
    """
    Pure deterministic controller.
    Drives the feature pipeline without making any LLM calls itself.
    Calls agent_runner (from orchestrator.py) to execute each step.
    Calls orchestrator_agent only at session boundaries and on blocked features.
    """

    def __init__(self, agent_runner, orchestrator_agent):
        """
        agent_runner       — callable(agent_name, prompt) → str
                             provided by orchestrator.py
        orchestrator_agent — callable(prompt) → str
                             the LLM orchestrator agent, called sparingly
        """
        self.run_agent = agent_runner
        self.orc_agent = orchestrator_agent
        self.features  = FeatureManager()
        self.handoff   = HandoffManager()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _trunc(text: str, chars: int = 8_000) -> str:
        """Trim long handoff/output text to keep prompts token-efficient.
        Keeps the tail — the most recent content is most actionable."""
        if len(text) <= chars:
            return text
        return f"[…truncated to last {chars} chars…]\n\n" + text[-chars:]

    # ── Prompt builders ───────────────────────────────────────────────────────

    def _task_prompt(self, feature: dict, extra: str = "") -> str:
        criteria = "\n".join(f"  - {c}" for c in feature["done_criteria"])
        prompt = (
            f"## Your current task\n\n"
            f"**Feature:** {feature['id']} — {feature['description']}\n"
            f"**Iteration:** {feature['iteration']}\n\n"
            f"**Done criteria (every item must pass):**\n{criteria}\n\n"
            f"**Current project state:**\n{self.features.summary()}\n"
        )
        if extra:
            prompt += f"\n\n{extra}"
        return prompt

    # ── Individual steps ──────────────────────────────────────────────────────

    def _step_developer(self, feature: dict, bug_report: str = "") -> str:
        extra    = f"## Bug report from QA\n\n{bug_report}" if bug_report else ""
        response = self.run_agent("developer", self._task_prompt(feature, extra))

        self.handoff.write(
            sender="dev", receiver="qa",
            feature_id=feature["id"],
            iteration=feature["iteration"],
            content=response,
        )
        self.features.update_status(feature["id"], "dev-complete")
        log("developer", feature["id"], "dev-complete",
            "Feature implemented.", "QA review")
        return response

    def _step_qa(self, feature: dict, source: str = "dev") -> tuple[str, str]:
        handoff  = self._trunc(self.handoff.read(source, "qa"), chars=8_000)
        response = self.run_agent(
            "qa",
            self._task_prompt(
                feature,
                f"## Incoming handoff from {source}\n\n{handoff}\n\n"
                "Review carefully. Test every done criterion. Be skeptical.\n"
                "End your response with exactly one of:\n"
                "`Verdict: PASS` or `Verdict: FAIL`"
            )
        )

        # Deterministic verdict parsing — no ambiguity allowed
        verdict  = "pass" if "verdict: pass" in response.lower().split("\n")[-1] else "fail"
        receiver = "devops" if verdict == "pass" else "dev"

        self.handoff.write(
            sender="qa", receiver=receiver,
            feature_id=feature["id"],
            iteration=feature["iteration"],
            content=response,
        )
        log("qa", feature["id"], f"qa-{verdict}",
            f"Verdict: {verdict}",
            "DevOps" if verdict == "pass" else "Developer fix")
        return response, verdict

    def _step_devops(self, feature: dict) -> str:
        handoff  = self._trunc(self.handoff.read("qa", "devops"), chars=8_000)
        response = self.run_agent(
            "devops",
            self._task_prompt(
                feature,
                f"## QA passed — integrate into Docker\n\n{handoff}"
            )
        )
        self.handoff.write(
            sender="devops", receiver="qa",
            feature_id=feature["id"],
            iteration=feature["iteration"],
            content=response,
        )
        self.features.update_status(feature["id"], "devops-complete")
        log("devops", feature["id"], "devops-complete",
            "Docker integration complete.", "Final QA verify")
        return response

    def _step_local_docker_tests(self) -> dict:
        """
        Run the full Docker stack + pytest on the LOCAL machine (where Docker is
        available). Called by the orchestrator Python process, not by any agent.

        Writes handoff/docker-test-results.md so the QA agent can read the
        authoritative results and incorporate them into its evidence ledger.

        Returns: {"success": bool, "skipped": bool, "reason": str, "output": str}
        """
        results_file = HANDOFF_DIR / "docker-test-results.md"
        script       = BASE_DIR / ".claude/skills/interpol-qa/scripts/run_docker_tests.sh"

        def _write(status: str, reason: str, output: str = "") -> dict:
            icon = {"SKIPPED": "⚠️", "PASSED": "✅", "FAILED": "❌", "TIMEOUT": "❌", "ERROR": "❌"}
            body = (
                f"# Docker Test Results\n\n"
                f"{icon.get(status, '?')} {status}\n\n"
                f"Reason: {reason}\n\n"
                + (f"```\n{output}\n```\n" if output else "")
            )
            results_file.write_text(body, encoding="utf-8")
            success = status == "PASSED"
            skipped = status == "SKIPPED"
            return {"success": success or skipped, "skipped": skipped,
                    "reason": reason, "output": output}

        if os.environ.get("SKIP_DOCKER_TESTS"):
            reason = "SKIP_DOCKER_TESTS env var is set"
            print(f"\n  [{ts()}] Docker tests  SKIPPED — {reason}")
            return _write("SKIPPED", reason)

        if not shutil.which("docker"):
            reason = "docker not found on this machine"
            print(f"\n  [{ts()}] Docker tests  SKIPPED — {reason}")
            return _write("SKIPPED", reason)

        print(f"\n  [{ts()}] Docker tests  running locally…")
        try:
            proc = subprocess.run(
                ["bash", str(script)],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            reason = "timed out after 10 minutes"
            print(f"\n  [{ts()}] Docker tests  TIMEOUT")
            log("local_docker", "ALL", "docker-timeout", reason, "QA Docker verdict")
            return _write("TIMEOUT", reason)
        except Exception as exc:
            reason = f"runner error: {exc}"
            print(f"\n  [{ts()}] Docker tests  ERROR — {exc}")
            log("local_docker", "ALL", "docker-error", reason, "QA Docker verdict")
            return _write("ERROR", reason)

        output  = (proc.stdout or "") + (proc.stderr or "")
        success = proc.returncode == 0
        status  = "PASSED" if success else "FAILED"
        reason  = f"exit code {proc.returncode}"
        print(f"\n  [{ts()}] Docker tests  {status}")
        log("local_docker", "ALL", f"docker-{status.lower()}", reason, "QA Docker verdict")
        return _write(status, reason, output)

    def _step_qa_docker_verdict(self, features: list, docker_result: dict) -> tuple[str, str]:
        """
        QA agent reads the local Docker test results (written to
        handoff/docker-test-results.md) and issues a final verdict.
        Returns (response, verdict).
        """
        combined = "\n\n".join(
            f"### {f['id']}: {f['description']}\n"
            + "\n".join(f"  - {c}" for c in f["done_criteria"])
            for f in features
        )
        devops_handoff = self._trunc(self.handoff.read("devops", "qa"), chars=6_000)

        if docker_result.get("skipped"):
            docker_section = (
                "## Docker test results\n\n"
                f"⚠️ SKIPPED — {docker_result['reason']}\n\n"
                "Docker was not run on the local machine. "
                "Issue verdict based on static analysis only."
            )
        elif docker_result["success"]:
            tail = docker_result["output"][-2_000:]
            docker_section = (
                "## Docker test results\n\n"
                "✅ ALL TESTS PASSED\n\n"
                f"```\n{tail}\n```"
            )
        else:
            tail = docker_result["output"][-3_000:]
            docker_section = (
                "## Docker test results\n\n"
                "❌ TESTS FAILED\n\n"
                f"Reason: {docker_result['reason']}\n\n"
                f"```\n{tail}\n```\n\n"
                "These are REAL RUNTIME FAILURES against the live Docker stack. "
                "Your verdict MUST be FAIL unless the failures are pre-existing and "
                "completely unrelated to the features under test."
            )

        prompt = (
            f"## Your task: Docker integration verdict\n\n"
            f"The orchestrator ran the Docker stack and pytest on the local machine. "
            f"The full output is in /workspace/repo/handoff/docker-test-results.md — read it.\n\n"
            f"Your job:\n"
            f"  1. Run `bash /workspace/repo/.claude/skills/interpol-qa/scripts/run_all.sh` "
            f"for static checks\n"
            f"  2. Incorporate the Docker test results below into your Evidence ledger\n"
            f"  3. Issue your final verdict\n\n"
            f"{docker_section}\n\n"
            f"## DevOps handoff\n\n{devops_handoff}\n\n"
            f"## Features under test\n\n{combined}\n\n"
            f"End your response with EXACTLY one of:\n"
            f"  Verdict: PASS\n"
            f"  Verdict: FAIL"
        )
        response = self.run_agent("qa", prompt)
        verdict  = "pass" if "verdict: pass" in response.lower().split("\n")[-1] else "fail"
        self.handoff.write("qa", "done" if verdict == "pass" else "dev",
                           "DOCKER-VERDICT", 0, response)
        log("qa", "DOCKER", f"qa-docker-{verdict}",
            f"Docker integration verdict: {verdict}",
            "Done" if verdict == "pass" else "Developer fix")
        return response, verdict

    def _block_feature(self, feature: dict, reason: str):
        """Mark a feature as blocked and ask the orchestrator agent to analyse it."""
        self.features.update_status(feature["id"], "blocked", qa_verdict="fail")
        log("state_machine", feature["id"], "blocked", reason, "Manual review needed")
        print(f"\n{ts()} ⚠  {feature['id']} BLOCKED — {reason}")

        # Only here does the state machine call the orchestrator LLM
        analysis = self.orc_agent(
            f"Feature {feature['id']} is blocked.\n"
            f"Reason: {reason}\n\n"
            f"Last QA report:\n{self.handoff.read('qa', 'dev')}\n\n"
            "Analyse the situation and suggest a recovery path."
        )
        log("orchestrator_agent", feature["id"], "blocked-analysis",
            analysis, "See analysis above")
        print(f"\n── Orchestrator analysis ────────────────────────────")
        print(analysis)
        print("─────────────────────────────────────────────────────")

    # ── Feature pipeline ──────────────────────────────────────────────────────

    def run_feature(self, feature: dict):
        """
        Deterministic pipeline for one feature.
        State transitions:
          pending → in-progress → dev-complete
            → qa loop (max MAX_ITERATIONS)
              → qa-pass → devops-complete → done
              → blocked (on iteration exhaustion or Docker QA fail)
        """
        fid = feature["id"]
        _section(f"{fid}  {feature['description']}")

        self.features.update_status(fid, "in-progress")

        # Step 1 — Developer builds
        print(f"\n  [{ts()}] {fid}  Developer")
        self._step_developer(feature)

        # Step 2 — QA fix loop
        qa_passed = False
        for attempt in range(1, MAX_ITERATIONS + 1):
            print(f"\n  [{ts()}] {fid}  QA  attempt {attempt}/{MAX_ITERATIONS}")
            _, verdict = self._step_qa(feature, source="dev")
            print(f"  verdict: {verdict.upper()}")

            if verdict == "pass":
                self.features.update_status(fid, "qa-pass", qa_verdict="pass")
                qa_passed = True
                break

            if attempt < MAX_ITERATIONS:
                bug_report = self.handoff.read("qa", "dev")
                print(f"\n  [{ts()}] {fid}  Developer  fix #{attempt}")
                self.features.increment_iteration(fid)
                self._step_developer(feature, bug_report=bug_report)

        if not qa_passed:
            self._block_feature(
                feature,
                f"QA failed after {MAX_ITERATIONS} iterations."
            )
            return

        # Step 3 — DevOps integrates (Dockerfiles, docker-compose, .env.example)
        print(f"\n  [{ts()}] {fid}  DevOps")
        self._step_devops(feature)

        self.features.update_status(fid, "done")
        log("state_machine", fid, "done", "Feature complete.", "Next feature")
        print(f"\n  ✓  {fid} DONE")

    def _step_research(self, features: list):
        """
        Probe every external system the project depends on BEFORE any code is written.
        Output: research/<target>-constraints.md per target system, consumed by
        downstream developer/devops/qa agents as authoritative ground truth.
        """
        combined = "\n".join(f"  - {f['id']}: {f['description']}" for f in features)
        prompt = (
            "## Your task: probe every external system this project depends on\n\n"
            "Identify the external systems from CLAUDE.md (APIs, websites, CDNs, "
            "third-party services). For each, run the full probe checklist from "
            "the research SKILL.md and write a constraints document to "
            "/mnt/session/outputs/research/<target>-constraints.md.\n\n"
            f"## Features that will be built from your findings\n{combined}\n\n"
            "Every claim in your output MUST be backed by an actual probe you executed. "
            "No speculation. No copy-pasted documentation."
        )
        response = self.run_agent("research", prompt)
        log("research", "ALL", "research-complete",
            "External systems probed; constraints documents produced.",
            "Developer batch")
        return response

    # ── Cross-functional review loop ──────────────────────────────────────────

    def _step_cross_review_loop(self, features: list):
        """
        Cross-functional review: DevOps + QA inspect the developer's code (NOT
        their own output) and produce findings. Developer addresses findings.
        Loop stops on convergence (zero findings from both reviewers) or after
        MAX_REVIEW_ROUNDS, whichever comes first.

        This runs BEFORE the formal QA verdict loop — its job is to catch issues
        early so the QA batch has cleaner code to grade.
        """
        _section(f"CROSS-REVIEW LOOP  max {MAX_REVIEW_ROUNDS} rounds")

        for round_num in range(1, MAX_REVIEW_ROUNDS + 1):
            print(f"\n  [{ts()}] cross-review round {round_num}/{MAX_REVIEW_ROUNDS}")

            print(f"  [{ts()}] reviewer: devops")
            devops_findings = self._review_pass("devops", features, round_num)

            print(f"  [{ts()}] reviewer: qa")
            qa_findings = self._review_pass("qa", features, round_num)

            devops_clean = self._is_clean(devops_findings)
            qa_clean     = self._is_clean(qa_findings)

            print(f"  devops={'clean' if devops_clean else 'has findings'}  "
                  f"qa={'clean' if qa_clean else 'has findings'}")

            if devops_clean and qa_clean:
                print(f"  ✓ cross-review converged at round {round_num}")
                log("state_machine", "ALL", f"cross-review-converged",
                    f"Converged at round {round_num}.", "QA batch")
                return

            print(f"  [{ts()}] developer fix from cross-review findings")
            self._developer_fix_from_reviews(features, devops_findings, qa_findings, round_num)

        print(f"  ⚠  cross-review hit max rounds ({MAX_REVIEW_ROUNDS}) — proceeding to QA")
        log("state_machine", "ALL", "cross-review-max-rounds",
            f"Hit MAX_REVIEW_ROUNDS={MAX_REVIEW_ROUNDS}.", "QA batch")

    def _review_pass(self, reviewer_role: str, features: list, round_num: int) -> str:
        """
        Run a reviewer agent in REVIEW-ONLY mode. The agent reads source code
        and produces a findings list — it does NOT write its normal output
        artifacts. Returns the findings text.
        """
        combined = "\n".join(f"  - {f['id']}: {f['description']}" for f in features)
        prompt = (
            f"## Cross-functional review — REVIEW ONLY MODE (round {round_num}/{MAX_REVIEW_ROUNDS})\n\n"
            f"You are reviewing the developer's source code from your role's perspective.\n"
            f"**You are NOT writing any of your normal output artifacts in this pass.**\n"
            f"Your single deliverable is a findings list in your final message.\n\n"
            f"## What to review\n"
            f"Read every file under /workspace/repo/container_a/ and /workspace/repo/container_b/.\n"
            f"Check it against:\n"
            f"  - Every done_criterion for the features listed below\n"
            f"  - Every 'Hard Rule' in /workspace/repo/research/*-constraints.md\n"
            f"  - Every entry in CLAUDE.md → Engineering Decisions\n"
            f"  - Your role's domain (devops: infra/dockerization readiness; qa: correctness & test coverage)\n\n"
            f"## Features in scope\n{combined}\n\n"
            f"## Output format — final message MUST end with this exact block\n"
            f"```\n"
            f"## Findings\n"
            f"- severity=<critical|high|medium|low>  file=<path:line>  issue=<one line>  rule=<cite or N/A>  fix=<one line>\n"
            f"- ...\n"
            f"```\n\n"
            f"If you find no real issues, end the message with EXACTLY this block — nothing else after it:\n"
            f"```\n"
            f"## Findings\n"
            f"(none)\n"
            f"```\n\n"
            f"## Skepticism contract\n"
            f"- Empty findings is a VALID outcome. Do NOT invent issues to look thorough.\n"
            f"- Every finding must cite a real file:line and a real rule or done_criterion.\n"
            f"- 'looks suboptimal' is not a finding. 'Violates Hard Rule X at file:line' is a finding.\n"
            f"- Self-bias is your enemy from both directions: do not under-report (defending the developer) "
            f"and do not over-report (proving you're useful)."
        )
        response = self.run_agent(reviewer_role, prompt)
        log(f"{reviewer_role}-review", "ALL", f"cross-review-round-{round_num}",
            "Cross-functional review pass complete.",
            "Convergence check")
        return response

    @staticmethod
    def _is_clean(findings_text: str) -> bool:
        """
        Detect 'no findings' output. The reviewer prompt mandates a `## Findings`
        block ending the message; an `(none)` line in that block means clean.
        """
        if not findings_text:
            return True
        # Look in the last 500 chars where the Findings block lives
        tail = findings_text[-500:].lower()
        return "(none)" in tail and "## findings" in tail

    def _developer_fix_from_reviews(self, features: list, devops_findings: str,
                                    qa_findings: str, round_num: int):
        """
        Developer addresses consolidated cross-review findings in a single fix pass.
        """
        combined = "\n\n".join(
            f"### {f['id']}: {f['description']}" for f in features
        )
        prompt = (
            f"## Cross-review fix pass — round {round_num}/{MAX_REVIEW_ROUNDS}\n\n"
            f"Two reviewers independently inspected your code from different perspectives. "
            f"Address every finding. Read each cited file, make the fix, re-write the file.\n\n"
            f"## DevOps reviewer findings\n\n{devops_findings}\n\n"
            f"---\n\n"
            f"## QA reviewer findings\n\n{qa_findings}\n\n"
            f"---\n\n"
            f"## Features in scope\n\n{combined}\n\n"
            f"## Output\n"
            f"For each finding addressed, write one line:\n"
            f"  - <reviewer> <file:line> — what you changed.\n"
            f"If you disagree with a finding, write one line explaining why instead of changing the file. "
            f"Be honest — if the finding is wrong, say so."
        )
        response = self.run_agent("developer", prompt)
        log("developer", "ALL", f"cross-review-fix-{round_num}",
            "Developer addressed cross-review findings.",
            "Next review round")
        self.handoff.write("dev", "review", "FIX", round_num, response)

    def _step_developer_batch(self, features: list):
        """Send all pending developer features to the developer in one session."""
        combined = "\n\n".join(
            f"### {f['id']}: {f['description']}\n"
            + "\n".join(f"  - {c}" for c in f["done_criteria"])
            for f in features
        )
        prompt = (
            f"## Your task: implement ALL of the following features in one pass\n\n"
            f"{combined}\n\n"
            f"**Project state:**\n{self.features.summary()}\n\n"
            "Write all code now. Cover every done criterion for every feature listed."
        )
        response = self.run_agent("developer", prompt)
        for f in features:
            self.features.update_status(f["id"], "dev-complete")
            log("developer", f["id"], "dev-complete", "Batched implementation.", "QA review")
        self.handoff.write("dev", "qa", "BATCH", 0, response)

    def _step_developer_fix_batch(self, features: list, bug_report: str):
        """Send all dev features back for a fix pass after a QA batch failure."""
        combined = "\n\n".join(
            f"### {f['id']}: {f['description']}\n"
            + "\n".join(f"  - {c}" for c in f["done_criteria"])
            for f in features
        )
        prompt = (
            f"## Fix required — QA batch failed\n\n"
            f"Address every issue in the bug report below, then re-write all affected files.\n\n"
            f"## Bug report from QA\n\n{bug_report}\n\n"
            f"## Features covered\n\n{combined}\n\n"
            f"**Project state:**\n{self.features.summary()}"
        )
        response = self.run_agent("developer", prompt)
        for f in features:
            self.features.increment_iteration(f["id"])
            log("developer", f["id"], "dev-complete", "Fix iteration.", "QA re-review")
        self.handoff.write("dev", "qa", "BATCH-FIX", 0, response)

    def _step_qa_batch(self, features: list) -> tuple[str, str]:
        """Run QA on all developer features in one session. Returns (response, verdict)."""
        combined = "\n\n".join(
            f"### {f['id']}: {f['description']}\n"
            + "\n".join(f"  - {c}" for c in f["done_criteria"])
            for f in features
        )
        prompt = (
            f"## Your task: validate ALL of the following features\n\n"
            f"{combined}\n\n"
            f"**Project state:**\n{self.features.summary()}\n\n"
            "Read every source file, write and run tests, audit requirements.txt.\n"
            "Cover every done criterion for every feature listed.\n"
            "End your response with EXACTLY one of:\n"
            "  Verdict: PASS\n"
            "  Verdict: FAIL"
        )
        response = self.run_agent("qa", prompt)
        verdict = "pass" if "verdict: pass" in response.lower().split("\n")[-1] else "fail"
        receiver = "devops" if verdict == "pass" else "dev"
        self.handoff.write("qa", receiver, "BATCH", 0, response)
        log("qa", "BATCH", f"qa-{verdict}", f"Batch verdict: {verdict}",
            "DevOps batch" if verdict == "pass" else "Developer fix batch")
        return response, verdict

    def _step_devops_batch(self, features: list):
        """Run DevOps for all features in one session."""
        combined = "\n\n".join(
            f"### {f['id']}: {f['description']}" for f in features
        )
        handoff = self._trunc(self.handoff.read("qa", "devops"), chars=8_000)
        prompt = (
            f"## Your task: dockerize and document the full system\n\n"
            f"Features covered:\n{combined}\n\n"
            f"## QA sign-off\n\n{handoff}\n\n"
            f"**Project state:**\n{self.features.summary()}\n\n"
            "Read the source files, produce all Docker and documentation assets."
        )
        response = self.run_agent("devops", prompt)
        for f in features:
            self.features.update_status(f["id"], "devops-complete")
            log("devops", f["id"], "devops-complete", "Batched DevOps complete.", "QA Docker tests")
        self.handoff.write("devops", "qa", "BATCH", 0, response)
        print(f"\n  ✓  DevOps batch done")

    # ── Session loop ──────────────────────────────────────────────────────────

    def run_session(self):
        """
        Main session loop.
        Orchestrator LLM is called only at start and end of session,
        and when a feature is blocked.
        Everything in between is pure state machine logic.
        """
        _section(f"SESSION START  {ts()}")

        # Orchestrator agent: session plan (LLM call #1 of session)
        plan = self.orc_agent(
            f"Session starting. Current project state:\n\n"
            f"{self.features.summary()}\n\n"
            "Which features should be tackled this session and in what order? "
            "Briefly explain your reasoning."
        )
        print(plan)

        # Determine work queue — all unfinished features sorted by priority
        queue = sorted(
            [
                f for f in self.features.data["features"]
                if f["status"] not in ("done", "blocked")
            ],
            key=lambda f: f["priority"],
        )

        if not queue:
            if self.features.all_done():
                print("✓ All features complete.")
            else:
                print("⚠  No pending features. Check blocked items.")
            return

        # Docker-retry shortcut: all queued features already passed DevOps but
        # Docker tests failed (e.g. due to a bad dependency pin that was fixed
        # manually). Skip steps 0–3 and jump straight to the Docker test step.
        if all(f["status"] == "devops-complete" for f in queue):
            print(f"\n  [{ts()}] All features devops-complete — retrying Docker tests only")
            dev_features_for_fix = [f for f in queue if f.get("assigned_to") in (None, "developer")]
            docker_passed = False
            for attempt in range(1, MAX_ITERATIONS + 1):
                _section(f"LOCAL DOCKER TESTS  attempt {attempt}/{MAX_ITERATIONS}")
                docker_result = self._step_local_docker_tests()
                status = ("SKIPPED" if docker_result["skipped"]
                          else ("PASSED" if docker_result["success"] else "FAILED"))
                print(f"  {status}: {docker_result['reason']}")
                _section(f"QA DOCKER VERDICT  attempt {attempt}/{MAX_ITERATIONS}")
                _, docker_verdict = self._step_qa_docker_verdict(queue, docker_result)
                print(f"  verdict: {docker_verdict.upper()}")
                if docker_verdict == "pass":
                    for f in queue:
                        self.features.update_status(f["id"], "done")
                        log("state_machine", f["id"], "done",
                            "Feature complete — Docker tests passed.", "Next feature")
                    print(f"\n  ✓  All features DONE")
                    docker_passed = True
                    break
                if attempt < MAX_ITERATIONS and dev_features_for_fix:
                    bug_report = self.handoff.read("qa", "dev")
                    _section(f"DEVELOPER FIX BATCH  fix #{attempt}")
                    self._step_developer_fix_batch(dev_features_for_fix, bug_report)
            if not docker_passed:
                for f in queue:
                    self._block_feature(f, f"QA Docker verdict failed after {MAX_ITERATIONS} attempts.")
            summary = self.orc_agent(
                f"Session complete. Final state:\n\n{self.features.summary()}\n\n"
                "Write a concise summary: what was accomplished, "
                "what is blocked, and recommended next steps."
            )
            log("orchestrator_agent", "ALL", "session-end", summary, "Next session")
            _section(f"SESSION SUMMARY  {ts()}")
            print(summary)
            return

        dev_features    = [f for f in queue if f.get("assigned_to") in (None, "developer")]
        devops_features = [f for f in queue if f.get("assigned_to") == "devops"]

        # ── Step 0: Research — probe external systems before any code is written ─
        if dev_features:
            research_dir = BASE_DIR / "research"
            existing = list(research_dir.glob("*-constraints.md")) if research_dir.exists() else []
            if existing:
                print(f"\n  [{ts()}] research already done — {[p.name for p in existing]}")
            else:
                _section("RESEARCH")
                self._step_research(dev_features)

        # ── Step 1: Developer batch (only truly pending features) ─────────────
        pending_dev = [f for f in dev_features if f["status"] == "pending"]
        if pending_dev:
            _section("DEVELOPER BATCH")
            self._step_developer_batch(pending_dev)

        # ── Step 1.5: Cross-functional review loop (NEW) ──────────────────────
        # DevOps + QA review developer code BEFORE formal QA verdict. Catches
        # issues early so QA batch grades clean(er) code. Stops on convergence.
        if dev_features:
            self._step_cross_review_loop(dev_features)

        # ── Step 2: QA batch (all developer features) ─────────────────────────
        if dev_features:
            qa_passed = False
            for attempt in range(1, MAX_ITERATIONS + 1):
                _section(f"QA BATCH  attempt {attempt}/{MAX_ITERATIONS}")
                _, verdict = self._step_qa_batch(dev_features)
                print(f"  verdict: {verdict.upper()}")

                if verdict == "pass":
                    for f in dev_features:
                        self.features.update_status(f["id"], "qa-pass", qa_verdict="pass")
                    qa_passed = True
                    break

                if attempt < MAX_ITERATIONS:
                    bug_report = self.handoff.read("qa", "dev")
                    _section(f"DEVELOPER FIX BATCH  fix #{attempt}")
                    self._step_developer_fix_batch(dev_features, bug_report)

            if not qa_passed:
                for f in dev_features:
                    self._block_feature(f, f"QA batch failed after {MAX_ITERATIONS} iterations.")
                return

        # ── Step 3: DevOps batch (QA-passed dev features + devops-owned) ──────
        devops_targets = [f for f in dev_features if f["status"] == "qa-pass"] + devops_features
        if devops_targets:
            _section("DEVOPS BATCH")
            self._step_devops_batch(devops_targets)

        # ── Step 4a: Local Docker tests (orchestrator, runs on this machine) ────
        # The Managed Agents cloud env has no Docker daemon, so the orchestrator
        # runs docker compose + pytest locally and writes the results to
        # handoff/docker-test-results.md for the QA agent to read.
        docker_result: dict = {"success": True, "skipped": True,
                               "reason": "no devops targets", "output": ""}
        if devops_targets:
            _section("LOCAL DOCKER TESTS")
            docker_result = self._step_local_docker_tests()
            status = ("SKIPPED" if docker_result["skipped"]
                      else ("PASSED" if docker_result["success"] else "FAILED"))
            print(f"  {status}: {docker_result['reason']}")

        # ── Step 4b: QA Docker verdict with developer fix loop ────────────────
        if devops_targets:
            docker_passed = False
            for docker_attempt in range(1, MAX_ITERATIONS + 1):
                if docker_attempt > 1:
                    _section(f"LOCAL DOCKER TESTS  retry {docker_attempt}/{MAX_ITERATIONS}")
                    docker_result = self._step_local_docker_tests()
                    status = ("SKIPPED" if docker_result["skipped"]
                              else ("PASSED" if docker_result["success"] else "FAILED"))
                    print(f"  {status}: {docker_result['reason']}")
                _section(f"QA DOCKER VERDICT  attempt {docker_attempt}/{MAX_ITERATIONS}")
                _, docker_verdict = self._step_qa_docker_verdict(devops_targets, docker_result)
                print(f"  verdict: {docker_verdict.upper()}")
                if docker_verdict == "pass":
                    for f in devops_targets:
                        self.features.update_status(f["id"], "done")
                        log("state_machine", f["id"], "done",
                            "Feature complete — Docker tests passed.", "Next feature")
                    print(f"\n  ✓  All features DONE")
                    docker_passed = True
                    break
                if docker_attempt < MAX_ITERATIONS and dev_features:
                    bug_report = self.handoff.read("qa", "dev")
                    _section(f"DEVELOPER FIX BATCH  docker fix #{docker_attempt}")
                    self._step_developer_fix_batch(dev_features, bug_report)
            if not docker_passed:
                for f in devops_targets:
                    self._block_feature(f, f"QA Docker verdict failed after {MAX_ITERATIONS} attempts.")

        # Orchestrator agent: session summary (LLM call #2 of session,
        # plus one per blocked feature)
        summary = self.orc_agent(
            f"Session complete. Final state:\n\n{self.features.summary()}\n\n"
            "Write a concise summary: what was accomplished, "
            "what is blocked, and recommended next steps."
        )
        log("orchestrator_agent", "ALL", "session-end", summary, "Next session")
        _section(f"SESSION SUMMARY  {ts()}")
        print(summary)