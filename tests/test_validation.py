"""Tests for strict event validation + sanitization."""

from the_word.validation import validate_and_sanitize


def _ev(**overrides):
    base = {
        "name": "Show",
        "dateTime": "2026-05-01T20:00:00",
        "venue": "First Avenue",
    }
    base.update(overrides)
    return base


def test_keeps_valid_event():
    kept, dropped = validate_and_sanitize([_ev()])
    assert len(kept) == 1
    assert not dropped


def test_drops_missing_required_field():
    kept, dropped = validate_and_sanitize([{"name": "X"}])
    assert len(kept) == 0
    assert len(dropped) == 1


def test_drops_unparseable_datetime():
    kept, dropped = validate_and_sanitize([_ev(dateTime="next Friday")])
    assert len(kept) == 0
    assert "invalid dateTime" in dropped[0]


def test_drops_invalid_source_url():
    kept, dropped = validate_and_sanitize([_ev(sourceUrl="not-a-url")])
    assert len(kept) == 0
    assert "invalid sourceUrl" in dropped[0]


def test_drops_invalid_image_url():
    kept, dropped = validate_and_sanitize([_ev(imageUrl="ftp://bad/scheme.png")])
    assert len(kept) == 0


def test_strips_whitespace_in_name_and_venue():
    kept, _ = validate_and_sanitize(
        [_ev(name="  Loud    Show  ", venue="  Venue   A  ")]
    )
    assert kept[0]["name"] == "Loud Show"
    assert kept[0]["venue"] == "Venue A"


def test_drops_empty_optionals():
    ev = _ev(description="", address="", imageUrl=None, sourceUrl=None)
    kept, _ = validate_and_sanitize([ev])
    assert "description" not in kept[0]
    assert "address" not in kept[0]
    assert "imageUrl" not in kept[0]
    assert "sourceUrl" not in kept[0]


def test_ensures_tags_is_list():
    kept, _ = validate_and_sanitize([_ev()])
    assert kept[0]["tags"] == []


def test_drops_non_string_tags():
    kept, dropped = validate_and_sanitize([_ev(tags=["Music", 123])])
    assert len(kept) == 0
    assert "tags must be a list of strings" in dropped[0]


def test_accepts_datetime_with_z_suffix():
    kept, _ = validate_and_sanitize([_ev(dateTime="2026-05-01T20:00:00Z")])
    assert len(kept) == 1
    # Canonicalized (Z stripped)
    assert kept[0]["dateTime"] == "2026-05-01T20:00:00"


def test_truncates_overlong_description():
    kept, _ = validate_and_sanitize([_ev(description="x" * 2000)])
    assert len(kept[0]["description"]) <= 600
