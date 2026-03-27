"""CLI entry point for TheWord pipeline."""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_DIR = ROOT / "docs"
CONFIG_DIR = ROOT / "config"


def cmd_scrape(args):
    """Full pipeline: fetch → structure → process → write → push."""
    from .fetcher import fetch_all_sources
    from .structurer import structure_events
    from .processor import process_events
    from .writer import write_events
    from .publisher import publish

    print("=== TheWord Pipeline ===")

    # 1. Fetch
    print("\n[1/5] Fetching sources...")
    sources_yaml = CONFIG_DIR / "sources.yaml"
    raw_content = asyncio.run(fetch_all_sources(sources_yaml))
    if not raw_content:
        print("ERROR: No sources returned content. Aborting.")
        sys.exit(1)

    # 2. Structure
    print(f"\n[2/5] Structuring events from {len(raw_content)} sources...")
    raw_events = asyncio.run(structure_events(raw_content))
    print(f"  Extracted {len(raw_events)} raw events")

    # 3. Process
    print("\n[3/5] Processing (dedup, tagging, consolidation)...")
    venues_yaml = CONFIG_DIR / "venues.yaml"
    processed = process_events(raw_events, venues_yaml)
    print(f"  {len(processed)} events after processing")

    # 4. Write
    print("\n[4/5] Writing events.json...")
    events_json = DOCS_DIR / "events.json"
    written = write_events(processed, events_json)
    if not written:
        print("  Kept previous events.json (below minimum threshold)")
    else:
        print(f"  Wrote {len(processed)} events to {events_json}")

    # 5. Publish
    if not args.no_push:
        print("\n[5/5] Publishing to GitHub...")
        publish(ROOT, events_json, len(processed), len(raw_content))
    else:
        print("\n[5/5] Skipping push (--no-push)")

    print("\n=== Done ===")


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


def main():
    parser = argparse.ArgumentParser(prog="the_word", description="TheWord events pipeline")
    sub = parser.add_subparsers(dest="command")

    scrape_p = sub.add_parser("scrape", help="Full pipeline: fetch → structure → process → write → push")
    scrape_p.add_argument("--no-push", action="store_true", help="Skip git push")
    scrape_p.set_defaults(func=cmd_scrape)

    fetch_p = sub.add_parser("fetch", help="Fetch sources only (debug)")
    fetch_p.set_defaults(func=cmd_fetch)

    validate_p = sub.add_parser("validate", help="Validate existing events.json")
    validate_p.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
