"""Tests for publish-time quality gates."""

from the_word.quality_gate import GateThresholds, evaluate


def _ev(name="Show", venue="First Avenue", source_url="https://x.com/a"):
    e = {"name": name, "dateTime": "2026-05-01T20:00:00", "venue": venue}
    if source_url is not None:
        e["sourceUrl"] = source_url
    return e


def _set(n, venue_pattern=lambda i: "First Avenue", source_url=lambda i: "https://x.com/a"):
    return [_ev(name=f"E{i}", venue=venue_pattern(i), source_url=source_url(i)) for i in range(n)]


def test_passes_clean_snapshot():
    new = _set(20, venue_pattern=lambda i: f"Venue {i % 5}")
    prev = _set(22, venue_pattern=lambda i: f"Venue {i % 5}")
    r = evaluate(new, prev)
    assert r.passed, [v.detail for v in r.violations]


def test_blocks_below_min_event_count():
    new = _set(3, venue_pattern=lambda i: f"V{i}")
    r = evaluate(new, None)
    assert not r.passed
    assert any(v.rule == "min_event_count" for v in r.violations)


def test_blocks_below_min_unique_venues():
    new = _set(20, venue_pattern=lambda i: "Single Venue")
    # Disable single-venue dominance to isolate the unique-venue rule
    t = GateThresholds(max_single_venue_share=1.0)
    r = evaluate(new, None, t)
    assert not r.passed
    assert any(v.rule == "min_unique_venues" for v in r.violations)


def test_blocks_single_venue_dominance():
    new = (
        _set(14, venue_pattern=lambda i: "331 Club")
        + _set(1, venue_pattern=lambda i: "Other")
    )
    r = evaluate(new, None)
    assert not r.passed
    assert any(v.rule == "single_venue_dominance" for v in r.violations)


def test_blocks_coverage_drop():
    prev = _set(40, venue_pattern=lambda i: f"V{i % 5}")
    new = _set(15, venue_pattern=lambda i: f"V{i % 5}")
    r = evaluate(new, prev)
    assert not r.passed
    assert any(v.rule == "coverage_drop" for v in r.violations)


def test_skips_relative_rule_when_previous_too_small():
    prev = _set(3, venue_pattern=lambda i: f"V{i}")
    new = _set(7, venue_pattern=lambda i: f"V{i}")
    r = evaluate(new, prev)
    # Relative rules should not fire when previous is below threshold
    assert all(v.rule != "coverage_drop" for v in r.violations)


def test_blocks_source_url_density_too_low():
    new = _set(20, venue_pattern=lambda i: f"V{i % 5}", source_url=lambda i: None)
    r = evaluate(new, None)
    assert not r.passed
    assert any(v.rule == "source_url_density" for v in r.violations)


def test_blocks_source_url_density_drop():
    prev = _set(40, venue_pattern=lambda i: f"V{i % 5}", source_url=lambda i: "https://x.com/a")
    # Most events lose sourceUrl
    new = _set(40, venue_pattern=lambda i: f"V{i % 5}",
               source_url=lambda i: "https://x.com/a" if i < 8 else None)
    r = evaluate(new, prev)
    assert not r.passed
    assert any(v.rule == "source_url_density_drop" for v in r.violations)


def test_no_previous_skips_all_relative_rules():
    new = _set(20, venue_pattern=lambda i: f"V{i % 5}")
    r = evaluate(new, None)
    assert r.passed


def test_force_field_default_false():
    r = evaluate(_set(20, venue_pattern=lambda i: f"V{i % 5}"), None)
    assert r.forced is False


def test_stats_carry_top_venue_share():
    new = (
        _set(8, venue_pattern=lambda i: "Big Venue")
        + _set(12, venue_pattern=lambda i: f"Other {i}")
    )
    r = evaluate(new, None)
    assert r.stats["new"]["top_venue"] == "Big Venue"
    assert abs(r.stats["new"]["top_venue_share"] - 8 / 20) < 1e-9


def test_codex_failure_case_blocks():
    """The exact regression Codex flagged: 41 → 15 with 14/15 from one venue."""
    prev = _set(41, venue_pattern=lambda i: f"Venue {i % 22}", source_url=lambda i: "https://x.com/a" if i < 27 else None)
    new = (
        [_ev(name=f"C{i}", venue="331 Club", source_url=None) for i in range(14)]
        + [_ev(name="O1", venue="Other Venue", source_url="https://x.com/o1")]
    )
    r = evaluate(new, prev)
    assert not r.passed
    rules = {v.rule for v in r.violations}
    # Should trip: min_unique_venues, single_venue_dominance, coverage_drop,
    # source_url_density, source_url_density_drop
    assert "single_venue_dominance" in rules
    assert "coverage_drop" in rules
    assert "source_url_density" in rules
