"""Naukri.com fetcher.

Strategy:
1. Build search URL with hyphenated query and optional location path segment.
2. Paginate via next-button (CSS: a.pagination-next).
3. Collect job detail URLs from listing card links (a.title within article.jobTuple).
4. Navigate to each detail URL and capture rendered HTML.
5. Return list of (html, canonical_url).

Naukri has weak bot detection; no proxies needed for typical volumes.
"""

from __future__ import annotations

from urllib.parse import quote_plus, urljoin

from botasaurus.browser import Driver, Wait, browser

from .base import load_site_config, make_browser_options
from ..utils.logging import get_logger
from ..utils.pacing import human_sleep

log = get_logger("naukri")


def _build_search_url(template: str, query: str, location: str) -> str:
    hyphenated_query = "-".join(query.strip().split())
    if location and location.lower() not in ("india", ""):
        hyphenated_loc = "-".join(location.strip().split()).lower()
        # Naukri path format: /{query}-jobs-in-{location}
        path_url = template.replace(
            f"{hyphenated_query}-jobs",
            f"{hyphenated_query}-jobs-in-{hyphenated_loc}",
        )
        return path_url
    return template.format(query=hyphenated_query)


def fetch_naukri(
    query: str, location: str, max_jobs: int
) -> list[tuple[str, str]]:
    """Return up to max_jobs (raw_html, canonical_url) pairs for the query."""
    config = load_site_config("naukri")
    selectors = config["selectors"]
    base_url = config["base_url"]
    search_url = _build_search_url(
        config["search_url"].format(query="-".join(query.strip().split())),
        query,
        location,
    )
    min_delay = float(config.get("min_delay_seconds", 3))
    max_delay = float(config.get("max_delay_seconds", 8))

    @browser(**make_browser_options("naukri", config))
    def _scrape(driver: Driver, data: dict) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        log.info("naukri_search", url=search_url, query=query)
        driver.get(search_url)
        human_sleep(min_delay, max_delay)

        seen: set[str] = set()
        urls: list[str] = []

        while len(urls) < max_jobs:
            cards = driver.select_all(selectors["job_link"]) or []
            for card in cards:
                href = card.get_attribute("href") or ""
                if not href:
                    continue
                canonical = href.split("?")[0]
                if not canonical.startswith("http"):
                    canonical = urljoin(base_url, canonical)
                if canonical in seen:
                    continue
                seen.add(canonical)
                urls.append(canonical)
                if len(urls) >= max_jobs:
                    break

            log.info("listing_progress", collected=len(urls), target=max_jobs)
            if len(urls) >= max_jobs:
                break

            next_btn = driver.select(selectors["pagination_next"])
            if not next_btn:
                log.info("no_next_page")
                break
            next_btn.click()
            human_sleep(min_delay, max_delay)

        log.info("listing_done", collected=len(urls))

        jd_selector = selectors["jd_body"]
        for idx, url in enumerate(urls[:max_jobs]):
            try:
                driver.get(url)
                human_sleep(min_delay, max_delay)
                driver.select(jd_selector, wait=Wait.LONG)
                html = driver.run_js("return document.documentElement.outerHTML")
                if not html:
                    log.warning("empty_html", url=url)
                    continue
                results.append((html, url))
                log.info("detail_fetched", idx=idx, url=url)
            except Exception as e:  # noqa: BLE001
                log.warning("detail_failed", url=url, error=str(e))
                continue

        return results

    return _scrape({"query": query, "location": location, "max_jobs": max_jobs})
