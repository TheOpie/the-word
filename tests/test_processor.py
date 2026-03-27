"""Tests for the event processor — dedup, consolidation, tagging."""

import json
from datetime import datetime, timedelta
from pathlib import Path

from the_word.processor import (
    process_events,
    _normalize_name,
    _deduplicate,
    _consolidate_theater,
    _apply_tags,
    _dedup_key,
)

VENUES_YAML = Path(__file__).parent.parent / "config" / "venues.yaml"
SAMPLE_EVENTS = Path(__file__).parent / "sample_events.json"


def load_sample():
    with open(SAMPLE_EVENTS) as f:
        return json.load(f)


def make_event(name="Test", venue="First Avenue", days_from_now=1, tags=None):
    """Create a test event N days from now."""
    dt = datetime.now() + timedelta(days=days_from_now)
    return {
        "name": name,
        "dateTime": dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "venue": venue,
        "description": "Test",
        "sourceUrl": "https://example.com",
        "tags": tags or [],
    }


class TestNormalizeName:
    def test_basic(self):
        assert _normalize_name("Hello World") == "hello world"

    def test_presents_prefix(self):
        assert _normalize_name("Presents: Big Show") == "big show"

    def test_extra_whitespace(self):
        assert _normalize_name("  Too   Many   Spaces  ") == "too many spaces"


class TestDedup:
    def test_removes_exact_dupes(self):
        e1 = make_event("Show", "Venue A")
        e2 = make_event("Show", "Venue A")
        result = _deduplicate([e1, e2])
        assert len(result) == 1

    def test_keeps_different_venues(self):
        e1 = make_event("Show", "Venue A")
        e2 = make_event("Show", "Venue B")
        result = _deduplicate([e1, e2])
        assert len(result) == 2

    def test_removes_presents_prefix_dupe(self):
        e1 = make_event("Big Concert", "First Avenue")
        e2 = make_event("Presents: Big Concert", "First Avenue")
        result = _deduplicate([e1, e2])
        assert len(result) == 1

    def test_different_days_not_deduped(self):
        e1 = make_event("Show", "Venue A", days_from_now=1)
        e2 = make_event("Show", "Venue A", days_from_now=2)
        result = _deduplicate([e1, e2])
        assert len(result) == 2


class TestTheaterConsolidation:
    def test_consolidates_recurring_shows(self):
        events = [
            make_event("Hamlet", "Guthrie Theater", days_from_now=1),
            make_event("Hamlet", "Guthrie Theater", days_from_now=2),
            make_event("Hamlet", "Guthrie Theater", days_from_now=3),
        ]
        result = _consolidate_theater(events)
        assert len(result) == 1
        assert "dateRange" in result[0]

    def test_no_consolidation_for_single(self):
        events = [make_event("One Night Only", "Guthrie Theater")]
        result = _consolidate_theater(events)
        assert len(result) == 1
        assert "dateRange" not in result[0]


class TestTagging:
    def test_venue_tagging(self):
        event = make_event("Something", "First Avenue")
        import yaml
        with open(VENUES_YAML) as f:
            config = yaml.safe_load(f)
        result = _apply_tags(
            event,
            config["venues"],
            config["keywords"],
            config.get("fallback_venue_words", []),
            config.get("fallback_tags", []),
        )
        assert "Music" in result["tags"]

    def test_keyword_tagging(self):
        event = make_event("Comedy Night", "Unknown Venue")
        import yaml
        with open(VENUES_YAML) as f:
            config = yaml.safe_load(f)
        result = _apply_tags(
            event,
            config["venues"],
            config["keywords"],
            config.get("fallback_venue_words", []),
            config.get("fallback_tags", []),
        )
        assert "Comedy" in result["tags"]

    def test_fallback_tagging(self):
        event = make_event("Random Event", "Weird Bar Name")
        import yaml
        with open(VENUES_YAML) as f:
            config = yaml.safe_load(f)
        result = _apply_tags(
            event,
            config["venues"],
            config["keywords"],
            config.get("fallback_venue_words", []),
            config.get("fallback_tags", []),
        )
        assert "Music" in result["tags"]


class TestFullProcessing:
    def test_end_to_end(self):
        events = [
            make_event("Concert", "First Avenue", 1, []),
            make_event("Concert", "First Avenue", 1, []),  # dupe
            make_event("Hamlet", "Guthrie Theater", 1, []),
            make_event("Hamlet", "Guthrie Theater", 2, []),
            make_event("Hamlet", "Guthrie Theater", 3, []),
            make_event("Comedy Show", "Acme Comedy", 1, []),
        ]
        result = process_events(events, VENUES_YAML)
        # Should have: 1 concert + 1 consolidated Hamlet + 1 comedy = 3
        assert len(result) == 3
        # All should have tags
        for e in result:
            assert len(e["tags"]) > 0
