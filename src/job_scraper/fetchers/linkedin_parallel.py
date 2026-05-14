"""LinkedIn parallel scraping orchestration.

Handles listing browser reuse across queries and concurrent detail-page
scraping via multiple browser workers.  Each worker fetches a detail page,
then calls the *extract_and_write* callback so the caller controls
extraction + persistence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urljoin

from ..normalizer import compute_job_id
from ..schema import JobPosting
from ..utils.logging import get_logger
from ..utils.seleniumbase_compat import close_pure_cdp_browser, open_pure_cdp_browser
from .base import load_site_config
from .browser import capture_detail_html
from .linkedin import _detail_ready, collect_linkedin_urls_in_browser

log = get_logger("linkedin")

# ── Callback types ────────────────────────────────────────────────────
LinkedInBrowserOpener = Callable[[], tuple[Any, dict[str, Any]]]
LinkedInBrowserFetcher = Callable[[Any, dict[str, Any], str], tuple[str, str] | None]
LinkedInBrowserCloser = Callable[[Any], None]
ExtractAndWrite = Callable[[str, str], Coroutine[Any, Any, JobPosting | None]]


# ── Browser lifecycle helpers ─────────────────────────────────────────


def open_linkedin_detail_browser() -> tuple[Any, dict[str, Any]]:
    """Open one reusable LinkedIn detail-page browser session."""
    config = load_site_config("linkedin")
    return open_pure_cdp_browser("linkedin", config), config


def fetch_linkedin_detail_in_browser(
    sb: Any,
    config: dict[str, Any],
    url: str,
) -> tuple[str, str] | None:
    """Capture one LinkedIn detail page with an already-open browser."""
    canonical_url = urljoin(config["base_url"], url.split("?")[0])
    selectors = config["selectors"]
    html = capture_detail_html(
        sb,
        canonical_url,
        selectors["jd_body"],
        float(config.get("min_delay_seconds", 4)),
        float(config.get("max_delay_seconds", 10)),
        log,
        ready_check=_detail_ready,
    )
    if html is None:
        return None
    return html, canonical_url


def close_linkedin_detail_browser(sb: Any) -> None:
    close_pure_cdp_browser(sb)


# ── Parallel workers ──────────────────────────────────────────────────


async def _scrape_extract_write_linkedin_url(
    url_queue: asyncio.Queue[str],
    browser_opener: LinkedInBrowserOpener,
    browser_fetcher: LinkedInBrowserFetcher,
    browser_closer: LinkedInBrowserCloser,
    extract_and_write: ExtractAndWrite,
    seen_ids: set[str],
    log: Any,
) -> int:
    """Reuse one LinkedIn browser for full scrape -> extract -> write flows."""
    loop = asyncio.get_running_loop()
    written = 0

    with ThreadPoolExecutor(max_workers=1) as executor:
        sb: Any | None = None
        try:
            sb, config = await loop.run_in_executor(executor, browser_opener)
            while True:
                try:
                    url = url_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                job_id = compute_job_id(url)
                if job_id in seen_ids:
                    url_queue.task_done()
                    continue

                page = await loop.run_in_executor(executor, browser_fetcher, sb, config, url)
                if page is None:
                    log.warning("detail_empty", url=url)
                    url_queue.task_done()
                    continue

                html, source_url = page
                try:
                    posting = await extract_and_write(html, source_url)
                    if posting is not None:
                        written += 1
                finally:
                    url_queue.task_done()
        finally:
            if sb is not None:
                await loop.run_in_executor(executor, browser_closer, sb)
        return written


async def _scrape_linkedin_urls_for_query(
    urls: list[str],
    browser_opener: LinkedInBrowserOpener,
    browser_fetcher: LinkedInBrowserFetcher,
    browser_closer: LinkedInBrowserCloser,
    extract_and_write: ExtractAndWrite,
    seen_ids: set[str],
    parallel_scrape: int,
) -> int:
    """Fan out *parallel_scrape* browser workers over *urls*."""
    url_queue: asyncio.Queue[str] = asyncio.Queue()
    for url in urls:
        url_queue.put_nowait(url)

    worker_count = min(max(parallel_scrape, 1), len(urls))
    tasks = [
        _scrape_extract_write_linkedin_url(
            url_queue,
            browser_opener,
            browser_fetcher,
            browser_closer,
            extract_and_write,
            seen_ids,
            log,
        )
        for _ in range(worker_count)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    written = 0
    for result in results:
        if isinstance(result, Exception):
            log.error("linkedin_job_task_failed", error=str(result))
        else:
            written += result
    return written


# ── Public entry point ────────────────────────────────────────────────


def run_linkedin_site(
    queries: list[str],
    location: str,
    max_jobs: int,
    extract_and_write: ExtractAndWrite,
    seen_ids: set[str],
) -> int:
    """Run all LinkedIn queries with listing browser reuse and parallel detail scraping.

    *extract_and_write* is an async ``async (html, url) -> JobPosting | None``
    callback that handles LLM extraction + persistence.  This keeps the
    orchestration layer decoupled from extraction details.
    """
    config = load_site_config("linkedin")
    parallel_scrape = max(int(config.get("parallel_scrape", 1)), 1)

    listing_sb, listing_config = open_linkedin_detail_browser()
    seen_listing_urls: set[str] = set()
    written = 0
    try:
        for query in queries:
            log.info(
                "query_start",
                query=query,
                max_per_query=max_jobs,
                parallel_scrape=parallel_scrape,
            )
            try:
                urls = collect_linkedin_urls_in_browser(
                    listing_sb,
                    listing_config,
                    query,
                    location,
                    max_jobs,
                    seen_urls=seen_listing_urls,
                )
            except Exception as e:  # noqa: BLE001
                log.error("url_collection_crashed", query=query, error=str(e))
                continue

            log.info("linkedin_urls_collected", query=query, urls=len(urls))
            written += asyncio.run(
                _scrape_linkedin_urls_for_query(
                    urls=urls,
                    browser_opener=open_linkedin_detail_browser,
                    browser_fetcher=fetch_linkedin_detail_in_browser,
                    browser_closer=close_linkedin_detail_browser,
                    extract_and_write=extract_and_write,
                    seen_ids=seen_ids,
                    parallel_scrape=parallel_scrape,
                )
            )
    finally:
        close_linkedin_detail_browser(listing_sb)

    return written
