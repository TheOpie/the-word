"""Source fetcher — agentcdn, Playwright (JS-heavy sites), and agent-browser."""

import asyncio
from pathlib import Path

import httpx
import yaml

AGENTCDN_BASE = "https://yellow-resonance-7c40.opieworks-ai.workers.dev/agent"
FETCH_RETRIES = 1
RETRY_DELAY = 3  # seconds

# Realistic UA prevents headless-browser blocks on JS-rendered sites
_PLAYWRIGHT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def fetch_agentcdn(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch URL content via agentcdn markdown proxy. Retries once on transient errors."""
    last_err = None
    for attempt in range(1 + FETCH_RETRIES):
        try:
            resp = await client.get(
                f"{AGENTCDN_BASE}/{url}",
                params={"refresh": "true"},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.text
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_err = e
            if attempt < FETCH_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (502, 503, 429) and attempt < FETCH_RETRIES:
                last_err = e
                await asyncio.sleep(RETRY_DELAY)
            else:
                print(f"  WARN: agentcdn failed for {url}: {e}")
                return None
        except Exception as e:
            print(f"  WARN: agentcdn failed for {url}: {e}")
            return None
    print(f"  WARN: agentcdn failed for {url} after retry: {last_err}")
    return None


async def fetch_playwright_sources(sources: list[dict]) -> dict[str, str]:
    """Fetch JS-heavy sources using a shared Playwright Chromium instance.

    All sources share one browser; pages are opened in parallel.
    Returns {source_name: page_text}.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        for s in sources:
            print(f"  FAIL: {s['name']} — playwright not installed (pip install playwright && playwright install chromium)")
        return {}

    results = {}

    async def _fetch_one(page, source: dict) -> tuple[str, str | None]:
        name = source["name"]
        url = source["url"]
        try:
            # "load" fires when initial resources are done; networkidle is too
            # strict for sites with persistent analytics/ad requests.
            # Short sleep lets JS finish rendering the main content.
            await page.goto(url, wait_until="load", timeout=45_000)
            await page.wait_for_timeout(2_000)
            # Annotate links with their absolute URLs before extracting text so
            # the structurer can populate sourceUrl for each event.
            text = await page.evaluate("""() => {
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.getAttribute('href');
                    if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
                        try {
                            const abs = new URL(href, location.href).href;
                            a.textContent = a.textContent + ' [' + abs + ']';
                        } catch(e) {}
                    }
                });
                return document.body.innerText;
            }""")
            if text and len(text.strip()) > 100:
                return name, text
            print(f"  WARN: playwright got thin content for {name} ({len(text.strip())} chars)")
            return name, None
        except Exception as e:
            print(f"  WARN: playwright failed for {name}: {str(e) or type(e).__name__}")
            return name, None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=_PLAYWRIGHT_UA)
        pages = [await context.new_page() for _ in sources]
        pairs = list(zip(pages, sources))
        completed = await asyncio.gather(*[_fetch_one(page, source) for page, source in pairs])
        await browser.close()

    for name, content in completed:
        if content:
            print(f"  OK: {name} — {len(content)} chars")
            results[name] = content
        else:
            print(f"  FAIL: {name}")

    return results


async def fetch_browser(url: str) -> str | None:
    """Fetch URL content via agent-browser (headless, for JS-heavy sites).

    Uses sequential commands: open URL, wait for JS to render, extract text.
    Browser sessions are sequential (not parallel) to avoid conflicts.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "npx", "agent-browser", "open", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            print("  WARN: agent-browser open failed for {}: {}".format(url, stderr.decode()[:200]))
            return None

        proc = await asyncio.create_subprocess_exec(
            "npx", "agent-browser", "wait", "--load", "networkidle",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30.0)

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
        print("  WARN: agent-browser failed for {}: {}".format(url, str(e) or type(e).__name__))
        return None


async def fetch_source(client: httpx.AsyncClient, source: dict) -> tuple[str, str | None]:
    """Fetch a single agentcdn or browser source, returning (name, content|None)."""
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

    agentcdn sources run in parallel. Playwright sources share one browser
    instance with parallel pages. Browser (npx) sources run sequentially.
    """
    if not sources_yaml.exists():
        print("  ERROR: Sources config not found: {}".format(sources_yaml))
        return {}

    try:
        with open(sources_yaml) as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print("  ERROR: Failed to parse sources config: {}".format(e))
        return {}

    sources = config.get("sources", [])
    if not sources:
        print("  ERROR: No sources defined in config")
        return {}

    results = {}

    cdn_sources = [s for s in sources if s.get("method", "agentcdn") == "agentcdn"]
    playwright_sources = [s for s in sources if s.get("method") == "playwright"]
    browser_sources = [s for s in sources if s.get("method") == "browser"]

    # agentcdn sources — parallel HTTP
    async with httpx.AsyncClient() as client:
        tasks = [fetch_source(client, s) for s in cdn_sources]
        completed = await asyncio.gather(*tasks)
    for name, content in completed:
        if content:
            results[name] = content

    # Playwright sources — shared browser, parallel pages
    if playwright_sources:
        for s in playwright_sources:
            print(f"  Fetching: {s['name']} (playwright)")
        pw_results = await fetch_playwright_sources(playwright_sources)
        results.update(pw_results)

    # npx agent-browser sources — sequential
    if browser_sources:
        for source in browser_sources:
            name, content = await fetch_source(None, source)
            if content:
                results[name] = content
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
