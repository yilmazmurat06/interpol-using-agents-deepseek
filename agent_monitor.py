#!/usr/bin/env python3
"""
agent_monitor.py — Live dashboard for the Interpol pipeline
-----------------------------------------------------------
Watches running opencode agent processes and shows real-time activity.
Usage: python3 agent_monitor.py [--interval 2]

Requires: nothing beyond stdlib
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from shutil import get_terminal_size
except ImportError:
    def get_terminal_size():
        return os.get_terminal_size()

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
PROGRESS_LOG = BASE_DIR / "claude-progress.txt"
FEATURE_LIST = BASE_DIR / "feature-list.json"
HANDOFF_DIR  = BASE_DIR / "handoff"
RESEARCH_DIR = BASE_DIR / "research"

# Colors
C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"
C_DIM    = "\033[2m"
C_GREEN  = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE   = "\033[34m"
C_CYAN   = "\033[36m"
C_RED    = "\033[31m"
C_MAGENTA= "\033[35m"
C_BG_BLUE= "\033[44m"
C_BG_GREEN = "\033[42m"
C_BG_YELLOW= "\033[43m"
C_BG_RED   = "\033[41m"

# ── Agent role detection ──────────────────────────────────────────────────────

ROLE_MAP = {
    "interpol-research":     "RESEARCH",
    "interpol-developer":    "DEVELOPER",
    "interpol-devops":       "DEVOPS",
    "interpol-qa":           "QA",
    "interpol-orchestrator": "ORCHESTRATOR",
}

ROLE_COLORS = {
    "RESEARCH":      C_CYAN,
    "DEVELOPER":     C_GREEN,
    "DEVOPS":        C_YELLOW,
    "QA":            C_MAGENTA,
    "ORCHESTRATOR":  C_BLUE,
}

# ── Context limit tracking ────────────────────────────────────────────────────

# Model → context window (tokens). Sources: DeepSeek API docs.
# RSS memory is used as a proxy for context pressure — higher RSS ≈ more
# conversation history + system prompts loaded in the LLM process.
# These are rough estimates calibrated on macOS ARM64 opencode processes.
MODEL_CONTEXT = {
    "interpol-research":     ("deepseek-v4-pro",     128_000),
    "interpol-developer":    ("deepseek-v4-pro",       128_000),
    "interpol-devops":       ("deepseek-chat",       128_000),
    "interpol-qa":           ("deepseek-chat",       128_000),
    "interpol-orchestrator": ("deepseek-v4-flash",  128_000),
}

# RSS thresholds (MB) mapped to approximate context pressure levels.
# Calibrated: ~200 MB idle → ~1.5 GB at heavy context usage.
RSS_CONTEXT_LOW     = 400   # <10% context — normal
RSS_CONTEXT_MEDIUM  = 800   # 10-40% context — growing
RSS_CONTEXT_HIGH    = 1200  # 40-70% context — caution
RSS_CONTEXT_CRITICAL = 1600 # >70% context — near limit

# ── Process scanning ──────────────────────────────────────────────────────────

def estimate_context_pressure(rss_mb: float) -> tuple[str, str, int]:
    """
    Estimate context window pressure from RSS memory.
    Returns (label, bar_color, estimated_pct).
    """
    if rss_mb < RSS_CONTEXT_LOW:
        return f"{rss_mb:.0f}MB", C_GREEN, 5
    elif rss_mb < RSS_CONTEXT_MEDIUM:
        pct = int(((rss_mb - RSS_CONTEXT_LOW) / (RSS_CONTEXT_MEDIUM - RSS_CONTEXT_LOW)) * 30) + 5
        return f"{rss_mb:.0f}MB", C_GREEN, pct
    elif rss_mb < RSS_CONTEXT_HIGH:
        pct = int(((rss_mb - RSS_CONTEXT_MEDIUM) / (RSS_CONTEXT_HIGH - RSS_CONTEXT_MEDIUM)) * 30) + 35
        return f"{rss_mb:.0f}MB", C_YELLOW, pct
    elif rss_mb < RSS_CONTEXT_CRITICAL:
        pct = int(((rss_mb - RSS_CONTEXT_HIGH) / (RSS_CONTEXT_CRITICAL - RSS_CONTEXT_HIGH)) * 25) + 65
        return f"{rss_mb:.0f}MB", C_YELLOW, pct
    else:
        pct = min(int(((rss_mb - RSS_CONTEXT_CRITICAL) / 800) * 30) + 90, 100)
        return f"{rss_mb:.0f}MB", C_RED, pct


def context_bar(pct: int, width: int = 20) -> str:
    """Render a compact progress bar."""
    filled = int(width * pct / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def scan_agents() -> list[dict]:
    """Find all running opencode agent processes."""
    agents = []
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "opencode run --agent" not in line:
                continue
            # Extract agent name
            m = re.search(r"--agent\s+(\S+)", line)
            if not m:
                continue
            agent_name = m.group(1)
            role = ROLE_MAP.get(agent_name, agent_name)

            # Extract PID, CPU, MEM, RSS, start time, elapsed
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            pid     = int(parts[1])
            cpu     = float(parts[2])
            mem     = float(parts[3])
            rss_kb  = int(parts[5])
            start   = parts[8]
            elapsed = parts[9]

            # Extract task prompt (after the last \012\012## Your task)
            cmd = parts[10] if len(parts) > 10 else ""
            task = ""
            if "## Your task" in cmd:
                task = cmd.split("## Your task")[-1].strip()
                # Decode \012 as newlines, then truncate
                task = task.replace("\\012", "\n").strip()
                if len(task) > 200:
                    task = task[:200] + "..."

            model_name, ctx_limit = MODEL_CONTEXT.get(agent_name, ("unknown", 128_000))
            ctx_label, ctx_color, ctx_pct = estimate_context_pressure(round(rss_kb / 1024, 1))

            agents.append({
                "pid":        pid,
                "role":       role,
                "agent_name": agent_name,
                "cpu":        cpu,
                "mem":        mem,
                "rss_mb":     round(rss_kb / 1024, 1),
                "start":      start,
                "elapsed":    elapsed,
                "task":       task,
                "model":      model_name,
                "ctx_limit":  ctx_limit,
                "ctx_label":  ctx_label,
                "ctx_color":  ctx_color,
                "ctx_pct":    ctx_pct,
            })
    except Exception:
        pass
    return agents


# ── Progress log ──────────────────────────────────────────────────────────────

def read_progress_log() -> list[dict]:
    """Parse claude-progress.txt into structured entries."""
    entries = []
    if not PROGRESS_LOG.exists():
        return entries
    text = PROGRESS_LOG.read_text()
    for line in text.splitlines():
        # Format: [YYYY-MM-DD HH:MM] [AGENT] [FEATURE] [STATUS]
        m = re.match(r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\]\s+\[(\w+)\]\s+\[(\S+)\]\s+\[(\S+)\]", line)
        if m:
            entries.append({
                "time":   m.group(1),
                "agent":  m.group(2),
                "feature": m.group(3),
                "status": m.group(4),
            })
    return entries


# ── Feature statuses ─────────────────────────────────────────────────────────

def read_features() -> list[dict]:
    """Read feature-list.json."""
    if not FEATURE_LIST.exists():
        return []
    with open(FEATURE_LIST) as f:
        return json.load(f).get("features", [])


# ── File activity ─────────────────────────────────────────────────────────────

def recent_files(since: float, max_age=300) -> list[str]:
    """Find files modified in the last `max_age` seconds."""
    files = []
    for root, dirs, filenames in os.walk(BASE_DIR):
        # Skip hidden dirs and node_modules
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules" and d != "__pycache__"]
        for fn in filenames:
            fp = os.path.join(root, fn)
            try:
                mtime = os.path.getmtime(fp)
                if time.time() - mtime < max_age and mtime > since:
                    rel = os.path.relpath(fp, BASE_DIR)
                    files.append((rel, mtime))
            except OSError:
                pass
    files.sort(key=lambda x: x[1], reverse=True)
    return [f[0] for f in files[:15]]


# ── Handoff files ─────────────────────────────────────────────────────────────

def list_handoffs() -> list[str]:
    """List handoff files."""
    if not HANDOFF_DIR.exists():
        return []
    return sorted(os.listdir(HANDOFF_DIR))


# ── Dashboard rendering ───────────────────────────────────────────────────────

def render_dashboard(agents: list[dict], progress: list[dict],
                     features: list[dict], files: list[str],
                     handoffs: list[str], start_time: float):
    """Render the full dashboard."""
    width = get_terminal_size().columns
    now = time.time()
    elapsed_total = now - start_time
    mins, secs = divmod(int(elapsed_total), 60)
    total_str = f"{mins}m {secs:02d}s"

    lines = []

    # ── Header ──────────────────────────────────────────────────────────────
    header = f"  INTERPOL PIPELINE MONITOR  ·  {datetime.now().strftime('%H:%M:%S')}  ·  Total: {total_str}"
    lines.append(f"{C_BG_BLUE}{C_BOLD}{' ' * width}{C_RESET}")
    pad = max(width - len(header) - 2, 0)
    lines.append(f"{C_BG_BLUE}{C_BOLD}  {header}{' ' * pad}{C_RESET}")
    lines.append(f"{C_BG_BLUE}{' ' * width}{C_RESET}")
    lines.append("")

    # ── Active agents ───────────────────────────────────────────────────────
    lines.append(f"{C_BOLD}── ACTIVE AGENTS ──────────────────────────────────────────────{C_RESET}")
    if agents:
        for a in agents:
            color = ROLE_COLORS.get(a["role"], C_RESET)
            status = f"{color}● RUNNING{C_RESET}" if a["cpu"] > 0 else f"{C_DIM}○ IDLE{C_RESET}"
            ctx_bar_str = context_bar(a["ctx_pct"])
            lines.append(
                f"  {status}  {C_BOLD}{a['role']}{C_RESET}  PID:{a['pid']}  "
                f"CPU:{a['cpu']:.1f}%  MEM:{a['mem']:.1f}%  "
                f"RSS:{a['rss_mb']}MB  Elapsed:{a['elapsed']}"
            )
            lines.append(
                f"  {C_DIM}    model:{a['model']}  "
                f"context:{a['ctx_color']}{ctx_bar_str} {a['ctx_pct']}%{C_RESET}  "
                f"limit:{a['ctx_limit']:,} tokens"
            )
            if a["task"]:
                task_preview = a["task"].split("\n")[0][:120]
                lines.append(f"  {C_DIM}    → {task_preview}{C_RESET}")
    else:
        lines.append(f"  {C_DIM}  (no agents running){C_RESET}")
    lines.append("")

    # ── Recent file activity ────────────────────────────────────────────────
    lines.append(f"{C_BOLD}── RECENT FILE ACTIVITY ─────────────────────────────────────────{C_RESET}")
    if files:
        for f in files:
            lines.append(f"  {C_GREEN}✦{C_RESET}  {f}")
    else:
        lines.append(f"  {C_DIM}  (no files created yet){C_RESET}")
    lines.append("")

    # ── Progress log (last 10 entries) ──────────────────────────────────────
    lines.append(f"{C_BOLD}── PROGRESS LOG ─────────────────────────────────────────────────{C_RESET}")
    if progress:
        for e in progress[-10:]:
            color = ROLE_COLORS.get(e["agent"], C_RESET)
            status_color = C_GREEN if "pass" in e["status"].lower() or "done" in e["status"].lower() or "complete" in e["status"].lower() else C_YELLOW
            lines.append(f"  {C_DIM}{e['time']}{C_RESET}  {color}{e['agent']}{C_RESET}  [{e['feature']}]  {status_color}{e['status']}{C_RESET}")
    else:
        lines.append(f"  {C_DIM}  (no entries yet){C_RESET}")
    lines.append("")

    # ── Feature statuses ────────────────────────────────────────────────────
    lines.append(f"{C_BOLD}── FEATURE STATUSES ─────────────────────────────────────────────{C_RESET}")
    if features:
        for f in features:
            status = f["status"]
            if status == "done":
                icon, color = "✅", C_GREEN
            elif status == "blocked":
                icon, color = "❌", C_RED
            elif status in ("qa-pass", "devops-complete"):
                icon, color = "🔵", C_BLUE
            elif status == "dev-complete":
                icon, color = "🟡", C_YELLOW
            elif status == "in-progress":
                icon, color = "🔄", C_CYAN
            else:
                icon, color = "⏳", C_DIM
            lines.append(f"  {icon}  {color}{f['id']}{C_RESET}  {f['description'][:60]}  {C_DIM}[{status}]{C_RESET}")
    lines.append("")

    # ── Handoff files ───────────────────────────────────────────────────────
    if handoffs:
        lines.append(f"{C_BOLD}── HANDOFF FILES ──────────────────────────────────────────────{C_RESET}")
        for h in handoffs:
            lines.append(f"  📄  {h}")
        lines.append("")

    # ── Footer ──────────────────────────────────────────────────────────────
    lines.append(f"{C_DIM}  Refresh: every 3s  ·  Press Ctrl+C to exit{C_RESET}")

    return "\n".join(lines)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    interval = 3
    for arg in sys.argv[1:]:
        if arg.startswith("--interval="):
            interval = int(arg.split("=")[1])
        elif arg == "--help":
            print(f"Usage: python3 {sys.argv[0]} [--interval N]")
            print(f"  --interval  Refresh interval in seconds (default: 3)")
            return

    start_time = time.time()
    last_file_count = 0

    # Clear screen
    os.system("clear")

    try:
        while True:
            agents   = scan_agents()
            progress = read_progress_log()
            features = read_features()
            files    = recent_files(since=start_time)
            handoffs = list_handoffs()

            dashboard = render_dashboard(
                agents, progress, features, files, handoffs, start_time
            )

            # Move cursor to top-left and redraw
            sys.stdout.write(f"\033[H\033[J")
            sys.stdout.write(dashboard)
            sys.stdout.flush()

            time.sleep(interval)
    except KeyboardInterrupt:
        sys.stdout.write(f"\n\n{C_BOLD}Monitor stopped.{C_RESET}\n")


if __name__ == "__main__":
    main()
