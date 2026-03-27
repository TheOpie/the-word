"""Git publisher — commit and push events.json updates."""

import subprocess
import time
from datetime import datetime
from pathlib import Path

MAX_PUSH_ATTEMPTS = 3
RETRY_DELAY = 2  # seconds


def _git(args: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    """Run a git command, capturing output."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        **kwargs,
    )


def publish(repo_root: Path, events_json: Path, event_count: int, source_count: int) -> bool:
    """Stage, commit, and push events.json. Returns True on success, False on failure.

    Handles: remote divergence (pull --rebase), transient push failures (retry),
    and network/auth errors (log and continue).
    """
    try:
        # --- Check for changes ---
        result = _git(["diff", "--quiet", str(events_json)], cwd=repo_root)
        if result.returncode == 0:
            status = _git(["status", "--porcelain", str(events_json)], cwd=repo_root)
            if not status.stdout.strip():
                print("  No changes to events.json — skipping push")
                return True

        # --- Verify we're on a branch (not detached HEAD) ---
        head = _git(["symbolic-ref", "--short", "HEAD"], cwd=repo_root)
        if head.returncode != 0:
            print("  ERROR: Detached HEAD — cannot push. Run 'git checkout main'.")
            return False

        branch = head.stdout.strip()

        # --- Verify remote exists ---
        remote = _git(["remote"], cwd=repo_root)
        if not remote.stdout.strip():
            print("  ERROR: No git remote configured — cannot push.")
            return False

        # --- Stage ---
        result = _git(["add", str(events_json)], cwd=repo_root)
        if result.returncode != 0:
            print("  ERROR: git add failed: {}".format(result.stderr.strip()))
            return False

        # --- Commit ---
        today = datetime.now().strftime("%Y-%m-%d")
        msg = "Events update: {} ({} events from {} sources)".format(
            today, event_count, source_count
        )
        result = _git(["commit", "-m", msg], cwd=repo_root)
        if result.returncode != 0:
            # Could be "nothing to commit" race
            if "nothing to commit" in result.stdout:
                print("  No changes to commit (already up to date)")
                return True
            print("  ERROR: git commit failed: {}".format(result.stderr.strip()))
            return False

        # --- Sync with remote and push (with retries) ---
        for attempt in range(1, MAX_PUSH_ATTEMPTS + 1):
            # Always fetch + rebase before pushing to avoid "remote contains work" errors
            print("  Syncing with remote (attempt {}/{})...".format(attempt, MAX_PUSH_ATTEMPTS))
            fetch_result = _git(["fetch", "origin", branch], cwd=repo_root)
            if fetch_result.returncode != 0:
                stderr = fetch_result.stderr.strip()
                if _is_network_error(stderr):
                    print("  WARN: Network error during fetch: {}".format(stderr))
                    if attempt < MAX_PUSH_ATTEMPTS:
                        time.sleep(RETRY_DELAY * attempt)
                        continue
                    print("  ERROR: Network unreachable after {} attempts. Commit saved locally.".format(MAX_PUSH_ATTEMPTS))
                    return False
                if _is_auth_error(stderr):
                    print("  ERROR: Git authentication failed. Check SSH keys or tokens.")
                    return False
                print("  WARN: git fetch failed: {}".format(stderr))

            # Rebase onto remote
            rebase_result = _git(["rebase", "origin/{}".format(branch)], cwd=repo_root)
            if rebase_result.returncode != 0:
                stderr = rebase_result.stderr.strip()
                if "CONFLICT" in stderr or "conflict" in rebase_result.stdout:
                    print("  ERROR: Merge conflict during rebase. Aborting rebase.")
                    _git(["rebase", "--abort"], cwd=repo_root)
                    print("  Commit preserved locally. Manual resolution needed:")
                    print("    cd {} && git pull --rebase && git push".format(repo_root))
                    return False
                # Might be "already up to date" or trivial — try pushing anyway

            # Push
            push_result = _git(["push", "origin", branch], cwd=repo_root)
            if push_result.returncode == 0:
                print("  Pushed: " + msg)
                return True

            stderr = push_result.stderr.strip()
            if _is_auth_error(stderr):
                print("  ERROR: Git authentication failed. Check SSH keys or tokens.")
                return False
            if _is_network_error(stderr):
                print("  WARN: Network error during push (attempt {})".format(attempt))
                if attempt < MAX_PUSH_ATTEMPTS:
                    time.sleep(RETRY_DELAY * attempt)
                    continue
            if "rejected" in stderr or "non-fast-forward" in stderr:
                print("  WARN: Push rejected (attempt {}), will re-sync...".format(attempt))
                if attempt < MAX_PUSH_ATTEMPTS:
                    time.sleep(RETRY_DELAY)
                    continue

            # Unknown push error
            print("  WARN: Push failed (attempt {}): {}".format(attempt, stderr))
            if attempt < MAX_PUSH_ATTEMPTS:
                time.sleep(RETRY_DELAY * attempt)

        print("  ERROR: Push failed after {} attempts. Commit saved locally.".format(MAX_PUSH_ATTEMPTS))
        print("  Manual fix: cd {} && git push".format(repo_root))
        return False

    except Exception as e:
        print("  ERROR: Unexpected publisher error: {}".format(e))
        return False


def _is_auth_error(stderr: str) -> bool:
    markers = ["permission denied", "authentication failed", "could not read from remote",
               "host key verification failed", "403", "401"]
    lower = stderr.lower()
    return any(m in lower for m in markers)


def _is_network_error(stderr: str) -> bool:
    markers = ["network is unreachable", "could not resolve", "connection timed out",
               "connection refused", "no route to host", "ssl", "tls"]
    lower = stderr.lower()
    return any(m in lower for m in markers)
