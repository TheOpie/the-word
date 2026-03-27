"""Events.json writer with minimum-event guard and schema validation."""

import json
from pathlib import Path

REQUIRED_FIELDS = {"name", "dateTime", "venue"}
MIN_EVENTS = 5


def write_events(events: list[dict], output_path: Path) -> bool:
    """Write events to JSON file. Returns False if below minimum threshold.

    If the new event count is below MIN_EVENTS, the previous file is preserved.
    """
    if len(events) < MIN_EVENTS:
        print(f"  WARNING: Below minimum threshold ({len(events)} < {MIN_EVENTS}), keeping previous data.")
        return False

    # Final schema validation pass
    valid = []
    for event in events:
        if all(event.get(f) for f in REQUIRED_FIELDS):
            valid.append(event)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(valid, f, indent=2, ensure_ascii=False)

    return True


def validate_events_json(path: Path) -> tuple[bool, int, list[str]]:
    """Validate an existing events.json file. Returns (valid, count, errors)."""
    errors = []

    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, 0, [f"Invalid JSON: {e}"]

    if not isinstance(data, list):
        return False, 0, ["Root element is not an array"]

    for i, event in enumerate(data):
        if not isinstance(event, dict):
            errors.append(f"Event {i}: not an object")
            continue
        for field in REQUIRED_FIELDS:
            if not event.get(field):
                errors.append(f"Event {i} ({event.get('name', '?')}): missing {field}")

    return len(errors) == 0, len(data), errors
