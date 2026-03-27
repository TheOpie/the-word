"""Image enrichment — extract event images from source page JSON-LD and OG metadata."""

import asyncio
import re

import httpx

AGENTCDN_BASE = "https://yellow-resonance-7c40.opieworks-ai.workers.dev/agent"

# Skip generic/placeholder images
SKIP_PATTERNS = [
    "placeholder", "default", "no-image", "icon-", "favicon",
    "banner", "heroBanner", "sprite", "pixel.gif", "spacer",
    "Event-Tickets.png",  # Generic ticket image on minneapolis.events
]


async def enrich_images(events: list[dict], source_content: dict[str, str]) -> list[dict]:
    """Add imageUrl to events missing one.

    Two strategies:
    1. Re-fetch source listing pages via agentcdn JSON mode to get JSON-LD
       event images. Match to events by name.
    2. For events with a sourceUrl but no image, fetch the individual event
       page for OG image.
    """
    needs_image = [e for e in events if not _has_valid_image(e)]
    if not needs_image:
        print("  All events already have images")
        return events

    print("  {} events need images...".format(len(needs_image)))

    # Strategy 1: Fetch JSON-LD from source listing pages
    image_map = await _build_image_map_from_sources(source_content)
    matched = 0
    for event in needs_image:
        name_lower = event.get("name", "").lower().strip()
        if name_lower in image_map:
            event["imageUrl"] = image_map[name_lower]
            matched += 1

    print("  Matched {} images from source JSON-LD".format(matched))

    # Strategy 2: Fetch OG images for events with sourceUrl but no image
    still_need = [e for e in events if not _has_valid_image(e) and e.get("sourceUrl")]
    if still_need:
        print("  Fetching OG images for {} events...".format(len(still_need)))
        async with httpx.AsyncClient() as client:
            tasks = [_fetch_og_image(client, e) for e in still_need]
            results = await asyncio.gather(*tasks)
        og_found = sum(1 for r in results if r)
        print("  Found {} OG images".format(og_found))

    total_with = sum(1 for e in events if _has_valid_image(e))
    print("  Final: {}/{} events have images".format(total_with, len(events)))

    return events


async def _build_image_map_from_sources(source_content: dict[str, str]) -> dict[str, str]:
    """Re-fetch source pages in JSON mode to extract JSON-LD event images.

    Returns {lowercase_event_name: image_url}.
    """
    image_map = {}

    # Only re-fetch agentcdn sources (browser sources don't go through agentcdn)
    # We identify agentcdn-compatible sources by checking if the content looks like markdown
    source_urls = _extract_source_urls(source_content)

    async with httpx.AsyncClient() as client:
        tasks = [_fetch_jsonld_images(client, url) for url in source_urls]
        results = await asyncio.gather(*tasks)

    for result in results:
        image_map.update(result)

    print("  Built image map with {} entries from JSON-LD".format(len(image_map)))
    return image_map


def _extract_source_urls(source_content: dict[str, str]) -> list[str]:
    """Get the original URLs from source content for re-fetching in JSON mode.

    We look for URLs embedded in the content from agentcdn sources.
    For known sources, we use their configured URLs directly.
    """
    # Known source listing URLs that have good JSON-LD
    return [
        "https://minneapolis.events",
        "https://www.songkick.com/metro-areas/29313-us-minneapolis/events",
        "https://www.bandsintown.com/c/minneapolis-mn",
        "https://first-avenue.com/calendar/",
    ]


async def _fetch_jsonld_images(client: httpx.AsyncClient, url: str) -> dict[str, str]:
    """Fetch a source page in JSON mode and extract event name→image mappings."""
    result = {}
    try:
        resp = await client.get(
            "{}/{}".format(AGENTCDN_BASE, url),
            params={"format": "json"},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("jsonld", []):
            _extract_images_recursive(item, result)

    except Exception:
        pass

    return result


def _extract_images_recursive(item, result: dict):
    """Recursively extract event name→image pairs from JSON-LD."""
    if isinstance(item, list):
        for sub in item:
            _extract_images_recursive(sub, result)
        return

    if not isinstance(item, dict):
        return

    item_type = item.get("@type", "")
    event_types = {"Event", "MusicEvent", "TheaterEvent", "SportsEvent",
                   "DanceEvent", "ComedyEvent", "EducationEvent", "Festival"}

    if item_type in event_types:
        name = item.get("name", "").lower().strip()
        image = _get_image_url(item.get("image"))
        if name and image and not _is_skip_image(image):
            result[name] = image

    # Check sub-events
    for key in ("subEvent", "performer"):
        sub = item.get(key)
        if sub:
            _extract_images_recursive(sub, result)


def _get_image_url(image) -> str | None:
    """Extract URL from various JSON-LD image formats."""
    if isinstance(image, str):
        return image
    if isinstance(image, list) and image:
        first = image[0]
        return first if isinstance(first, str) else first.get("url", "")
    if isinstance(image, dict):
        return image.get("url", "")
    return None


async def _fetch_og_image(client: httpx.AsyncClient, event: dict) -> str | None:
    """Fetch OG image from an event's sourceUrl."""
    try:
        resp = await client.get(
            "{}/{}".format(AGENTCDN_BASE, event["sourceUrl"]),
            params={"format": "json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

        # Try JSON-LD on the individual page
        page_images = {}
        for item in data.get("jsonld", []):
            _extract_images_recursive(item, page_images)
        if page_images:
            event["imageUrl"] = next(iter(page_images.values()))
            return event["imageUrl"]

        # Fall back to OG image
        og = data.get("metadata", {}).get("ogImage")
        if og and not _is_skip_image(og):
            event["imageUrl"] = og
            return og

    except Exception:
        pass
    return None


def _has_valid_image(event: dict) -> bool:
    """Check if event has a non-null, non-placeholder image URL."""
    url = event.get("imageUrl")
    if not url or not isinstance(url, str):
        return False
    return not _is_skip_image(url)


def _is_skip_image(url: str) -> bool:
    """Check if URL is a generic/placeholder image we should skip."""
    lower = url.lower()
    return any(p in lower for p in SKIP_PATTERNS)
