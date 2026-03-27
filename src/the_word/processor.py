"""Event processor — dedup, consolidation, venue tagging, filtering."""

import re
from datetime import datetime, timedelta
from pathlib import Path

import yaml


def process_events(raw_events: list[dict], venues_yaml: Path) -> list[dict]:
    """Full processing pipeline: tag → dedup → consolidate → filter → sort."""
    with open(venues_yaml) as f:
        config = yaml.safe_load(f)

    venue_map = config.get("venues", {})
    keyword_map = config.get("keywords", {})
    fallback_words = config.get("fallback_venue_words", [])
    fallback_tags = config.get("fallback_tags", [])

    # 1. Apply venue/keyword tagging
    events = [_apply_tags(e, venue_map, keyword_map, fallback_words, fallback_tags) for e in raw_events]

    # 2. Filter to 7-day rolling window from today
    events = _filter_date_window(events)

    # 3. Deduplicate
    events = _deduplicate(events)

    # 4. Theater consolidation
    events = _consolidate_theater(events)

    # 5. Sort by dateTime
    events.sort(key=lambda e: e.get("dateTime", ""))

    return events


def _normalize_name(name: str) -> str:
    """Normalize event name for comparison."""
    name = name.lower().strip()
    # Strip common prefixes
    for prefix in ["presents:", "presents ", "live:", "live "]:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
    # Remove extra whitespace
    name = re.sub(r"\s+", " ", name)
    return name


def _apply_tags(
    event: dict,
    venue_map: dict,
    keyword_map: dict,
    fallback_words: list,
    fallback_tags: list,
) -> dict:
    """Apply tags based on venue name and event name keywords."""
    tags = set(event.get("tags", []))
    venue = (event.get("venue") or "").lower().strip()
    name = (event.get("name") or "").lower()
    desc = (event.get("description") or "").lower()
    text = f"{name} {desc}"

    # Venue matching (partial match — venue map key appears in venue name)
    for venue_key, venue_tags in venue_map.items():
        if venue_key in venue:
            tags.update(venue_tags)

    # Keyword matching
    for keyword, kw_tags in keyword_map.items():
        if keyword in text:
            tags.update(kw_tags)

    # Fallback: check venue name for generic words
    if not tags:
        for word in fallback_words:
            if word in venue:
                tags.update(fallback_tags)
                break

    event["tags"] = sorted(tags)
    return event


def _filter_date_window(events: list[dict]) -> list[dict]:
    """Keep only events within the next 7 days starting from today."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = today + timedelta(days=7)
    filtered = []

    for event in events:
        dt_str = event.get("dateTime", "")
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00").replace("+00:00", ""))
        except (ValueError, AttributeError):
            continue  # Drop events with unparseable dates

        if today <= dt < end:
            filtered.append(event)

    dropped = len(events) - len(filtered)
    if dropped:
        print(f"  Filtered out {dropped} events outside 7-day window")

    return filtered


def _deduplicate(events: list[dict]) -> list[dict]:
    """Deduplicate events by normalized name + venue + date (same calendar day)."""
    seen = {}
    deduped = []

    for event in events:
        key = _dedup_key(event)
        if key not in seen:
            seen[key] = True
            deduped.append(event)

    dupes = len(events) - len(deduped)
    if dupes:
        print(f"  Removed {dupes} duplicate events")

    return deduped


def _dedup_key(event: dict) -> str:
    """Generate dedup key: normalized name + venue + calendar day."""
    name = _normalize_name(event.get("name", ""))
    venue = (event.get("venue") or "").lower().strip()
    dt_str = event.get("dateTime", "")
    try:
        day = dt_str[:10]  # YYYY-MM-DD
    except (TypeError, IndexError):
        day = ""
    return f"{name}|{venue}|{day}"


def _consolidate_theater(events: list[dict]) -> list[dict]:
    """Group recurring shows (same name + venue, different dates) into single entry with dateRange."""
    groups = {}
    standalone = []

    for event in events:
        key = f"{_normalize_name(event.get('name', ''))}|{(event.get('venue') or '').lower().strip()}"
        if key not in groups:
            groups[key] = []
        groups[key].append(event)

    for key, group in groups.items():
        if len(group) == 1:
            standalone.append(group[0])
        else:
            # Multiple dates for same show — consolidate
            dates = []
            for e in group:
                try:
                    dates.append(e["dateTime"][:10])
                except (KeyError, TypeError):
                    pass

            dates.sort()
            base = group[0].copy()
            if len(dates) >= 2:
                start = datetime.strptime(dates[0], "%Y-%m-%d")
                end = datetime.strptime(dates[-1], "%Y-%m-%d")
                base["dateRange"] = f"{start.strftime('%b %d')} – {end.strftime('%b %d')}"
            base["dateTime"] = group[0]["dateTime"]  # Keep earliest
            standalone.append(base)
            consolidated_count = len(group) - 1
            if consolidated_count:
                print(f"  Consolidated '{group[0].get('name', '?')}': {len(group)} dates → 1 card")

    return standalone
