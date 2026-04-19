"""Per-source run state: rolling counts and last-known-good event cache.

State lives at <repo>/state/source_state.json (gitignored). It is used to:
- Detect anomalies (a source that historically returns events suddenly returns 0).
- Fall back to the previous successful extraction for that source so the site
  degrades gracefully when one source flakes.
- Power the health report.

Schema (version 1):
{
  "version": 1,
  "updated_at": "<iso>",
  "sources": {
    "<source name>": {
      "runs": [ {"at": "<iso>", "count": N, "status": "ok|empty|failed|fallback"} ],
      "consecutive_empty": N,
      "last_known_good": {"at": "<iso>", "events": [...]}
    }
  }
}
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATE_VERSION = 1
ROLLING_WINDOW = 14  # keep last 14 runs per source


@dataclass
class SourceState:
    name: str
    runs: list[dict] = field(default_factory=list)
    consecutive_empty: int = 0
    last_known_good_at: str | None = None
    last_known_good_events: list[dict] = field(default_factory=list)

    def record_run(self, count: int, status: str) -> None:
        """Append a run record, trimming to ROLLING_WINDOW."""
        self.runs.append(
            {
                "at": _utcnow_iso(),
                "count": count,
                "status": status,
            }
        )
        if len(self.runs) > ROLLING_WINDOW:
            self.runs = self.runs[-ROLLING_WINDOW:]

        if status == "ok" and count == 0:
            # "ok" with zero count is treated as empty for streak tracking
            self.consecutive_empty += 1
        elif status in ("empty", "failed", "fallback"):
            self.consecutive_empty += 1
        else:
            self.consecutive_empty = 0

    def update_cache(self, events: list[dict]) -> None:
        """Replace last-known-good with a fresh non-empty extraction."""
        if not events:
            return
        self.last_known_good_at = _utcnow_iso()
        self.last_known_good_events = events

    def baseline_count(self) -> int:
        """Median count of the most recent runs with status 'ok' and count > 0.

        Used as an anomaly baseline: if current count is well below this we
        treat it as suspicious and attempt recovery (retry / fallback).
        """
        productive = [r["count"] for r in self.runs if r.get("count", 0) > 0 and r.get("status") == "ok"]
        if not productive:
            return 0
        return int(statistics.median(productive))

    def is_historically_productive(self) -> bool:
        """True if the source has produced events in recent history."""
        return self.baseline_count() > 0

    def to_dict(self) -> dict:
        return {
            "runs": self.runs,
            "consecutive_empty": self.consecutive_empty,
            "last_known_good": (
                {"at": self.last_known_good_at, "events": self.last_known_good_events}
                if self.last_known_good_at is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> SourceState:
        lkg = data.get("last_known_good") or {}
        return cls(
            name=name,
            runs=list(data.get("runs", [])),
            consecutive_empty=int(data.get("consecutive_empty", 0)),
            last_known_good_at=lkg.get("at") if lkg else None,
            last_known_good_events=list(lkg.get("events", []) if lkg else []),
        )


class PipelineState:
    """Aggregate per-source state, loaded from and saved to a single JSON file."""

    def __init__(self, path: Path, sources: dict[str, SourceState]):
        self.path = path
        self.sources = sources

    @classmethod
    def load(cls, path: Path) -> PipelineState:
        if not path.exists():
            return cls(path=path, sources={})
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARN: state file unreadable ({e}); starting fresh")
            return cls(path=path, sources={})
        version = data.get("version")
        if version != STATE_VERSION:
            print(f"  WARN: state version {version} != {STATE_VERSION}; starting fresh")
            return cls(path=path, sources={})
        sources = {
            name: SourceState.from_dict(name, sdata)
            for name, sdata in data.get("sources", {}).items()
        }
        return cls(path=path, sources=sources)

    def get(self, name: str) -> SourceState:
        """Get or create the per-source state entry."""
        if name not in self.sources:
            self.sources[name] = SourceState(name=name)
        return self.sources[name]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": STATE_VERSION,
            "updated_at": _utcnow_iso(),
            "sources": {name: s.to_dict() for name, s in self.sources.items()},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
