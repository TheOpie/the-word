"""Tests for the events.json writer — min-event guard, schema validation."""

import json
import tempfile
from pathlib import Path

from the_word.writer import write_events, validate_events_json


def make_events(n):
    """Generate n valid test events."""
    return [
        {
            "name": f"Event {i}",
            "dateTime": f"2026-03-{28+i:02d}T20:00:00",
            "venue": f"Venue {i}",
            "description": "Test",
            "sourceUrl": "https://example.com",
            "tags": ["Music"],
        }
        for i in range(n)
    ]


class TestMinEventGuard:
    def test_below_minimum_preserves_old(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(make_events(10), f)
            path = Path(f.name)

        # Try writing only 3 events
        result = write_events(make_events(3), path)
        assert result is False

        # Old data should be preserved
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 10
        path.unlink()

    def test_above_minimum_writes(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump([], f)
            path = Path(f.name)

        events = make_events(7)
        result = write_events(events, path)
        assert result is True

        with open(path) as f:
            data = json.load(f)
        assert len(data) == 7
        path.unlink()

    def test_exactly_minimum_writes(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump([], f)
            path = Path(f.name)

        events = make_events(5)
        result = write_events(events, path)
        assert result is True
        path.unlink()


class TestSchemaValidation:
    def test_valid_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(make_events(5), f)
            path = Path(f.name)

        valid, count, errors = validate_events_json(path)
        assert valid is True
        assert count == 5
        assert errors == []
        path.unlink()

    def test_missing_required_field(self):
        events = [{"name": "Test", "dateTime": "2026-03-28T20:00:00"}]  # no venue
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(events, f)
            path = Path(f.name)

        valid, count, errors = validate_events_json(path)
        assert valid is False
        assert len(errors) == 1
        path.unlink()

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("not json{{{")
            path = Path(f.name)

        valid, count, errors = validate_events_json(path)
        assert valid is False
        path.unlink()

    def test_drops_invalid_on_write(self):
        events = make_events(6)
        events.append({"name": "Bad Event"})  # missing venue and dateTime
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump([], f)
            path = Path(f.name)

        write_events(events, path)
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 6  # bad event dropped
        path.unlink()
