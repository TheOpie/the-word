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
    """Fetch URL content via agent-browser (headless, for JS-heavy sites).

    Uses sequential commands: open URL, wait for JS to render, extract text.
    Browser sessions are sequential (not parallel) to avoid conflicts.
    """
    try:
        # Open URL
        proc = await asyncio.create_subprocess_exec(
            "npx", "agent-browser", "open", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            print("  WARN: agent-browser open failed for {}: {}".format(url, stderr.decode()[:200]))
            return None

        # Wait for JS to finish rendering
        proc = await asyncio.create_subprocess_exec(
            "npx", "agent-browser", "wait", "--load", "networkidle",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30.0)

        # Extract page text
        proc = await asyncio.create_subprocess_exec(
            "npx", "agent-browser", "get", "text", "body",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        if proc.returncode == 0 and stdout:
            return stdout.decode()

        print("  WARN: agent-browser text extraction failed for {}: {}".format(url, stderr.decode()[:200]))
        return None
    except Exception as e:
        print("  WARN: agent-browser failed for {}: {}".format(url, e))
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
    """Fetch all sources from config. Returns {source_name: markdown_content}.

    agentcdn sources run in parallel. Browser sources run sequentially
    (they share one headless browser instance).
    """
    with open(sources_yaml) as f:
        config = yaml.safe_load(f)

    sources = config.get("sources", [])
    results = {}

    # Split by method
    cdn_sources = [s for s in sources if s.get("method", "agentcdn") == "agentcdn"]
    browser_sources = [s for s in sources if s.get("method") == "browser"]

    # Fetch agentcdn sources in parallel
    async with httpx.AsyncClient() as client:
        tasks = [fetch_source(client, s) for s in cdn_sources]
        completed = await asyncio.gather(*tasks)

    for name, content in completed:
        if content:
            results[name] = content

    # Fetch browser sources sequentially (shared browser)
    if browser_sources:
        for source in browser_sources:
            name, content = await fetch_source(None, source)
            if content:
                results[name] = content

        # Close browser session when done
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "agent-browser", "close",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except Exception:
            pass

    succeeded = len(results)
    failed = len(sources) - succeeded
    print("\n  Sources: {} succeeded, {} failed out of {}".format(succeeded, failed, len(sources)))

    return results
