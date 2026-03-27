"""Source fetcher — agentcdn + agent-browser fallback."""

import asyncio
from pathlib import Path

import httpx
import yaml

AGENTCDN_BASE = "https://yellow-resonance-7c40.opieworks-ai.workers.dev/agent"


async def fetch_agentcdn(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch URL content via agentcdn markdown proxy."""
    try:
        resp = await client.get(
            f"{AGENTCDN_BASE}/{url}",
            params={"refresh": "true"},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  WARN: agentcdn failed for {url}: {e}")
        return None


async def fetch_browser(url: str) -> str | None:
    """Fetch URL content via agent-browser (headless, for JS-heavy sites)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "agent-browser", "--url", url, "--format", "markdown",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        if proc.returncode == 0 and stdout:
            return stdout.decode()
        print(f"  WARN: agent-browser failed for {url}: {stderr.decode()[:200]}")
        return None
    except Exception as e:
        print(f"  WARN: agent-browser failed for {url}: {e}")
        return None


async def fetch_source(client: httpx.AsyncClient, source: dict) -> tuple[str, str | None]:
    """Fetch a single source, returning (name, content|None)."""
    name = source["name"]
    url = source["url"]
    method = source.get("method", "agentcdn")

    print(f"  Fetching: {name} ({method})")

    if method == "browser":
        content = await fetch_browser(url)
    else:
        content = await fetch_agentcdn(client, url)

    if content:
        print(f"  OK: {name} — {len(content)} chars")
    else:
        print(f"  FAIL: {name}")

    return name, content


async def fetch_all_sources(sources_yaml: Path) -> dict[str, str]:
    """Fetch all sources from config. Returns {source_name: markdown_content}."""
    with open(sources_yaml) as f:
        config = yaml.safe_load(f)

    sources = config.get("sources", [])
    results = {}

    async with httpx.AsyncClient() as client:
        tasks = [fetch_source(client, s) for s in sources]
        completed = await asyncio.gather(*tasks)

    for name, content in completed:
        if content:
            results[name] = content

    succeeded = len(results)
    failed = len(sources) - succeeded
    print(f"\n  Sources: {succeeded} succeeded, {failed} failed out of {len(sources)}")

    return results
