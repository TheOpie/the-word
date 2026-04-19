"""Publish-time quality gates.

Sits between the processor and `write_events`. Prevents silent regressions
by comparing the incoming snapshot against the previous committed snapshot
(docs/events.json) and a set of absolute thresholds.

Rules (in order of severity):
- Absolute minimums: event count, unique venue count.
- Relative drop: new count must not fall below `max_drop_ratio` of the
  previous count when the previous snapshot had more than a trivial number.
- Single-venue dominance: no venue may account for more than
  `max_single_venue_share` of events (prevents a lone source taking over
  when all others fail).
- Provenance density: minimum fraction of events with a valid `sourceUrl`,
  plus a cap on how much this density may regress from the prior snapshot.

Every failure is surfaced to the operator. An explicit override (`force=True`
from the CLI) lets operators publish intentional reductions; overrides are
logged to the health report.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GateThresholds:
    min_event_count: int = 5
    min_unique_venues: int = 3
    max_drop_ratio: float = 0.5  # new_count must be >= previous_count * (1 - max_drop_ratio)
    max_single_venue_share: float = 0.6
    min_source_url_density: float = 0.4
    max_source_url_density_drop: float = 0.3
    # Relative rules only apply once the previous snapshot is large enough to trust.
    relative_rule_min_previous: int = 10


@dataclass
class GateViolation:
    rule: str
    detail: str


@dataclass
class GateReport:
    passed: bool
    violations: list[GateViolation] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    # Populated when the operator bypasses failing gates with --force.
    forced: bool = False

    def format_lines(self) -> list[str]:
        lines = []
        for v in self.violations:
            lines.append(f"  GATE FAILED [{v.rule}]: {v.detail}")
        return lines


def evaluate(
    new_events: list[dict],
    previous_events: list[dict] | None,
    thresholds: GateThresholds | None = None,
) -> GateReport:
    """Run every gate and return the consolidated report."""
    t = thresholds or GateThresholds()
    violations: list[GateViolation] = []

    stats = _compute_stats(new_events)
    prev_stats = _compute_stats(previous_events) if previous_events else None

    # Absolute gates (always enforced)
    if stats["count"] < t.min_event_count:
        violations.append(
            GateViolation(
                "min_event_count",
                f"{stats['count']} events is below minimum {t.min_event_count}",
            )
        )

    if stats["unique_venues"] < t.min_unique_venues:
        violations.append(
            GateViolation(
                "min_unique_venues",
                f"{stats['unique_venues']} unique venues is below minimum {t.min_unique_venues}",
            )
        )

    if stats["count"] > 0:
        share = stats["top_venue_share"]
        if share > t.max_single_venue_share:
            violations.append(
                GateViolation(
                    "single_venue_dominance",
                    (
                        f"'{stats['top_venue']}' accounts for "
                        f"{int(round(share * 100))}% of events "
                        f"(cap {int(round(t.max_single_venue_share * 100))}%)"
                    ),
                )
            )

        density = stats["source_url_density"]
        if density < t.min_source_url_density:
            violations.append(
                GateViolation(
                    "source_url_density",
                    (
                        f"sourceUrl density {int(round(density * 100))}% "
                        f"is below minimum {int(round(t.min_source_url_density * 100))}%"
                    ),
                )
            )

    # Relative gates (only if previous snapshot exists and is substantive)
    if prev_stats and prev_stats["count"] >= t.relative_rule_min_previous:
        min_expected = int(prev_stats["count"] * (1 - t.max_drop_ratio))
        if stats["count"] < min_expected:
            drop_pct = int(round((1 - stats["count"] / prev_stats["count"]) * 100))
            violations.append(
                GateViolation(
                    "coverage_drop",
                    (
                        f"{stats['count']} events is a {drop_pct}% drop from "
                        f"previous {prev_stats['count']} "
                        f"(allowed drop {int(round(t.max_drop_ratio * 100))}%)"
                    ),
                )
            )

        density_drop = prev_stats["source_url_density"] - stats["source_url_density"]
        if density_drop > t.max_source_url_density_drop:
            violations.append(
                GateViolation(
                    "source_url_density_drop",
                    (
                        f"sourceUrl density dropped "
                        f"{int(round(density_drop * 100))} points "
                        f"(prev {int(round(prev_stats['source_url_density'] * 100))}%, "
                        f"now {int(round(stats['source_url_density'] * 100))}%)"
                    ),
                )
            )

    return GateReport(
        passed=not violations,
        violations=violations,
        stats={"new": stats, "previous": prev_stats},
    )


def load_previous(events_json_path: Path) -> list[dict] | None:
    """Load the previous committed events.json, returning None on missing/invalid."""
    if not events_json_path.exists():
        return None
    try:
        import json

        data = json.loads(events_json_path.read_text())
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return data


def _compute_stats(events: list[dict] | None) -> dict:
    if not events:
        return {
            "count": 0,
            "unique_venues": 0,
            "top_venue": None,
            "top_venue_share": 0.0,
            "source_url_density": 0.0,
        }
    venues = Counter((e.get("venue") or "").strip() for e in events)
    top_venue, top_count = venues.most_common(1)[0] if venues else ("", 0)
    total = len(events)
    with_source_url = sum(1 for e in events if _is_nonempty_str(e.get("sourceUrl")))
    return {
        "count": total,
        "unique_venues": sum(1 for v in venues if v),
        "top_venue": top_venue or None,
        "top_venue_share": (top_count / total) if total else 0.0,
        "source_url_density": (with_source_url / total) if total else 0.0,
    }


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())
