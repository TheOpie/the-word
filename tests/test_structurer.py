"""Tests for the structurer: determinism, retry on empty, fallback wiring.

HTTP is mocked via httpx.MockTransport. We assert on request payloads
(including temperature) and the derived SourceResult structure.
"""

import json
from typing import Callable

import httpx
import pytest

from the_word import structurer
from the_word.structurer import (
    SourceResult,
    structure_events_per_source,
)


def _ok_event(name="Show"):
    return {
        "name": name,
        "dateTime": "2026-05-01T20:00:00",
        "venue": "First Avenue",
    }


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]):
    """Patch httpx.AsyncClient to return a mock client configured with transport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch):
    """Each test can override via its own transport; by default no network."""
    yield


@pytest.mark.asyncio
async def test_happy_path_parses_events(monkeypatch):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return _chat_response(json.dumps([_ok_event("A"), _ok_event("B")]))

    monkeypatch.setattr(
        structurer,
        "httpx",
        _PatchedHttpx(handler),
    )

    results = await structure_events_per_source({"Src1": "content"})
    assert len(results) == 1
    r = results[0]
    assert r.status == "ok"
    assert len(r.events) == 2
    assert r.attempts == 1
    # Deterministic first call
    assert captured[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_empty_with_history_retries_with_nudge(monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        # First call returns empty, retry returns events
        if len(calls) == 1:
            return _chat_response("[]")
        return _chat_response(json.dumps([_ok_event("Rescued")]))

    monkeypatch.setattr(structurer, "httpx", _PatchedHttpx(handler))

    results = await structure_events_per_source(
        {"Src1": "content"},
        historically_productive={"Src1": True},
    )
    r = results[0]
    assert r.status == "ok"
    assert len(r.events) == 1
    assert r.attempts == 2
    assert calls[0]["temperature"] == 0.0
    assert calls[1]["temperature"] > 0.0


@pytest.mark.asyncio
async def test_empty_without_history_does_not_retry(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return _chat_response("[]")

    monkeypatch.setattr(structurer, "httpx", _PatchedHttpx(handler))

    results = await structure_events_per_source(
        {"Src1": "content"}, historically_productive={"Src1": False}
    )
    assert results[0].status == "empty"
    assert results[0].attempts == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_http_error_returns_failed(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(structurer, "httpx", _PatchedHttpx(handler))
    monkeypatch.setattr(structurer, "API_RETRY_DELAY", 0)

    results = await structure_events_per_source({"Src1": "content"})
    r = results[0]
    assert r.status == "failed"
    assert "HTTP 500" in (r.error or "")


@pytest.mark.asyncio
async def test_invalid_json_returns_failed(monkeypatch):
    def handler(request):
        return _chat_response("not json at all")

    monkeypatch.setattr(structurer, "httpx", _PatchedHttpx(handler))

    results = await structure_events_per_source({"Src1": "content"})
    assert results[0].status == "failed"
    assert "invalid JSON" in (results[0].error or "")


@pytest.mark.asyncio
async def test_events_with_missing_required_fields_dropped_not_failed(monkeypatch):
    def handler(request):
        return _chat_response(
            json.dumps(
                [
                    _ok_event("Good"),
                    {"name": "No Date Or Venue"},
                ]
            )
        )

    monkeypatch.setattr(structurer, "httpx", _PatchedHttpx(handler))

    results = await structure_events_per_source({"Src1": "content"})
    r = results[0]
    assert r.status == "ok"
    assert len(r.events) == 1
    assert len(r.dropped) == 1


@pytest.mark.asyncio
async def test_markdown_wrapped_json_unwraps(monkeypatch):
    def handler(request):
        return _chat_response("```json\n" + json.dumps([_ok_event()]) + "\n```")

    monkeypatch.setattr(structurer, "httpx", _PatchedHttpx(handler))

    results = await structure_events_per_source({"Src1": "content"})
    assert results[0].status == "ok"
    assert len(results[0].events) == 1


# --- test harness --------------------------------------------------------

class _PatchedHttpx:
    """Stand-in for the httpx module that forces MockTransport everywhere."""

    def __init__(self, handler):
        self._handler = handler
        # Pass through error classes the structurer catches
        self.HTTPStatusError = httpx.HTTPStatusError
        self.ConnectError = httpx.ConnectError
        self.ReadTimeout = httpx.ReadTimeout
        self.RemoteProtocolError = httpx.RemoteProtocolError

    def AsyncClient(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handler))
