"""
Git Auto-Updater Module for Raspberry Pi Dashboard.
Supports:
1. Periodic background polling for new GitHub commits (ideal for Pis behind home NAT/firewall).
2. GitHub Webhook endpoint for instant push updates.
3. On-demand UI checks and one-click manual update execution.
4. Auto-install of updated requirements.txt and systemd/process restart.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import subprocess
import sys
import time
from typing import Dict, Any, Optional

logger = logging.getLogger("pi_dashboard.updater")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Configuration (can be overridden via environment variables)
# If AUTO_UPDATE_ON_COMMIT is True, it automatically pulls and restarts when a new commit is found
AUTO_UPDATE_ON_COMMIT = os.getenv("AUTO_UPDATE_ON_COMMIT", "true").lower() in ("1", "true", "yes")
CHECK_INTERVAL_SECONDS = int(os.getenv("UPDATE_CHECK_INTERVAL", "300"))  # Default: every 5 minutes
WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")  # Optional secret for GitHub Webhook

# Global updater state
update_state = {
    "is_git_repo": False,
    "branch": "main",
    "current_commit": "unknown",
    "commit_message": "",
    "commit_date": "",
    "remote_url": "",
    "update_available": False,
    "commits_behind": 0,
    "latest_remote_commit": "",
    "latest_remote_message": "",
    "last_check_timestamp": 0,
    "is_updating": False,
    "last_update_log": "",
    "channel": "main",
    "version": "",
    "latest_release": ""
}

# Update channel (also read by update.sh as root): "main" = every commit, "stable" = release tags only
CHANNEL_FILE = os.path.join(BASE_DIR, ".update-channel")
CHANNELS = {"main": "Jeder Commit (main)", "stable": "Nur Releases (stabil)"}
TAG_PATTERN = "v[0-9]*.[0-9]*.[0-9]*"


def channel() -> str:
    try:
        with open(CHANNEL_FILE) as f:
            return "stable" if f.read().strip() == "stable" else "main"
    except OSError:
        return "main"


def set_channel(value: str) -> str:
    if value not in CHANNELS:
        raise ValueError("Unbekannter Update-Kanal.")
    tmp = CHANNEL_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(value + "\n")
    os.replace(tmp, CHANNEL_FILE)
    update_state["channel"] = value
    return value


def _target(branch: str) -> str:
    """Commit to update to - the same rule as update.sh: branch head, or on "stable" the newest
    release tag of the branch (never an older one than what is installed)."""
    rc, head = run_git(["rev-parse", f"origin/{branch}"])
    if rc != 0:
        return ""
    if channel() != "stable":
        return head
    rc, tags = run_git(["tag", "--list", TAG_PATTERN, "--merged", f"origin/{branch}", "--sort=-v:refname"])
    tag = tags.splitlines()[0].strip() if rc == 0 and tags.strip() else ""
    update_state["latest_release"] = tag
    if not tag:
        return ""
    rc, commit = run_git(["rev-parse", f"{tag}^{{commit}}"])
    if rc != 0:
        return ""
    rc_anc, _ = run_git(["merge-base", "--is-ancestor", commit, "HEAD"])
    return "" if rc_anc == 0 else commit


def run_git(args: list[str], timeout: int = 15) -> tuple[int, str]:
    """Execute a git command within the project repository."""
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return res.returncode, (res.stdout or res.stderr).strip()
    except Exception as e:
        return 1, str(e)


def refresh_git_info() -> Dict[str, Any]:
    """Inspect local repository state."""
    # Check if .git exists
    if not os.path.exists(os.path.join(BASE_DIR, ".git")):
        update_state["is_git_repo"] = False
        update_state["current_commit"] = "Kein Git-Repo"
        return update_state

    update_state["is_git_repo"] = True

    # Current branch
    rc, out = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0:
        update_state["branch"] = out

    # Current short commit hash
    rc, out = run_git(["rev-parse", "--short", "HEAD"])
    if rc == 0:
        update_state["current_commit"] = out

    # Commit message & date
    rc, out = run_git(["log", "-1", "--format=%s (%cr)"])
    if rc == 0:
        update_state["commit_message"] = out

    # Remote URL
    rc, out = run_git(["config", "--get", "remote.origin.url"])
    if rc == 0:
        update_state["remote_url"] = out

    # Release this commit belongs to (newest tag at or before it) and the update channel
    rc, out = run_git(["describe", "--tags", "--abbrev=0", "--match", TAG_PATTERN])
    update_state["version"] = out if rc == 0 else ""
    update_state["channel"] = channel()

    return update_state


def check_for_updates() -> Dict[str, Any]:
    """Fetch remote repository and check if commits are pending."""
    refresh_git_info()
    if not update_state["is_git_repo"]:
        return update_state

    update_state["last_check_timestamp"] = time.time()
    branch = update_state["branch"] or "main"

    # Fetch remote silently (with tags: the stable channel follows them)
    rc, out = run_git(["fetch", "--tags", "--force", "origin", branch], timeout=20)
    if rc != 0:
        logger.warning(f"Git fetch failed: {out}")
        return update_state

    target = _target(branch)
    if not target:
        update_state.update(commits_behind=0, update_available=False, latest_remote_commit="", latest_remote_message="")
        return update_state

    # Check commit difference
    rc, count_str = run_git(["rev-list", f"HEAD..{target}", "--count"])
    if rc == 0 and count_str.isdigit():
        behind_count = int(count_str)
        update_state["commits_behind"] = behind_count
        update_state["update_available"] = behind_count > 0

        if behind_count > 0:
            # Get latest remote commit info
            rc_log, log_str = run_git(["log", "-1", target, "--format=%h - %s"])
            if rc_log == 0 and " - " in log_str:
                parts = log_str.split(" - ", 1)
                update_state["latest_remote_commit"] = parts[0]
                update_state["latest_remote_message"] = parts[1]
                logger.info(f"🚀 New update available on GitHub: {log_str} ({behind_count} commits behind)")
        else:
            update_state["latest_remote_commit"] = ""
            update_state["latest_remote_message"] = ""

    return update_state


def perform_update() -> Dict[str, Any]:
    """Pull latest changes, install dependencies, and schedule restart."""
    if update_state["is_updating"]:
        return {"status": "in_progress", "message": "Update läuft bereits..."}

    refresh_git_info()
    if not update_state["is_git_repo"]:
        return {"status": "error", "message": "Das Verzeichnis ist kein Git-Repository."}

    update_state["is_updating"] = True
    branch = update_state["branch"] or "main"
    logs = []

    try:
        # 1. Git pull
        if channel() == "stable":
            target = _target(branch)
            if not target:
                update_state["is_updating"] = False
                return {"status": "success", "message": "Bereits auf dem neuesten Release."}
            logs.append(f"Switching to release {update_state['latest_release']} ({target[:7]})...")
            rc, out = run_git(["reset", "--hard", target], timeout=30)
        else:
            logs.append(f"Pulling latest commits from origin/{branch}...")
            rc, out = run_git(["pull", "origin", branch], timeout=30)
        logs.append(out)
        if rc != 0:
            update_state["is_updating"] = False
            update_state["last_update_log"] = "\n".join(logs)
            return {"status": "error", "message": f"Git Pull fehlgeschlagen: {out}", "logs": "\n".join(logs)}

        # 2. Check and install dependencies if requirements.txt changed
        venv_pip = os.path.join(BASE_DIR, "venv", "bin", "pip")
        pip_bin = venv_pip if os.path.exists(venv_pip) else "pip3"
        req_file = os.path.join(BASE_DIR, "requirements.txt")

        if os.path.exists(req_file):
            logs.append(f"Installing dependencies via {pip_bin}...")
            res = subprocess.run([pip_bin, "install", "-r", req_file], capture_output=True, text=True, timeout=90)
            logs.append(res.stdout or res.stderr)

        # 3. Refresh git state
        refresh_git_info()
        update_state["update_available"] = False
        update_state["commits_behind"] = 0
        update_state["last_update_log"] = "\n".join(logs)

        # 4. Trigger restart in background after a brief delay
        asyncio.create_task(_restart_server_delayed())

        return {
            "status": "success",
            "message": "Update erfolgreich installiert! Der Server wird jetzt neu geladen...",
            "logs": "\n".join(logs),
            "new_commit": update_state["current_commit"]
        }

    except Exception as e:
        logger.error(f"Error during update: {e}")
        update_state["is_updating"] = False
        return {"status": "error", "message": str(e), "logs": "\n".join(logs)}
    finally:
        update_state["is_updating"] = False


def run_update_now() -> Dict[str, Any]:
    """Start the update unit right away (update.sh: pull, pip, restart if something changed).
    Same as what the timer does every minute - only without waiting."""
    try:
        res = subprocess.run(["sudo", "-n", "systemctl", "start", "--no-block", "pi-dashboard-update.service"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "message": f"Update konnte nicht gestartet werden ({type(e).__name__})."}
    if res.returncode != 0:
        return {"ok": False, "message": (res.stderr or res.stdout).strip()[:200] or "Update konnte nicht gestartet werden."}
    return {"ok": True, "message": "Update gestartet – der Pi holt die neueste Version und startet das Dashboard bei Bedarf neu."}


async def _restart_server_delayed():
    """Wait 1 second so response finishes, then restart server service or process."""
    await asyncio.sleep(1.2)
    logger.info("Restarting dashboard process...")

    # Check if systemd service is active on Raspberry Pi
    try:
        res = subprocess.run(["systemctl", "is-active", "--quiet", "raspberry-dashboard"], timeout=2)
        if res.returncode == 0:
            logger.info("Restarting via systemctl restart raspberry-dashboard...")
            subprocess.Popen(["sudo", "systemctl", "restart", "raspberry-dashboard"])
            return
    except Exception:
        pass

    # Process restart fallback
    python = sys.executable
    os.execv(python, [python] + sys.argv)


def verify_github_signature(payload_body: bytes, signature_header: Optional[str]) -> bool:
    """Validate HMAC hex digest signature for GitHub Webhook security."""
    if not WEBHOOK_SECRET:
        return True  # No secret configured, allow trigger

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_signature = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        payload_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected_signature}", signature_header)


async def background_update_poller():
    """
    Background worker that runs alongside FastAPI.
    Checks for new commits every CHECK_INTERVAL_SECONDS.
    If AUTO_UPDATE_ON_COMMIT is enabled, automatically updates and restarts!
    """
    logger.info(f"Git Update Poller started (Interval: {CHECK_INTERVAL_SECONDS}s, Auto-Apply: {AUTO_UPDATE_ON_COMMIT})")
    
    # Initial quick check after 10s of startup
    await asyncio.sleep(10)

    while True:
        try:
            status = check_for_updates()
            if status.get("update_available"):
                logger.info(f"Detected {status['commits_behind']} new commit(s) on GitHub!")
                if AUTO_UPDATE_ON_COMMIT:
                    logger.info("Auto-Update is enabled! Triggering perform_update()...")
                    perform_update()
                    break  # Process will restart, loop exits
        except Exception as e:
            logger.warning(f"Error in background update check: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
