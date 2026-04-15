# TheWord

Rolling 7-day events calendar for Minneapolis/St. Paul. Live at **[theword.theopie.com](https://theword.theopie.com)**.

No database. No server. No framework. Just a scraping pipeline that writes a JSON file and a static site that reads it.

## How It Works

```
10 sources → Python scraper → Claude API (structuring) → events.json → GitHub Pages
```

1. **Fetch** — pulls event listings from 10 Minneapolis sources (venues, aggregators, local media) using [AgentCDN](https://github.com/TheOpie/agentcdn) for static pages and headless browser for JS-heavy sites.
2. **Structure** — sends raw HTML/markdown to Claude API to extract structured event data (title, date, time, venue, tags, description).
3. **Process** — deduplicates, validates, applies venue/keyword tagging from curated mappings (26 venues, 46 keywords), drops events older than today.
4. **Write** — outputs `docs/events.json`. Requires minimum 5 events or the previous file is preserved.
5. **Publish** — commits and pushes to GitHub. GitHub Pages serves `docs/` at the custom domain.

The site (`docs/index.html`) is vanilla HTML/CSS/JS that fetches `events.json` on load and renders filterable event cards — no build step.

## Sources

| Source | Method |
|--------|--------|
| Minneapolis.org Events | Browser |
| Minneapolis Events | AgentCDN |
| Twin Cities Family | AgentCDN |
| Hennepin Theatre Trust | AgentCDN |
| Songkick Minneapolis | AgentCDN |
| 331 Club | Browser |
| Bandsintown Minneapolis | AgentCDN |
| First Avenue | AgentCDN |
| Nobool Presents | Browser |
| The Current Events | AgentCDN |

## Setup

Requires Python 3.11+ and API keys in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
```

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
# Full pipeline: fetch → structure → process → write → push
python -m the_word scrape

# Local dev (skip git push)
python -m the_word scrape --no-push

# Debug: fetch sources only
python -m the_word fetch

# Validate events.json schema
python -m the_word validate
```

## Design

90s street art meets gospel energy. Dark mode only.

- **Background:** `#0D0D0D` with gradient cards
- **Accents:** Gold `#FFD700` (primary), Purple `#7B2D8E` (secondary), Magenta `#E066FF` (tags)
- **Fonts:** Bebas Neue (body/UI), Permanent Marker (headings/graffiti feel)
- **Effects:** Divine glow on cards and hover lift transitions

Filter by date (today / tomorrow / weekend) or by tag (music, art, sports, theater, comedy, etc.).

## Project Structure

```
the-word/
├── config/
│   ├── sources.yaml      # Scraping targets + fetch method
│   └── venues.yaml       # Venue→tag and keyword→tag mappings
├── docs/                  # Static site (GitHub Pages root)
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   ├── events.json        # Generated data file
│   └── img/placeholders/  # Category SVG placeholders
├── src/the_word/
│   ├── __main__.py        # CLI entrypoint
│   ├── fetcher.py         # Source fetching (agentcdn + browser)
│   ├── structurer.py      # Claude API event extraction
│   ├── processor.py       # Dedup, validation, tagging
│   ├── writer.py          # JSON output with safety checks
│   └── publisher.py       # Git commit + push
├── tests/
├── pyproject.toml
└── CLAUDE.md
```

## License

Private project by [TheOpie](https://github.com/TheOpie).
