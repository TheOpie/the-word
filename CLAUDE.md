# CLAUDE.md — TheWord

Minneapolis/St. Paul rolling 7-day events calendar. Static site + automated scraping pipeline.

## Architecture

Two systems in one repo:
1. **Static site** (`docs/`) — vanilla HTML/CSS/JS served by GitHub Pages at theword.theopie.com
2. **Scraping pipeline** (`src/the_word/`) — Python CLI that fetches events from 10 sources, structures them per-source via Ollama (MiniMax), writes `docs/events.json`

No database. No server. No framework. No build step for the site.

## Reliability

The pipeline runs unattended in cron; the reliability machinery assumes a site a paying customer depends on.

- **Determinism** — `structurer.py` calls the model with `temperature=0` and extracts one source per request. Multi-source bundling was observed to make MiniMax return `[]`.
- **Per-source state** — `state/source_state.json` (gitignored) keeps the last 14 runs per source plus a cache of the most recent successful extraction.
- **Retry-on-empty** — If a historically-productive source returns 0 events at temp=0, the structurer retries once with a small temperature nudge.
- **Graceful fallback** — If a source still returns empty (or fails), the orchestrator uses its last-known-good events. The 7-day window filter removes expired ones automatically.
- **Strict validation** — `validation.py` drops events with unparseable `dateTime`, malformed URLs, or missing required fields before they enter the pipeline.
- **Publish-time quality gates** — `quality_gate.py` compares the new snapshot to the previously-committed `events.json` before writing. Blocks the write when any of these fire: below absolute minimums (count, unique venues), coverage drops >50% vs the prior snapshot, any single venue holds >60% of events, or `sourceUrl` density falls below 40% or drops >30 points. Operators can bypass with `python -m the_word scrape --force` for intentional curations; forced runs are flagged in the health report.
- **Health reporting** — Every run writes `state/last_run.json` and prints a summary. Overall status is `healthy | degraded | critical`. Exit codes: `0` healthy, `2` degraded, `1` critical.
- **Write guard** — `writer.py` refuses to overwrite `events.json` if the processed set is below the minimum threshold.

## Commands

```bash
# Pipeline
source .venv/bin/activate
pip install -e ".[dev]"
python -m the_word scrape          # Full pipeline: fetch → structure → fallback → process → write → push
python -m the_word scrape --no-push  # Skip git push (local dev)
python -m the_word fetch            # Fetch sources only (debug)
python -m the_word validate         # Validate existing events.json schema
python -m the_word health           # Print the last-run health report (JSON)

# Site
open docs/index.html               # Local preview (no server needed)
```

## Key Files

- `config/sources.yaml` — scraping source URLs + fetch method (agentcdn or browser)
- `config/venues.yaml` — venue → tag mapping (26 venues, 46 keywords)
- `docs/events.json` — the data file (generated, but committed to git)
- `docs/index.html` — the site
- `state/source_state.json` — per-source rolling history + last-known-good cache (gitignored, per-machine)
- `state/last_run.json` — machine-readable health report written every run
- `.env` — API keys and model overrides (OLLAMA_BASE_URL, THE_WORD_MODEL). Never commit.

## Conventions

- Python: src layout, pyproject.toml, .venv, black formatting
- Site: vanilla HTML/CSS/JS only. No npm. No build.
- Git: commit after each pipeline run with message "Events update: YYYY-MM-DD (N events from M sources)"
- Events older than today are never written to events.json
- Minimum 5 events required or previous file is preserved

## Design System (90s Street Art / Gospel — from v1)

- Dark mode only: background #0D0D0D, cards gradient #1a1a1a → #0D0D0D
- Gold accent: #FFD700 (primary), Purple: #7B2D8E (secondary), Dark Purple: #4A1259
- Magenta for tag text: #E066FF, Cream for body text: #FFF8E7
- Fonts: Bebas Neue (body/UI, all-caps bold), Permanent Marker (headings, graffiti feel) — Google Fonts
- Divine glow: box-shadow 0 0 30px rgba(255,215,0,0.4), 0 0 60px rgba(123,45,142,0.2)
- Card hover: translateY(-4px) + enhanced glow, 0.3s transition
- Filter pills: gold bg + black text (active), dark + purple hover (inactive)
- Bold, loud, high-contrast 90s energy
