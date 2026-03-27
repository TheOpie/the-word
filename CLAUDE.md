# CLAUDE.md — TheWord

Minneapolis/St. Paul rolling 7-day events calendar. Static site + automated scraping pipeline.

## Architecture

Two systems in one repo:
1. **Static site** (`docs/`) — vanilla HTML/CSS/JS served by GitHub Pages at theword.theopie.com
2. **Scraping pipeline** (`src/the_word/`) — Python CLI that fetches events from 10 sources, structures them via Claude API, writes `docs/events.json`

No database. No server. No framework. No build step for the site.

## Commands

```bash
# Pipeline
source .venv/bin/activate
pip install -e ".[dev]"
python -m the_word scrape          # Full pipeline: fetch → structure → process → write → push
python -m the_word scrape --no-push  # Skip git push (local dev)
python -m the_word fetch            # Fetch sources only (debug)
python -m the_word validate         # Validate existing events.json schema

# Site
open docs/index.html               # Local preview (no server needed)
```

## Key Files

- `config/sources.yaml` — scraping source URLs + fetch method (agentcdn or browser)
- `config/venues.yaml` — venue → tag mapping (26 venues, 46 keywords)
- `docs/events.json` — the data file (generated, but committed to git)
- `docs/index.html` — the site
- `.env` — API keys (ANTHROPIC_API_KEY, GITHUB_TOKEN). Never commit.

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
