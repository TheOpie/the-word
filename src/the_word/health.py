"""Post-run health reporting.

Produces a structured summary of the last pipeline run and a human-readable
console summary. Consumers (cron wrappers, dashboards, operators) can read
the JSON report to detect degradation without parsing logs.

Exit codes (from the CLI orchestration layer):
  0 — success (all sources healthy OR minor issues tolerated)
  1 — critical failure (pipeline aborted, no fresh data written)
  2 — degraded (some sources failed; fallback used; write may or may not have happened)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .state import PipelineState
from .structurer import SourceResult


@dataclass
class SourceHealth:
    name: str
    status: str           # ok | empty | failed | fallback
    fresh_count: int      # events from this run, pre-fallback
    final_count: int      # events contributed to the final set
    baseline: int         # historical median
    used_fallback: bool
    attempts: int
    duration_s: float
    consecutive_empty: int
    error: str | None = None
    dropped_count: int = 0


@dataclass
class RunHealth:
    run_at: str
    total_sources: int
    succeeded: int
    empty: int
    failed: int
    fallback_used: int
    fresh_events: int
    final_events: int
    wrote_events_json: bool
    published: bool | None
    overall_status: str   # healthy | degraded | critical
    sources: list[SourceHealth] = field(default_factory=list)


def build_health_report(
    source_results: list[SourceResult],
    state: PipelineState,
    final_counts: dict[str, int],
    fallback_used_for: set[str],
    wrote_events_json: bool,
    published: bool | None,
) -> RunHealth:
    """Compile per-source health and overall run verdict."""
    by_name = {r.name: r for r in source_results}
    source_healths: list[SourceHealth] = []

    for name, result in by_name.items():
        src_state = state.get(name)
        effective_status = "fallback" if name in fallback_used_for else result.status
        source_healths.append(
            SourceHealth(
                name=name,
                status=effective_status,
                fresh_count=len(result.events),
                final_count=final_counts.get(name, 0),
                baseline=src_state.baseline_count(),
                used_fallback=name in fallback_used_for,
                attempts=result.attempts,
                duration_s=result.duration_s,
                consecutive_empty=src_state.consecutive_empty,
                error=result.error,
                dropped_count=len(result.dropped),
            )
        )

    succeeded = sum(1 for h in source_healths if h.status == "ok")
    empty = sum(1 for h in source_healths if h.status == "empty")
    failed = sum(1 for h in source_healths if h.status == "failed")
    fallback = sum(1 for h in source_healths if h.status == "fallback")

    if not wrote_events_json:
        overall = "critical"
    elif failed > 0 or fallback > 0 or empty > succeeded:
        overall = "degraded"
    else:
        overall = "healthy"

    return RunHealth(
        run_at=_utcnow_iso(),
        total_sources=len(source_healths),
        succeeded=succeeded,
        empty=empty,
        failed=failed,
        fallback_used=fallback,
        fresh_events=sum(h.fresh_count for h in source_healths),
        final_events=sum(h.final_count for h in source_healths),
        wrote_events_json=wrote_events_json,
        published=published,
        overall_status=overall,
        sources=sorted(source_healths, key=lambda h: h.name),
    )


def write_health_report(report: RunHealth, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(report)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def print_summary(report: RunHealth) -> None:
    print("\n=== Health Summary ===")
    print(f"Status: {report.overall_status.upper()}")
    print(
        f"Sources: {report.succeeded}/{report.total_sources} ok, "
        f"{report.empty} empty, {report.failed} failed, "
        f"{report.fallback_used} fallback"
    )
    print(f"Events: {report.fresh_events} fresh → {report.final_events} final")
    print(f"Wrote events.json: {report.wrote_events_json}")
    if report.published is not None:
        print(f"Published: {report.published}")
    for h in report.sources:
        marker = {
            "ok": "  ",
            "empty": "? ",
            "failed": "X ",
            "fallback": "~ ",
        }.get(h.status, "  ")
        anomaly = ""
        if h.status == "ok" and h.baseline > 3 and h.fresh_count < h.baseline * 0.5:
            anomaly = f" (ANOMALY: {h.fresh_count} vs baseline {h.baseline})"
        print(
            f"  {marker}{h.name}: {h.status}, fresh={h.fresh_count}, "
            f"baseline={h.baseline}, attempts={h.attempts}, "
            f"streak_empty={h.consecutive_empty}{anomaly}"
        )


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
