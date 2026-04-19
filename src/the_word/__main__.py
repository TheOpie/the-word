"""CLI entry point for TheWord pipeline."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = ROOT / "docs"
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "source_state.json"
HEALTH_FILE = STATE_DIR / "last_run.json"


def cmd_scrape(args):
    """Full pipeline: fetch → structure → fallback → process → write → push."""
    from .fetcher import fetch_all_sources
    from .structurer import structure_events_per_source, SourceResult
    from .processor import process_events
    from .images import enrich_images
    from .writer import write_events
    from .publisher import publish
    from .state import PipelineState
    from .quality_gate import evaluate as evaluate_gates, load_previous, GateThresholds
    from .health import build_health_report, write_health_report, print_summary

    print("=== TheWord Pipeline ===")

    # Load per-source state for fallback decisions
    state = PipelineState.load(STATE_FILE)

    # [1/6] Fetch
    print("\n[1/6] Fetching sources...")
    sources_yaml = CONFIG_DIR / "sources.yaml"
    raw_content = asyncio.run(fetch_all_sources(sources_yaml))
    if not raw_content:
        print("ERROR: No sources returned content. Aborting.")
        _emit_critical_report(state, sources=[], reason="no sources fetched")
        sys.exit(1)

    # [2/6] Structure (per-source, deterministic, with retry on empty)
    print("\n[2/6] Structuring events from {} sources...".format(len(raw_content)))
    productive = {
        name: state.get(name).is_historically_productive()
        for name in raw_content
    }
    results: list[SourceResult] = asyncio.run(
        structure_events_per_source(raw_content, historically_productive=productive)
    )

    # [2b] Fallback: for each source that came back empty/failed, use
    # last-known-good events if we have any. The window filter runs later,
    # so stale-but-valid-date events are kept and expired ones drop out.
    fallback_used_for: set[str] = set()
    merged_events: list[dict] = []
    per_source_source: dict[str, str] = {}  # event.name → source name (for final counts)

    for result in results:
        src_state = state.get(result.name)
        if result.status == "ok" and result.events:
            merged_events.extend(result.events)
            for ev in result.events:
                per_source_source.setdefault(_event_key(ev), result.name)
        else:
            cached = src_state.last_known_good_events
            if cached:
                age = src_state.last_known_good_at or "unknown"
                print(
                    f"  FALLBACK: {result.name} → using {len(cached)} cached events "
                    f"from {age}"
                )
                merged_events.extend(cached)
                fallback_used_for.add(result.name)
                for ev in cached:
                    per_source_source.setdefault(_event_key(ev), result.name)
            else:
                print(f"  {result.name}: no cache available, source contributes 0 events")

    print(f"  Total after fallback: {len(merged_events)} raw events")

    # [3/6] Process (tag, dedup, 7-day window, consolidate)
    print("\n[3/6] Processing (dedup, tagging, consolidation)...")
    venues_yaml = CONFIG_DIR / "venues.yaml"
    processed = process_events(merged_events, venues_yaml)
    print("  {} events after processing".format(len(processed)))

    # Compute final per-source contribution
    final_counts: dict[str, int] = {name: 0 for name in raw_content}
    for ev in processed:
        src = per_source_source.get(_event_key(ev))
        if src and src in final_counts:
            final_counts[src] += 1

    # [4/6] Enrich images
    print("\n[4/6] Enriching event images...")
    processed = asyncio.run(enrich_images(processed, raw_content))

    # [5/6] Quality gates + write
    print("\n[5/6] Running quality gates...")
    events_json = DOCS_DIR / "events.json"
    previous = load_previous(events_json)
    gate_report = evaluate_gates(processed, previous, GateThresholds())
    _print_gate_summary(gate_report)

    if gate_report.passed:
        wrote = write_events(processed, events_json)
        if wrote:
            print(f"  Wrote {len(processed)} events to {events_json}")
        else:
            print("  Kept previous events.json (below minimum threshold)")
    elif args.force:
        print("  --force: overriding gate failures, writing anyway.")
        gate_report.forced = True
        wrote = write_events(processed, events_json)
        if wrote:
            print(f"  Wrote {len(processed)} events to {events_json} (forced)")
        else:
            print("  Kept previous events.json (below minimum threshold)")
    else:
        print("  Kept previous events.json (gate failure; re-run with --force to override)")
        wrote = False

    # Update state: record runs + refresh caches
    for result in results:
        src_state = state.get(result.name)
        if result.status == "ok" and result.events:
            src_state.record_run(len(result.events), "ok")
            src_state.update_cache(result.events)
        elif result.name in fallback_used_for:
            src_state.record_run(0, "fallback")
        elif result.status == "empty":
            src_state.record_run(0, "empty")
        else:
            src_state.record_run(0, "failed")

    state.save()

    # [6/6] Publish
    published: bool | None = None
    if not args.no_push:
        print("\n[6/6] Publishing to GitHub...")
        published = publish(ROOT, events_json, len(processed), len(raw_content))
        if not published:
            print("\n=== Done (scrape OK, publish failed — commit saved locally) ===")
    else:
        print("\n[6/6] Skipping push (--no-push)")

    # Health report
    from .health import build_health_report  # re-import for type
    report = build_health_report(
        source_results=results,
        state=state,
        final_counts=final_counts,
        fallback_used_for=fallback_used_for,
        wrote_events_json=wrote,
        published=published,
        gate_report=gate_report,
    )
    write_health_report(report, HEALTH_FILE)
    print_summary(report)

    # Exit code reflects overall health
    if report.overall_status == "critical":
        sys.exit(1)
    if report.overall_status == "degraded" or (not args.no_push and published is False):
        sys.exit(2)

    print("\n=== Done ===")


def _event_key(event: dict) -> str:
    """Stable key for tracing an event back to its source (approximate)."""
    return f"{event.get('name','')}|{event.get('venue','')}|{event.get('dateTime','')}"


def _print_gate_summary(report) -> None:
    new = report.stats.get("new", {})
    prev = report.stats.get("previous")
    print(
        f"  New: {new.get('count',0)} events, {new.get('unique_venues',0)} venues, "
        f"sourceUrl {int(round(new.get('source_url_density',0.0)*100))}%"
    )
    if prev:
        print(
            f"  Previous: {prev.get('count',0)} events, {prev.get('unique_venues',0)} venues, "
            f"sourceUrl {int(round(prev.get('source_url_density',0.0)*100))}%"
        )
    if report.passed:
        print("  Gates: PASSED")
    else:
        print(f"  Gates: FAILED ({len(report.violations)} violation(s))")
        for line in report.format_lines():
            print(line)


def _emit_critical_report(state, sources, reason):
    from .health import RunHealth, write_health_report
    report = RunHealth(
        run_at=datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        total_sources=len(sources),
        succeeded=0,
        empty=0,
        failed=len(sources),
        fallback_used=0,
        fresh_events=0,
        final_events=0,
        wrote_events_json=False,
        published=None,
        overall_status="critical",
        sources=[],
    )
    write_health_report(report, HEALTH_FILE)
    print(f"\n=== CRITICAL: {reason} ===")


def cmd_fetch(args):
    """Fetch sources only (debug)."""
    from .fetcher import fetch_all_sources

    sources_yaml = CONFIG_DIR / "sources.yaml"
    results = asyncio.run(fetch_all_sources(sources_yaml))
    for source, content in results.items():
        print(f"\n{'='*60}")
        print(f"SOURCE: {source}")
        print(f"LENGTH: {len(content)} chars")
        print(content[:500])
        print("...")


def cmd_validate(args):
    """Validate existing events.json schema."""
    from .writer import validate_events_json

    events_json = DOCS_DIR / "events.json"
    if not events_json.exists():
        print(f"No events.json found at {events_json}")
        sys.exit(1)

    valid, count, errors = validate_events_json(events_json)
    if valid:
        print(f"Valid: {count} events, all pass schema validation")
    else:
        print(f"Invalid: {len(errors)} error(s)")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


def cmd_health(args):
    """Print last-run health report."""
    if not HEALTH_FILE.exists():
        print(f"No health report found at {HEALTH_FILE}. Run 'scrape' first.")
        sys.exit(1)
    data = json.loads(HEALTH_FILE.read_text())
    print(json.dumps(data, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="the_word", description="TheWord events pipeline")
    sub = parser.add_subparsers(dest="command")

    scrape_p = sub.add_parser("scrape", help="Full pipeline: fetch → structure → process → write → push")
    scrape_p.add_argument("--no-push", action="store_true", help="Skip git push")
    scrape_p.add_argument(
        "--force",
        action="store_true",
        help="Override publish quality gates (use for intentional curations or debugging).",
    )
    scrape_p.set_defaults(func=cmd_scrape)

    fetch_p = sub.add_parser("fetch", help="Fetch sources only (debug)")
    fetch_p.set_defaults(func=cmd_fetch)

    validate_p = sub.add_parser("validate", help="Validate existing events.json")
    validate_p.set_defaults(func=cmd_validate)

    health_p = sub.add_parser("health", help="Print last-run health report")
    health_p.set_defaults(func=cmd_health)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
