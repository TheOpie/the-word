"""Event structurer — Claude API extraction from markdown content."""

import json
import os

import anthropic

REQUIRED_FIELDS = {"name", "dateTime", "venue"}

SYSTEM_PROMPT = """You are an event data extractor for Minneapolis/St. Paul. Given markdown content from an events website, extract structured event data.

CRITICAL RULES:
- Extract ONLY events explicitly listed in the content
- Do NOT infer, predict, or generate events that aren't explicitly mentioned
- Do NOT fabricate dates, times, venues, or any other details
- If information is ambiguous or missing, omit that field rather than guessing
- Each event must have at minimum: name, dateTime, venue

Output a JSON array of event objects with these fields:
{
  "name": "Event Name",
  "dateTime": "ISO 8601 datetime (YYYY-MM-DDTHH:MM:SS)",
  "venue": "Venue Name",
  "address": "Street address if available, otherwise omit",
  "description": "1-2 sentence description from the listing. Do not copy more than 2 sentences.",
  "sourceUrl": "Direct link to the event listing if available",
  "imageUrl": "Direct URL to an event image/flyer/artist photo if visible in the content, otherwise null",
  "tags": []
}

For dateTime:
- Use the current year (2026) unless explicitly stated otherwise
- If only a date is given with no time, use T00:00:00
- If a time range is given (e.g., "8pm-11pm"), use the start time

Output ONLY the JSON array. No markdown formatting, no explanation."""


async def structure_events(source_content: dict[str, str]) -> list[dict]:
    """Send source content to Claude API for event extraction.

    Batches sources to minimize API calls while staying within context limits.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    client = anthropic.Anthropic(api_key=api_key)
    all_events = []

    # Batch sources into groups that fit in context (~100K chars per batch)
    batches = _make_batches(source_content, max_chars=80_000)

    for i, batch in enumerate(batches, 1):
        print(f"  Structuring batch {i}/{len(batches)} ({len(batch)} sources)...")

        user_content = ""
        for name, content in batch.items():
            # Truncate very long pages
            truncated = content[:15_000]
            user_content += f"\n\n--- SOURCE: {name} ---\n{truncated}"

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=8192,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            text = response.content[0].text.strip()

            # Handle markdown-wrapped JSON
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            events = json.loads(text)
            if not isinstance(events, list):
                print(f"  WARN: Batch {i} returned non-list, skipping")
                continue

            # Validate required fields
            valid = []
            dropped = 0
            for event in events:
                if all(event.get(f) for f in REQUIRED_FIELDS):
                    valid.append(event)
                else:
                    dropped += 1

            if dropped:
                print(f"  Dropped {dropped} event(s): missing required fields")

            all_events.extend(valid)
            print(f"  Batch {i}: {len(valid)} valid events")

        except json.JSONDecodeError as e:
            print(f"  WARN: Batch {i} returned invalid JSON: {e}")
        except Exception as e:
            print(f"  WARN: Batch {i} API call failed: {e}")

    return all_events


def _make_batches(source_content: dict[str, str], max_chars: int) -> list[dict]:
    """Group sources into batches that fit within character limit."""
    batches = []
    current_batch = {}
    current_size = 0

    for name, content in source_content.items():
        size = min(len(content), 15_000)  # We truncate to 15K per source
        if current_size + size > max_chars and current_batch:
            batches.append(current_batch)
            current_batch = {}
            current_size = 0
        current_batch[name] = content
        current_size += size

    if current_batch:
        batches.append(current_batch)

    return batches
