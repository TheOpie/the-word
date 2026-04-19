"""Tests for per-source state: rolling history, fallback cache, baseline."""

import json
import tempfile
from pathlib import Path

from the_word.state import PipelineState, SourceState


def test_new_state_empty(tmp_path):
    state = PipelineState.load(tmp_path / "missing.json")
    assert state.sources == {}


def test_record_run_tracks_history(tmp_path):
    state = PipelineState(path=tmp_path / "s.json", sources={})
    s = state.get("First Avenue")
    s.record_run(20, "ok")
    s.record_run(25, "ok")
    assert len(s.runs) == 2
    assert s.runs[-1]["count"] == 25


def test_consecutive_empty_streak(tmp_path):
    s = SourceState(name="Songkick")
    s.record_run(10, "ok")
    assert s.consecutive_empty == 0
    s.record_run(0, "empty")
    s.record_run(0, "failed")
    s.record_run(0, "fallback")
    assert s.consecutive_empty == 3
    s.record_run(5, "ok")
    assert s.consecutive_empty == 0


def test_ok_with_zero_count_counts_as_empty_streak():
    s = SourceState(name="X")
    s.record_run(10, "ok")
    s.record_run(0, "ok")
    assert s.consecutive_empty == 1


def test_rolling_window_trimmed():
    s = SourceState(name="X")
    for i in range(20):
        s.record_run(i + 1, "ok")
    assert len(s.runs) == 14  # ROLLING_WINDOW
    assert s.runs[0]["count"] == 7  # 20 - 14 + 1


def test_baseline_uses_median_of_productive():
    s = SourceState(name="X")
    for count in [10, 12, 15, 11, 0, 0, 13]:
        s.record_run(count, "ok" if count > 0 else "empty")
    # Only productive 'ok' runs count: [10,12,15,11,13] → median 12
    assert s.baseline_count() == 12


def test_baseline_zero_when_no_productive_runs():
    s = SourceState(name="X")
    s.record_run(0, "empty")
    s.record_run(0, "failed")
    assert s.baseline_count() == 0
    assert not s.is_historically_productive()


def test_update_cache_replaces_events():
    s = SourceState(name="X")
    s.update_cache([{"name": "A"}, {"name": "B"}])
    assert len(s.last_known_good_events) == 2
    assert s.last_known_good_at is not None

    s.update_cache([{"name": "C"}])
    assert len(s.last_known_good_events) == 1


def test_update_cache_ignores_empty():
    s = SourceState(name="X")
    s.update_cache([{"name": "A"}])
    old_at = s.last_known_good_at
    s.update_cache([])
    assert s.last_known_good_at == old_at  # unchanged


def test_roundtrip_save_load(tmp_path):
    state = PipelineState(path=tmp_path / "s.json", sources={})
    s = state.get("First Avenue")
    s.record_run(20, "ok")
    s.update_cache([{"name": "Evt", "dateTime": "2026-05-01T20:00:00", "venue": "V"}])
    state.save()

    reloaded = PipelineState.load(tmp_path / "s.json")
    rs = reloaded.sources["First Avenue"]
    assert rs.runs[0]["count"] == 20
    assert len(rs.last_known_good_events) == 1


def test_load_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not valid json")
    state = PipelineState.load(p)
    assert state.sources == {}


def test_load_wrong_version_returns_empty(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"version": 999, "sources": {"X": {}}}))
    state = PipelineState.load(p)
    assert state.sources == {}
