"""Git publisher — commit and push events.json updates."""

import subprocess
from datetime import datetime
from pathlib import Path


def publish(repo_root: Path, events_json: Path, event_count: int, source_count: int):
    """Stage, commit, and push events.json. No-op if unchanged."""
    try:
        # Check if there are changes
        result = subprocess.run(
            ["git", "diff", "--quiet", str(events_json)],
            cwd=repo_root,
            capture_output=True,
        )
        if result.returncode == 0:
            # Also check if file is untracked
            status = subprocess.run(
                ["git", "status", "--porcelain", str(events_json)],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )
            if not status.stdout.strip():
                print("  No changes to events.json — skipping push")
                return

        # Stage
        subprocess.run(
            ["git", "add", str(events_json)],
            cwd=repo_root,
            check=True,
        )

        # Commit
        today = datetime.now().strftime("%Y-%m-%d")
        msg = "Events update: {} ({} events from {} sources)".format(
            today, event_count, source_count
        )
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=repo_root,
            check=True,
        )

        # Push
        subprocess.run(
            ["git", "push"],
            cwd=repo_root,
            check=True,
        )
        print("  Pushed: " + msg)

    except subprocess.CalledProcessError as e:
        print(f"  ERROR: Git operation failed: {e}")
        raise
