"""Strict per-event validation + sanitization.

Ensures events leaving the structurer meet schema guarantees before they reach
processing, image enrichment, or the public events.json. We prefer dropping a
suspect event over propagating bad data.
"""

from __future__ import annotations

from datetime import datetime
import ipaddress
import re
from urllib.parse import urlparse

MAX_NAME_LEN = 200
MAX_VENUE_LEN = 150
MAX_DESC_LEN = 600

REQUIRED_FIELDS = ("name", "dateTime", "venue")


def validate_and_sanitize(events: list[dict]) -> tuple[list[dict], list[str]]:
    """Return (kept_events, dropped_reasons). Modifies events in place."""
    kept: list[dict] = []
    reasons: list[str] = []

    for idx, event in enumerate(events):
        err = _validate_event(event)
        if err:
            label = event.get("name") or f"event#{idx}"
            reasons.append(f"{label}: {err}")
            continue
        kept.append(_sanitize(event))

    return kept, reasons


def _validate_event(event: dict) -> str | None:
    if not isinstance(event, dict):
        return "not an object"

    for field in REQUIRED_FIELDS:
        value = event.get(field)
        if not value or not isinstance(value, str):
            return f"missing required field: {field}"

    # dateTime must parse as ISO 8601
    dt_raw = event["dateTime"]
    try:
        _parse_iso(dt_raw)
    except ValueError as e:
        return f"invalid dateTime '{dt_raw}': {e}"

    # tags must be a list of strings if present
    tags = event.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)
    ):
        return "tags must be a list of strings"

    # Empty optional URLs are intentionally removed during sanitization, but a
    # nonempty malformed URL invalidates the event before it enters the pipeline.
    for field in ("sourceUrl", "imageUrl"):
        value = event.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not isinstance(value, str) or not _is_valid_url(value):
            return f"invalid {field}"

    return None


def _sanitize(event: dict) -> dict:
    """Normalize whitespace, truncate overlong fields, drop empty optional keys."""

    def clean_str(v, max_len):
        if not isinstance(v, str):
            return v
        cleaned = " ".join(v.split())
        return cleaned[:max_len]

    event["name"] = clean_str(event["name"], MAX_NAME_LEN)
    event["venue"] = clean_str(event["venue"], MAX_VENUE_LEN)
    if "description" in event and event["description"] is not None:
        event["description"] = clean_str(event["description"], MAX_DESC_LEN)

    # Normalize dateTime to canonical form (strip Z, reformat)
    try:
        dt = _parse_iso(event["dateTime"])
        event["dateTime"] = dt.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        pass  # Shouldn't happen — validate runs first

    # Drop empty optional keys. Nonempty malformed URLs were rejected above.
    for k in ("address", "description"):
        if k in event and (event[k] is None or event[k] == ""):
            del event[k]
    for k in ("sourceUrl", "imageUrl"):
        if k in event:
            v = event[k]
            if not isinstance(v, str) or not v.strip() or not _is_valid_url(v):
                del event[k]

    # Ensure tags is a list (not missing)
    if "tags" not in event or event["tags"] is None:
        event["tags"] = []

    return event


def _parse_iso(value: str) -> datetime:
    """Parse ISO 8601 datetime, accepting common variants."""
    if not isinstance(value, str):
        raise ValueError("not a string")
    # Normalize trailing Z or timezone offset, Python fromisoformat is strict pre-3.11
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1]
    # Remove tz suffix like +00:00 for naive compare
    if len(v) >= 6 and (v[-6] == "+" or v[-6] == "-") and v[-3] == ":":
        v = v[:-6]
    try:
        return datetime.fromisoformat(v)
    except ValueError as e:
        raise ValueError(str(e))


def _is_valid_url(url: str) -> bool:
    """Accept only canonical, credential-free HTTP(S) URLs with a valid host."""
    if not isinstance(url, str) or url != url.strip():
        return False
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        return False
    try:
        parsed = urlparse(url)
        # Accessing ``port`` deliberately validates its numeric range.
        port = parsed.port
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if parsed.netloc.endswith(":"):
        return False
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return False
    if port is not None and not 0 < port <= 65535:
        return False
    return _is_valid_hostname(parsed.hostname)


def _is_valid_hostname(hostname: str) -> bool:
    """Accept valid IP literals and DNS labels, including localhost."""
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    try:
        host = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if host.endswith("."):
        host = host[:-1]
    if not host or len(host) > 253:
        return False
    label = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
    return all(label.fullmatch(part) is not None for part in host.split("."))
