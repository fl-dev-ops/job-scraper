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

from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..utils.logging import get_logger
from ..utils.pacing import human_sleep
from ..utils.seleniumbase_compat import (
    close_pure_cdp_browser,
    has_selector,
    open_pure_cdp_browser,
    wait_for_selector,
)
from .base import load_site_config

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


def _collect_listing_urls_from_html(
    html: str,
    selectors: dict[str, str],
    base_url: str,
    seen: set[str],
    max_jobs: int,
) -> list[str]:
    parser = HTMLParser(html)
    urls: list[str] = []

    for card in parser.css(selectors["job_link"]):
        href = card.attributes.get("href") or ""
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

    return urls


def _page_html(sb: Any) -> str:
    return sb.get_page_source(include_shadow_dom=False) or ""


def _has_selector(sb: Any, selector: str) -> bool:
    return has_selector(sb, selector)


def _open_page(sb: Any, url: str) -> None:
    sb.open(url)


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

    results: list[tuple[str, str]] = []
    log.info("naukri_search", url=search_url, query=query)

    sb = open_pure_cdp_browser("naukri", config)
    try:
        _open_page(sb, search_url)
        human_sleep(min_delay, max_delay)

        seen: set[str] = set()
        urls: list[str] = []

        while len(urls) < max_jobs:
            urls.extend(
                _collect_listing_urls_from_html(
                    html=_page_html(sb),
                    selectors=selectors,
                    base_url=base_url,
                    seen=seen,
                    max_jobs=max_jobs - len(urls),
                )
            )

            log.info("listing_progress", collected=len(urls), target=max_jobs)
            if len(urls) >= max_jobs:
                break

            if not _has_selector(sb, selectors["pagination_next"]):
                log.info("no_next_page")
                break
            sb.click(selectors["pagination_next"])
            human_sleep(min_delay, max_delay)

        log.info("listing_done", collected=len(urls))

        jd_selector = selectors["jd_body"]
        for idx, url in enumerate(urls[:max_jobs]):
            try:
                _open_page(sb, url)
                human_sleep(min_delay, max_delay)
                wait_for_selector(sb, jd_selector, timeout=20)
                html = _page_html(sb)
                if not html:
                    log.warning("empty_html", url=url)
                    continue
                results.append((html, url))
                log.info("detail_fetched", idx=idx, url=url)
            except Exception as e:  # noqa: BLE001
                log.warning("detail_failed", url=url, error=str(e))
                continue
    finally:
        close_pure_cdp_browser(sb)

    return results


def fetch_naukri_url(url: str) -> list[tuple[str, str]]:
    """Return rendered HTML for one Naukri detail URL."""
    config = load_site_config("naukri")
    selectors = config["selectors"]
    min_delay = float(config.get("min_delay_seconds", 3))
    max_delay = float(config.get("max_delay_seconds", 8))
    jd_selector = selectors["jd_body"]

    log.info("naukri_detail", url=url)
    sb = open_pure_cdp_browser("naukri", config)
    try:
        _open_page(sb, url)
        human_sleep(min_delay, max_delay)
        wait_for_selector(sb, jd_selector, timeout=20)
        html = _page_html(sb)
        if not html:
            log.warning("empty_html", url=url)
            return []
        return [(html, url)]
    except Exception as e:  # noqa: BLE001
        log.warning("detail_failed", url=url, error=str(e))
        return []
    finally:
        close_pure_cdp_browser(sb)
