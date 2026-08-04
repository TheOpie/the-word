import fcntl
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "run_production", Path(__file__).parents[1] / "scripts" / "run_production.py"
)
runner_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = runner_module
SPEC.loader.exec_module(runner_module)


NOW = datetime(2026, 8, 4, 15, tzinfo=timezone.utc)


def make_runner(tmp_path, command=None, notices=None):
    config = runner_module.Config(tmp_path / "repo", tmp_path / "hermes")
    config.repo.mkdir()
    (config.repo / "state").mkdir()
    (config.repo / "docs").mkdir()
    notices = notices if notices is not None else []
    return runner_module.ProductionRunner(
        config, now=lambda: NOW, command=command or (lambda args, env: 0),
        notifier=lambda env, message: notices.append(message) or True,
    ), notices


def health(repo, **overrides):
    data = {"run_at": "2026-08-04T15:00:00Z", "published": True,
            "wrote_events_json": True, "overall_status": "healthy"}
    data.update(overrides)
    (repo / "state" / "last_run.json").write_text(json.dumps(data))


def test_lock_contention_does_not_start_commands(tmp_path):
    calls = []
    runner, _ = make_runner(tmp_path, lambda args, env: calls.append(args) or 0)
    runner.config.lock_path.parent.mkdir(parents=True)
    with runner.config.lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert runner.run() == 1
    assert calls == []


def test_preflight_repairs_then_smokes(tmp_path):
    calls = []
    outcomes = iter([1, 0, 0])
    runner, _ = make_runner(tmp_path, lambda args, env: calls.append((args, env)) or next(outcomes))
    runner.preflight()
    assert [args[1:3] for args, _ in calls] == [
        ["-c", "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()"],
        ["-m", "playwright"],
        ["-c", "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()"],
    ]
    assert all(env["PLAYWRIGHT_BROWSERS_PATH"] == str(runner.config.browser_path)
               for _, env in calls)


def test_preflight_repair_failure(tmp_path):
    runner, _ = make_runner(tmp_path, lambda args, env: 1)
    try:
        runner.preflight()
    except runner_module.OperationalError as exc:
        assert exc.stage == "browser-repair"
    else:
        assert False, "repair failure must be operational"


def test_streamed_child_output_is_sanitized_and_rotated(tmp_path, monkeypatch, capsys):
    runner, _ = make_runner(tmp_path)
    monkeypatch.setattr(runner_module, "MAX_LOG_BYTES", 150)
    monkeypatch.setattr(runner_module, "MAX_LOG_BACKUPS", 2)
    raw_lines = [
        "token=raw-token password=raw-password https://example.test/private\n"
        for _ in range(12)
    ]

    class FakeProcess:
        stdout = raw_lines

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def wait(self):
            return 0

    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    assert runner._stream_command(["fake-child"], runner._env()) == 0
    displayed = capsys.readouterr().out
    log_files = list(runner.config.log_path.parent.glob("the-word-scrape.log*"))
    retained = "".join(path.read_text() for path in log_files)
    assert "raw-token" not in displayed + retained
    assert "raw-password" not in displayed + retained
    assert "https://example.test/private" not in displayed + retained
    assert "token=[redacted]" in retained
    assert "password=[redacted]" in retained
    assert "[url]" in retained
    assert len(log_files) == 3
    assert all(path.stat().st_size <= 150 for path in log_files)
    assert sum(path.stat().st_size for path in log_files) <= 150 * 3


def test_streamed_credentials_never_reach_console_or_log(tmp_path, monkeypatch, capsys):
    runner, _ = make_runner(tmp_path)
    raw_lines = [
        "Authorization: Bearer authorization-secret\n",
        "api_key=api-key-secret&access_token=access-token-secret&bot_token=bot-token-secret\n",
        "password=password-secret https://example.test/path?token=url-token-secret\n",
        "GITHUB_TOKEN=github-token-secret OPENAI_API_KEY=openai-key-secret "
        "TELEGRAM_BOT_TOKEN=telegram-token-secret MixedCaseSecret=mixed-secret\n",
    ]

    class FakeProcess:
        stdout = raw_lines

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def wait(self):
            return 0

    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    runner._stream_command(["fake-child"], runner._env())
    output = capsys.readouterr().out + runner.config.log_path.read_text()
    for secret in (
        "authorization-secret", "api-key-secret", "access-token-secret",
        "bot-token-secret", "password-secret", "url-token-secret", "github-token-secret",
        "openai-key-secret", "telegram-token-secret", "mixed-secret",
    ):
        assert secret not in output
    assert "Authorization=[redacted]" in output
    assert "api_key=[redacted]" in output
    assert "access_token=[redacted]" in output
    assert "bot_token=[redacted]" in output
    assert "password=[redacted]" in output
    assert "GITHUB_TOKEN=[redacted]" in output
    assert "OPENAI_API_KEY=[redacted]" in output
    assert "TELEGRAM_BOT_TOKEN=[redacted]" in output
    assert "MixedCaseSecret=[redacted]" in output


def test_streaming_normalizes_oversized_and_surplus_old_logs(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path)
    monkeypatch.setattr(runner_module, "MAX_LOG_BYTES", 100)
    monkeypatch.setattr(runner_module, "MAX_LOG_BACKUPS", 2)
    log = runner.config.log_path
    log.parent.mkdir(parents=True)
    log.write_text("legacy-raw-secret\n" * 20)
    log.with_name(log.name + ".1").write_text("legacy-raw-secret\n" * 20)
    log.with_name(log.name + ".2").write_text("safe retained backup\n")
    log.with_name(log.name + ".3").write_text("surplus backup\n")
    log.with_name(log.name + ".99").write_text("surplus backup\n")

    class FakeProcess:
        stdout = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def wait(self):
            return 0

    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    assert runner._stream_command(["fake-child"], runner._env()) == 0
    retained = list(log.parent.glob("the-word-scrape.log*"))
    assert len(retained) == 1
    assert retained[0].name.endswith(".2")
    assert all(path.stat().st_size <= 100 for path in retained)
    assert sum(path.stat().st_size for path in retained) <= 100 * 3
    assert "legacy-raw-secret" not in "".join(path.read_text() for path in retained)


def test_pipeline_exit_one_fails(tmp_path):
    runner, _ = make_runner(tmp_path, lambda args, env: 0 if "-c" in args else 1)
    assert runner.run() == 1


def test_exit_two_with_verified_degraded_publish_is_success(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, lambda args, env: 0 if "-c" in args else 2)
    health(runner.config.repo, overall_status="degraded")
    monkeypatch.setattr(runner, "remote_proof", lambda: None)
    assert runner.run() == 0


def test_exit_two_without_publish_proof_fails(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, lambda args, env: 0 if "-c" in args else 2)
    health(runner.config.repo, published=False, overall_status="degraded")
    monkeypatch.setattr(runner, "remote_proof", lambda: None)
    assert runner.run() == 1


def test_stale_and_malformed_health_fail(tmp_path):
    runner, _ = make_runner(tmp_path)
    health(runner.config.repo, run_at="2026-08-03T15:00:00Z")
    try:
        runner.validate_health()
    except runner_module.OperationalError:
        pass
    else:
        assert False
    (runner.config.repo / "state" / "last_run.json").write_text("{")
    try:
        runner.validate_health()
    except runner_module.OperationalError:
        pass
    else:
        assert False


def test_remote_head_mismatch_fails(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, lambda args, env: 0)
    class Result:
        returncode = 0
        def __init__(self, stdout): self.stdout, self.stderr = stdout, ""
    values = iter(["local\n", "remote\n"])
    monkeypatch.setattr(runner_module.subprocess, "run", lambda *a, **k: Result(next(values)))
    try:
        runner.remote_proof()
    except runner_module.OperationalError as exc:
        assert exc.stage == "remote-proof"
    else:
        assert False


def test_remote_bad_current_date_subject_fails(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, lambda args, env: 0)
    class Result:
        returncode = 0
        def __init__(self, stdout): self.stdout, self.stderr = stdout, ""
    values = iter(["same\n", "same\n", "Events update: 2026-08-03 (10 events)\n"])
    monkeypatch.setattr(runner_module.subprocess, "run", lambda *a, **k: Result(next(values)))
    try:
        runner.remote_proof()
    except runner_module.OperationalError as exc:
        assert exc.reason == "HEAD is not today's Events update"
    else:
        assert False


def test_remote_exact_publisher_subject_is_accepted(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, lambda args, env: 0)
    local = '{"events": []}\n'
    (runner.config.repo / "docs" / "events.json").write_text(local)

    class Result:
        returncode = 0

        def __init__(self, stdout):
            self.stdout, self.stderr = stdout, ""

    values = iter([
        "same\n", "same\n", "Events update: 2026-08-04 (10 events from 2 sources)\n", local,
    ])
    monkeypatch.setattr(runner_module.subprocess, "run", lambda *a, **k: Result(next(values)))
    runner.remote_proof()


def test_remote_subject_with_only_expected_prefix_fails(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, lambda args, env: 0)

    class Result:
        returncode = 0

        def __init__(self, stdout):
            self.stdout, self.stderr = stdout, ""

    values = iter([
        "same\n", "same\n",
        "prefix Events update: 2026-08-04 (10 events from 2 sources) suffix\n",
    ])
    monkeypatch.setattr(runner_module.subprocess, "run", lambda *a, **k: Result(next(values)))
    try:
        runner.remote_proof()
    except runner_module.OperationalError as exc:
        assert exc.reason == "HEAD is not today's Events update"
    else:
        assert False


def test_remote_events_blob_mismatch_fails(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, lambda args, env: 0)
    (runner.config.repo / "docs" / "events.json").write_text('{"local": true}\n')
    class Result:
        returncode = 0
        def __init__(self, stdout): self.stdout, self.stderr = stdout, ""
    values = iter([
        "same\n", "same\n", "Events update: 2026-08-04 (10 events from 2 sources)\n", '{"remote": true}\n',
    ])
    monkeypatch.setattr(runner_module.subprocess, "run", lambda *a, **k: Result(next(values)))
    try:
        runner.remote_proof()
    except runner_module.OperationalError as exc:
        assert exc.reason == "HEAD docs/events.json differs from working copy"
    else:
        assert False


def test_telegram_uses_actual_home_channel_key_without_network(tmp_path, monkeypatch):
    calls = []
    class Response:
        is_success = True
    class FakeHttpx:
        @staticmethod
        def post(url, data, timeout):
            calls.append((url, data, timeout))
            return Response()
    monkeypatch.setitem(sys.modules, "httpx", FakeHttpx)
    assert runner_module.telegram_sender(
        {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_HOME_CHANNEL": "home-channel",
         "TELEGRAM_CHAT_ID": "legacy-channel"}, "test message"
    )
    assert calls[0][1]["chat_id"] == "home-channel"


def test_alert_dedup_changed_reminder_recovery(tmp_path):
    notices = []
    runner, notices = make_runner(tmp_path, notices=notices)
    failure = runner_module.OperationalError("pipeline", "bad one")
    runner._alert(failure)
    runner._alert(failure)
    assert len(notices) == 1
    runner._alert(runner_module.OperationalError("pipeline", "bad two"))
    assert len(notices) == 2
    runner.now = lambda: NOW.replace(day=5)
    runner._alert(runner_module.OperationalError("pipeline", "bad two"))
    assert len(notices) == 3
    runner._alert(None)
    assert len(notices) == 4
    assert not (runner.config.state_dir / "the-word-alert.json").exists()


def test_notification_failure_preserves_operational_failure(tmp_path):
    runner, _ = make_runner(tmp_path, command=lambda args, env: 1,
                            notices=[])
    def failing_notifier(env, message):
        raise RuntimeError("notification unavailable")
    runner.notifier = failing_notifier
    assert runner.run(preflight_only=True) == 1
    state = json.loads((runner.config.state_dir / "the-word-alert.json").read_text())
    assert state["notification_failed"] is True


def test_command_launch_exception_is_alerted_and_nonzero(tmp_path):
    notices = []
    def missing_python(args, env):
        raise FileNotFoundError("missing python")
    runner, notices = make_runner(tmp_path, command=missing_python, notices=notices)
    assert runner.run(preflight_only=True) == 1
    assert len(notices) == 1
    assert "runner:" in notices[0]


def test_alert_state_exception_does_not_escape_or_return_zero(tmp_path, monkeypatch):
    runner, _ = make_runner(tmp_path, command=lambda args, env: 1)
    monkeypatch.setattr(runner_module.json, "dumps", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("state unavailable")))
    assert runner.run(preflight_only=True) == 1
