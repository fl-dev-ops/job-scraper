"""LinkedIn fetcher (guest mode, no login).

Strategy:
1. Open the public guest jobs search URL (?keywords=...&f_E=2 = Entry level).
2. Scroll the listing column to lazy-load cards in the current result window.
3. Advance to the next result window and repeat until enough jobs are collected.
4. Collect each card's detail-page URL ("/jobs/view/{id}").
5. For each detail URL, navigate and capture the rendered HTML.
6. Return list of (html, canonical_url).

This intentionally uses Botasaurus's @browser decorator so the listing scroll
and the per-detail navigation share one driver instance — fewer launches,
fewer fingerprint-detection signals.

Soft-block handling: login walls / auth gates are detected on each page; when
seen, we log block_signal and stop collecting (rather than crash). Caller
sees whatever was successfully collected before the block.
"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

from botasaurus.browser import Driver, Wait, browser  # type: ignore[import-untyped]
from selectolax.parser import HTMLParser

from ..utils.logging import get_logger
from ..utils.pacing import human_sleep
from .base import load_site_config, make_browser_options

log = get_logger("linkedin")

LINKEDIN_PAGE_SIZE = 25
SCROLLS_PER_LISTING_PAGE = 6
LISTING_PAGE_BUFFER = 2


_BLOCKING_URL_FRAGMENTS = (
    "/authwall",
    "/login",
    "/uas/login",
    "/checkpoint/",
    "/signup",
)


def _load_company_ids(config: dict[str, Any]) -> tuple[str, ...]:
    raw_company_ids = config.get("company_ids") or []
    if not isinstance(raw_company_ids, list):
        raise TypeError("company_ids must be a YAML list of LinkedIn company IDs")

    company_ids: list[str] = []
    for company_id in raw_company_ids:
        cleaned = str(company_id).strip()
        if cleaned:
            company_ids.append(cleaned)
    return tuple(company_ids)


def _build_search_url(config: dict[str, Any], query: str, location: str) -> str:
    search_template = str(config["search_url"])
    search_url = search_template.format(
        query=quote_plus(query),
        location=quote_plus(location or "India"),
    )
    company_ids = _load_company_ids(config)
    if not company_ids:
        return search_url

    parsed = urlsplit(search_url)
    params = [(key, value) for key, value in parse_qsl(parsed.query) if key != "f_C"]
    params.append(("f_C", ",".join(company_ids)))
    filtered_url = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(params),
            parsed.fragment,
        )
    )
    return filtered_url


def _build_paginated_search_url(search_url: str, page_index: int, page_size: int) -> str:
    parsed = urlsplit(search_url)
    params = [
        (key, value)
        for key, value in parse_qsl(parsed.query)
        if key not in {"pageNum", "start"}
    ]
    params.append(("pageNum", str(page_index)))
    params.append(("start", str(page_index * page_size)))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(params),
            parsed.fragment,
        )
    )


def _is_blocked(driver: Driver) -> str | None:
    """Detect a real wall by URL or page-level class — not header form fields.

    LinkedIn's header always contains a sign-in form even when content is
    visible to guests; checking for that form gives false positives.
    """
    current_url = driver.run_js("return window.location.href || ''") or ""
    for frag in _BLOCKING_URL_FRAGMENTS:
        if frag in current_url:
            return f"redirect:{frag}"
    # Body-level authwall class is set when LinkedIn forces a block
    if driver.run_js(
        "return document.body && document.body.classList.contains('authwall') ? 1 : 0"
    ):
        return "authwall_body"
    return None


def _collect_listing_urls_from_html(
    html: str,
    selectors: dict[str, str],
    base_url: str,
    seen: set[str],
    max_jobs: int,
) -> list[str]:
    parser = HTMLParser(html)
    urls: list[str] = []

    for card in parser.css(selectors["job_cards"]):
        link = card.css_first(selectors["job_link"])
        if link is None:
            continue

        href = link.attributes.get("href") or ""
        if "/jobs/view/" not in href:
            continue

        canonical = urljoin(base_url, href.split("?")[0])
        if canonical in seen:
            continue
        seen.add(canonical)
        urls.append(canonical)
        if len(urls) >= max_jobs:
            break

    return urls


def fetch_linkedin(
    query: str, location: str, max_jobs: int
) -> list[tuple[str, str]]:
    """Return up to max_jobs (raw_html, canonical_url) pairs for the query."""
    config = load_site_config("linkedin")
    selectors = config["selectors"]
    search_url = _build_search_url(config, query, location)
    base_url = config["base_url"]
    min_delay = float(config.get("min_delay_seconds", 4))
    max_delay = float(config.get("max_delay_seconds", 10))
    scroll_depth = int(config.get("scroll_load_depth", 3000))
    listing_page_size = int(config.get("listing_page_size", LINKEDIN_PAGE_SIZE))
    scrolls_per_listing_page = int(
        config.get("scrolls_per_listing_page", SCROLLS_PER_LISTING_PAGE)
    )
    max_listing_pages = max(
        (max_jobs // listing_page_size) + LISTING_PAGE_BUFFER + 1,
        1,
    )

    @browser(**make_browser_options("linkedin", config))  # type: ignore[untyped-decorator]
    def _scrape(driver: Driver, data: dict[str, object]) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []

        # Collect detail URLs by processing one paginated listing window at a time.
        seen: set[str] = set()
        urls: list[str] = []
        for page_index in range(max_listing_pages):
            page_url = _build_paginated_search_url(
                search_url,
                page_index=page_index,
                page_size=listing_page_size,
            )
            log.info("linkedin_search", url=page_url, query=query, page_index=page_index)
            driver.get(page_url)
            human_sleep(min_delay, max_delay)

            signal = _is_blocked(driver)
            if signal:
                log.warning(
                    "block_signal",
                    signal=signal,
                    stage="listing",
                    page_index=page_index,
                )
                break

            page_start_count = len(urls)
            for scroll_index in range(scrolls_per_listing_page):
                listing_html = driver.run_js("return document.documentElement.outerHTML") or ""
                urls.extend(
                    _collect_listing_urls_from_html(
                        html=listing_html,
                        selectors=selectors,
                        base_url=base_url,
                        seen=seen,
                        max_jobs=max_jobs - len(urls),
                    )
                )
                log.info(
                    "scroll_progress",
                    page_index=page_index,
                    scroll_index=scroll_index,
                    collected=len(urls),
                    target=max_jobs,
                )
                if len(urls) >= max_jobs:
                    break
                driver.run_js(f"window.scrollBy(0, {scroll_depth});")
                human_sleep(min_delay, max_delay)

            log.info(
                "listing_page_done",
                page_index=page_index,
                collected=len(urls),
                page_new=len(urls) - page_start_count,
                target=max_jobs,
            )
            if len(urls) >= max_jobs:
                break
            if len(urls) == page_start_count:
                log.info("no_listing_results", page_index=page_index)
                break

        log.info("listing_done", collected=len(urls))

        # Detail pages
        jd_selector = selectors["jd_body"]
        for idx, url in enumerate(urls[:max_jobs]):
            try:
                driver.get(url)
                human_sleep(min_delay, max_delay)
                signal = _is_blocked(driver)
                if signal:
                    log.warning("block_signal", signal=signal, stage="detail", url=url)
                    break
                driver.select(jd_selector, wait=Wait.LONG)
                html = driver.run_js(
                    "return document.documentElement.outerHTML"
                )
                results.append((html, url))
                log.info("detail_fetched", idx=idx, url=url)
            except Exception as e:  # noqa: BLE001 — best-effort detail capture
                log.warning("detail_failed", url=url, error=str(e))
                continue

        return results

    return cast(
        "list[tuple[str, str]]",
        _scrape({"query": query, "location": location, "max_jobs": max_jobs}),
    )
