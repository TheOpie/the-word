#!/usr/bin/env python3
"""Hermes-facing, synchronous production runner for TheWord.

This file intentionally owns operational concerns (locking, browser repair,
publication proof, and alerting) while leaving the scraper pipeline unchanged.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
LOG_NAME = "the-word-scrape.log"
MAX_LOG_BYTES = 2_000_000
MAX_LOG_BACKUPS = 3
SENSITIVE_ENV_NAMES = {
    "TOKEN", "PASSWORD", "SECRET", "API_KEY", "ACCESS_TOKEN", "BOT_TOKEN",
    "AUTHORIZATION",
}
SENSITIVE_ENV_SUFFIXES = tuple(SENSITIVE_ENV_NAMES)
SENSITIVE_ENV_COMPACT_SUFFIXES = tuple(name.replace("_", "") for name in SENSITIVE_ENV_NAMES)


@dataclass
class Config:
    repo: Path
    hermes_home: Path

    @property
    def python(self) -> Path:
        return self.repo / ".venv/bin/python"

    @property
    def browser_path(self) -> Path:
        return self.hermes_home / "playwright-browsers"

    @property
    def log_path(self) -> Path:
        return self.hermes_home / "logs" / LOG_NAME

    @property
    def state_dir(self) -> Path:
        return self.hermes_home / "state"

    @property
    def lock_path(self) -> Path:
        return self.hermes_home / "run" / "the-word-scrape.lock"


class OperationalError(Exception):
    def __init__(self, stage: str, reason: str):
        self.stage, self.reason = stage, reason
        super().__init__(reason)


def current_date(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(CT).date().isoformat()


def sanitize(value: str, limit: int = 280) -> str:
    """Keep alert/log summaries useful without leaking token-shaped values or URLs."""
    # Headers are line-oriented: replace the whole value so ``Authorization:
    # Bearer value`` cannot leave its credential after a partial substitution.
    value = re.sub(r"(?i)\bauthorization\s*:\s*[^\r\n]*", "Authorization=[redacted]", value)
    value = re.sub(
        r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"
        r"(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s&;,]+)",
        _redact_sensitive_env_assignment,
        value,
    )
    credential = r"(?:api[\s_-]?key|access[\s_-]?token|bot[\s_-]?token|telegram[\s_-]?bot[\s_-]?token|token|password|secret|authorization)"
    value = re.sub(
        rf"(?i)\b({credential})\s*[:=]\s*(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s&;,]+)",
        r"\1=[redacted]",
        value,
    )
    value = re.sub(r"(?i)\bbearer\s+[^\s&;,]+", "Bearer [redacted]", value)
    value = re.sub(r"https?://\S+", "[url]", value)
    return " ".join(value.split())[:limit]


def _redact_sensitive_env_assignment(match: re.Match[str]) -> str:
    """Redact only identifier-style assignments whose key clearly denotes a secret."""
    key = match.group(1)
    normalized = key.upper().replace("-", "_")
    compact = normalized.replace("_", "")
    if (
        normalized in SENSITIVE_ENV_NAMES
        or normalized.endswith(SENSITIVE_ENV_SUFFIXES)
        or compact.endswith(SENSITIVE_ENV_COMPACT_SUFFIXES)
    ):
        return f"{key}=[redacted]"
    return match.group(0)


def normalize_log_retention(path: Path) -> None:
    """Prune surplus backups and discard oversized historical files safely."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = path.name + "."
    for candidate in path.parent.glob(prefix + "*"):
        suffix = candidate.name[len(prefix):]
        if suffix.isdigit() and (
            suffix != str(int(suffix)) or not 1 <= int(suffix) <= MAX_LOG_BACKUPS
        ):
            candidate.unlink(missing_ok=True)
    for candidate in [path, *(path.with_name(path.name + f".{n}") for n in range(1, MAX_LOG_BACKUPS + 1))]:
        if candidate.exists() and candidate.stat().st_size > MAX_LOG_BYTES:
            # Never preserve a prefix of an oversized old log: it may contain a
            # raw credential produced before the current sanitizer existed.
            candidate.unlink()


def rotate_log(path: Path, incoming_bytes: int = 0) -> None:
    """Keep the current log and all retained backups bounded while streaming."""
    normalize_log_retention(path)
    if not path.exists() or path.stat().st_size + incoming_bytes <= MAX_LOG_BYTES:
        return
    oldest = path.with_name(path.name + f".{MAX_LOG_BACKUPS}")
    oldest.unlink(missing_ok=True)
    for number in range(MAX_LOG_BACKUPS - 1, 0, -1):
        source = path.with_name(path.name + f".{number}")
        if source.exists():
            source.replace(path.with_name(path.name + f".{number + 1}"))
    path.replace(path.with_name(path.name + ".1"))


def bounded_log_line(message: str, now: datetime) -> str:
    """Format one sanitized log line that can never exceed the rotation size."""
    line = f"{now.astimezone(timezone.utc).isoformat(timespec='seconds')} {sanitize(message)}\n"
    encoded = line.encode("utf-8")
    if len(encoded) <= MAX_LOG_BYTES:
        return line
    # This is normally unreachable because sanitize bounds messages, but it
    # keeps retention bounded if the configured cap is reduced for operations.
    return encoded[: MAX_LOG_BYTES - 1].decode("utf-8", errors="ignore") + "\n"


def load_env_names(path: Path) -> dict[str, str]:
    """Read .env without ever emitting its contents."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            result[key.strip()] = value.strip().strip("\"'")
    return result


def telegram_sender(env: dict[str, str], message: str) -> bool:
    """Best-effort Telegram sender. Values are never included in exceptions/logs."""
    token = env.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_TOKEN")
    # Hermes currently names its destination channel explicitly. Keep the older
    # generic name as a compatibility fallback for other installations.
    chat_id = env.get("TELEGRAM_HOME_CHANNEL") or env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        import httpx
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message[:1000]}, timeout=10,
        )
        return response.is_success
    except Exception:
        return False


class ProductionRunner:
    """Dependency-injectable runner; tests use fakes for all external effects."""
    def __init__(self, config: Config, *, now: Callable[[], datetime] | None = None,
                 command: Callable[[list[str], dict[str, str]], int] | None = None,
                 notifier: Callable[[dict[str, str], str], bool] = telegram_sender):
        self.config = config
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.command = command or self._stream_command
        self.notifier = notifier

    def _log(self, message: str) -> None:
        line = bounded_log_line(message, self.now())
        rotate_log(self.config.log_path, len(line.encode("utf-8")))
        with self.config.log_path.open("a", encoding="utf-8") as log:
            log.write(line)
        print(line, end="")

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(self.config.browser_path)
        return env

    def _stream_command(self, args: list[str], env: dict[str, str]) -> int:
        # Stream output line-by-line through the same sanitizer/rotator used by
        # runner messages. Do not retain raw child output in memory or on disk.
        normalize_log_retention(self.config.log_path)
        with subprocess.Popen(args, cwd=self.config.repo, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                self._log(line)
            return proc.wait()

    def preflight(self) -> None:
        env = self._env()
        self.config.browser_path.mkdir(parents=True, exist_ok=True)
        smoke = [str(self.config.python), "-c", "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()"]
        if self.command(smoke, env) == 0:
            return
        self._log("browser smoke check failed; repairing Chromium")
        install = [str(self.config.python), "-m", "playwright", "install", "chromium"]
        if self.command(install, env) != 0:
            raise OperationalError("browser-repair", "Chromium installation failed")
        if self.command(smoke, env) != 0:
            raise OperationalError("browser-smoke", "Chromium launch failed after repair")

    def validate_health(self) -> None:
        path = self.config.repo / "state" / "last_run.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OperationalError("health", "last_run.json is missing or malformed") from exc
        try:
            run_date = datetime.fromisoformat(str(report["run_at"]).replace("Z", "+00:00")).astimezone(CT).date().isoformat()
        except (KeyError, ValueError, TypeError) as exc:
            raise OperationalError("health", "last_run.json has an invalid run_at") from exc
        if run_date != current_date(self.now()):
            raise OperationalError("health", "last_run.json is not from today in America/Chicago")
        required = report.get("published") is True and report.get("wrote_events_json") is True
        if not required or report.get("overall_status") not in {"healthy", "degraded"}:
            raise OperationalError("health", "last_run.json does not prove a published healthy/degraded run")

    def remote_proof(self) -> None:
        env = self._env()
        if self.command(["git", "fetch", "origin", "main"], env) != 0:
            raise OperationalError("remote-proof", "git fetch origin main failed")
        def git(*args: str, trim: bool = True) -> str:
            result = subprocess.run(["git", *args], cwd=self.config.repo, env=env,
                                    capture_output=True, text=True)
            if result.returncode:
                raise OperationalError("remote-proof", f"git {' '.join(args)} failed")
            return result.stdout.strip() if trim else result.stdout
        head, remote = git("rev-parse", "HEAD"), git("rev-parse", "refs/remotes/origin/main")
        if head != remote:
            raise OperationalError("remote-proof", "local HEAD differs from origin/main")
        subject = git("log", "-1", "--format=%s", "HEAD")
        expected = (
            "Events update: "
            + re.escape(current_date(self.now()))
            + r" \(\d+ events from \d+ sources\)"
        )
        if re.fullmatch(expected, subject) is None:
            raise OperationalError("remote-proof", "HEAD is not today's Events update")
        blob = git("show", "HEAD:docs/events.json", trim=False)
        try:
            local = (self.config.repo / "docs/events.json").read_text(encoding="utf-8")
        except OSError as exc:
            raise OperationalError("remote-proof", "working docs/events.json is missing") from exc
        if blob != local:
            raise OperationalError("remote-proof", "HEAD docs/events.json differs from working copy")

    def _alert(self, failure: OperationalError | None) -> None:
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.state_dir / "the-word-alert.json"
        old = {}
        try:
            old = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        now = self.now().astimezone(CT)
        env = load_env_names(self.config.hermes_home / ".env")
        if failure is None:
            if old.get("active"):
                try:
                    self.notifier(env, f"TheWord recovered | {now:%Y-%m-%d %H:%M %Z}")
                except Exception:
                    pass
            path.unlink(missing_ok=True)
            return
        signature = hashlib.sha256(f"{failure.stage}:{failure.reason}".encode()).hexdigest()
        due = not old.get("active") or old.get("signature") != signature
        try:
            last = datetime.fromisoformat(old.get("last_attempt", ""))
            due = due or now - last.astimezone(CT) >= timedelta(hours=24)
        except (TypeError, ValueError):
            due = True
        delivered = False
        if due:
            msg = f"TheWord failure | {now:%Y-%m-%d %H:%M %Z} | {failure.stage}: {sanitize(failure.reason, 180)} | log: {self.config.log_path}"
            try:
                delivered = self.notifier(env, msg)
            except Exception:
                delivered = False
        path.write_text(json.dumps({"active": True, "signature": signature,
                                    "last_attempt": now.isoformat() if due else old.get("last_attempt"),
                                    "notification_failed": due and not delivered}), encoding="utf-8")

    def _safe_alert(self, failure: OperationalError | None) -> None:
        """Alerts are best effort and must never alter the runner's truthful exit."""
        try:
            self._alert(failure)
        except Exception:
            pass

    def _record_failure(self, failure: OperationalError) -> int:
        try:
            self._log(f"operational failure ({failure.stage}): {failure.reason}")
        except Exception:
            pass
        self._safe_alert(failure)
        return 1

    def run(self, preflight_only: bool = False) -> int:
        try:
            self.config.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config.lock_path.open("a+") as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return self._record_failure(
                        OperationalError("lock", "another TheWord run is active")
                    )
                try:
                    self.preflight()
                    if not preflight_only:
                        code = self.command([str(self.config.python), "-m", "the_word", "scrape"], self._env())
                        if code not in (0, 2):
                            raise OperationalError("pipeline", f"scrape exited {code}")
                        self.validate_health()
                        self.remote_proof()
                    # Recovery delivery/state failures are optional and do not turn a
                    # verified publish into a failure.
                    self._safe_alert(None)
                    return 0
                except OperationalError as failure:
                    return self._record_failure(failure)
                except Exception as exc:
                    # Includes command-launch errors (for example a missing venv
                    # Python or git binary). Do not expose arbitrary child details.
                    return self._record_failure(
                        OperationalError("runner", sanitize(f"{type(exc).__name__}: {exc}"))
                    )
        except Exception as exc:
            return self._record_failure(
                OperationalError("runner", sanitize(f"{type(exc).__name__}: {exc}"))
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TheWord safely under Hermes.")
    parser.add_argument("--repo", type=Path, default=Path("/Users/dorian/project/the-word"))
    parser.add_argument("--hermes-home", type=Path, default=Path.home() / ".hermes")
    parser.add_argument("--preflight", action="store_true", help="repair/smoke-test Chromium only")
    args = parser.parse_args(argv)
    return ProductionRunner(Config(args.repo, args.hermes_home)).run(args.preflight)


if __name__ == "__main__":
    raise SystemExit(main())
