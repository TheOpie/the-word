"""Event structurer — Ollama extraction from markdown, one source per call.

Operates deterministically (temperature=0) with a bounded retry on empty
responses for sources that have historically produced events. Returns a
structured result per source so the orchestrator can make fallback decisions.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

from .validation import validate_and_sanitize

API_RETRIES_HTTP = 1  # retries on HTTP/connection errors
API_RETRY_DELAY = 5  # seconds
REQUEST_TIMEOUT = 300.0

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL = os.environ.get("THE_WORD_MODEL", "minimax-m2.7:cloud")

SOURCE_CHAR_CAP = 15_000

# Empty-response retry: if a source historically produced events but the
# deterministic call returns [], we retry once with a small temperature nudge
# to give the model a chance to escape a stuck state.
EMPTY_RETRY_TEMPERATURES = [0.4]

Status = Literal["ok", "empty", "failed"]


@dataclass
class SourceResult:
    name: str
    events: list[dict] = field(default_factory=list)
    status: Status = "failed"
    attempts: int = 0
    duration_s: float = 0.0
    error: str | None = None
    dropped: list[str] = field(default_factory=list)


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


async def structure_events_per_source(
    source_content: dict[str, str],
    historically_productive: dict[str, bool] | None = None,
) -> list[SourceResult]:
    """Extract events for each source independently.

    historically_productive maps source name → bool; True triggers one
    retry when the deterministic call returns empty.
    """
    historically_productive = historically_productive or {}
    results: list[SourceResult] = []

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        total = len(source_content)
        for i, (name, content) in enumerate(source_content.items(), 1):
            truncated = content[:SOURCE_CHAR_CAP]
            print(f"  Structuring {i}/{total}: {name} ({len(truncated)} chars)...")
            result = await _extract_one(
                client, name, truncated, historically_productive.get(name, False)
            )
            _log_result(result)
            results.append(result)

    return results


async def _extract_one(
    client: httpx.AsyncClient,
    name: str,
    content: str,
    retry_on_empty: bool,
) -> SourceResult:
    result = SourceResult(name=name)
    started = time.monotonic()

    user_content = f"--- SOURCE: {name} ---\n{content}"

    # First attempt: deterministic
    events, err = await _call_model(client, name, user_content, temperature=0.0)
    result.attempts += 1

    # Retry on empty if historically productive
    if not events and retry_on_empty and err is None:
        for temp in EMPTY_RETRY_TEMPERATURES:
            print(f"    {name}: empty at temp=0, retrying at temp={temp}")
            events, err = await _call_model(client, name, user_content, temperature=temp)
            result.attempts += 1
            if events:
                break

    if err is not None:
        result.status = "failed"
        result.error = err
    elif not events:
        result.status = "empty"
    else:
        kept, dropped = validate_and_sanitize(events)
        result.events = kept
        result.dropped = dropped
        result.status = "ok" if kept else "empty"

    result.duration_s = round(time.monotonic() - started, 2)
    return result


async def _call_model(
    client: httpx.AsyncClient,
    label: str,
    user_content: str,
    temperature: float,
) -> tuple[list[dict], str | None]:
    """Call Ollama's OpenAI-compatible endpoint. Retries HTTP/connection errors.

    Returns (events_or_empty, error_message_or_none). An empty list with no
    error means the model responded but returned no events.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "stream": False,
    }

    url = f"{OLLAMA_BASE_URL.rstrip('/')}/chat/completions"

    for attempt in range(1 + API_RETRIES_HTTP):
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            text = (data["choices"][0]["message"]["content"] or "").strip()

            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            if not text:
                return [], None

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as e:
                return [], f"invalid JSON from model: {e}"

            if not isinstance(parsed, list):
                return [], "model returned non-list"

            return parsed, None

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            retriable = status in (429, 500, 502, 503, 504)
            if retriable and attempt < API_RETRIES_HTTP:
                wait = API_RETRY_DELAY * (attempt + 1)
                print(f"    {label} HTTP {status}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            body = e.response.text[:300]
            return [], f"HTTP {status}: {body}"
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            if attempt < API_RETRIES_HTTP:
                wait = API_RETRY_DELAY * (attempt + 1)
                print(f"    Transient error on {label}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            return [], f"connection error: {e}"
        except Exception as e:
            return [], f"unexpected error: {e}"

    return [], "exhausted HTTP retries"


def _log_result(result: SourceResult) -> None:
    dropped_note = f", dropped {len(result.dropped)}" if result.dropped else ""
    if result.status == "ok":
        print(
            f"  {result.name}: {len(result.events)} valid events "
            f"({result.attempts} attempts, {result.duration_s}s{dropped_note})"
        )
    elif result.status == "empty":
        print(
            f"  {result.name}: 0 events "
            f"({result.attempts} attempts, {result.duration_s}s{dropped_note})"
        )
    else:
        print(
            f"  {result.name}: FAILED ({result.attempts} attempts, "
            f"{result.duration_s}s) — {result.error}"
        )
