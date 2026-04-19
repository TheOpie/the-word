# TODO

Follow-up work surfaced during the reliability overhaul and Codex adversarial review. Not a complete list — add as we learn.

## Observability & alerting
- Hook `state/last_run.json` into an external alert (Telegram / Slack). Today the report is written but nothing reads it — degraded runs are invisible unless someone opens the file.
- Track health history, not just the last run. A rolling log of per-run status would reveal slow degradations (e.g. `sourceUrl` density trending down over a week).
- Source-level alerting: if a source's `consecutive_empty` crosses a threshold (say 3), notify so an operator can investigate before the fallback cache goes stale.

## Resilience
- **Cache expiration.** Don't fall back to last-known-good events older than N days; past that, a smaller honest snapshot beats ancient data.
- **Source-weighted quorum.** Treat "First Avenue down" differently from "Minneapolis.org down". Today every source has equal weight in the dominance and count gates.
- **End-to-end gate-failure test.** Unit tests cover `quality_gate.evaluate`, but nothing integration-tests the `--force` path, the blocked-write path, or the exit-code contract. Add a fixtures-based integration test.
- **Ollama cloud SLA.** If `minimax-m2.7:cloud` is unavailable, the whole pipeline stalls. Decide between a local fallback model and failing fast with a clear alert.

## Data quality
- Twin Cities Family is chronically failing (502 from agentcdn). Remove the source or replace the URL — right now it just produces warning spam.
- Songkick / Minneapolis.org sometimes return empty from MiniMax for reasons that aren't obvious. Investigate source-specific prompts or a different extraction strategy.
- Image enrichment hit rate is near zero for most sources. The JSON-LD matching in `images.py` rarely lines up with event names the model extracted. Tighten name-matching or fall back to OG images more aggressively.

## Model tuning
- The retry-on-empty temperature (0.4) is a guess. A/B a week of captured source content to pick a better value.
- Try structured-output / JSON-mode if MiniMax supports it through Ollama's OpenAI-compatible endpoint. Would let us drop the markdown-fence unwrap.
- `SOURCE_CHAR_CAP = 15_000` is a defensive cap based on the old batching issues. Revisit now that we're per-source — we may be truncating content unnecessarily.

## Operations
- CI: run `pytest` on PR (GitHub Actions). Right now we only test locally.
- Document required env vars (`OLLAMA_BASE_URL`, `THE_WORD_MODEL`, `GITHUB_TOKEN`) in README or `.env.example`.
- Verify the cron job (`~/.openclaw/cron/jobs.json` → "The Word Daily Scrape — 6:00 AM CT") still works end-to-end against the new CLI signature, particularly exit codes. Degraded runs (exit 2) should not be treated as failures.
