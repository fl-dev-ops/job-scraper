"""LinkedIn fetcher (guest mode, no login).

Strategy:
1. Open the public guest jobs search URL (?keywords=...&f_E=2 = Entry level).
2. Scroll the listing column to lazy-load cards in the current result window.
3. Advance to the next result window and repeat until enough jobs are collected.
4. Collect each card's detail-page URL ("/jobs/view/{id}").
5. For each detail URL, navigate and capture the rendered HTML.
6. Return list of (html, canonical_url).

This uses the same SeleniumBase Pure CDP + Chrome for Testing browser path as
the Indeed fetcher. Listing and detail navigation share one browser instance.

Soft-block handling: login walls / auth gates are detected on each page; when
seen, we log block_signal and stop collecting (rather than crash). Caller
sees whatever was successfully collected before the block.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from ..utils.logging import get_logger
from ..utils.pacing import human_sleep
from ..utils.seleniumbase_compat import (
    close_pure_cdp_browser,
    open_pure_cdp_browser,
    wait_for_selector,
)
from .base import load_site_config
from .browser import capture_detail_html, fetch_detail_url, open_page, page_html

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


def _run_js(sb: Any, script: str) -> Any:
    try:
        return sb.execute_script(script)
    except Exception:  # noqa: BLE001
        return None


def _is_blocked(sb: Any) -> str | None:
    """Detect a real wall by URL or page-level class — not header form fields.

    LinkedIn's header always contains a sign-in form even when content is
    visible to guests; checking for that form gives false positives.
    """
    try:
        current_url = sb.get_current_url() or ""
    except Exception:  # noqa: BLE001
        current_url = ""
    for frag in _BLOCKING_URL_FRAGMENTS:
        if frag in current_url:
            return f"redirect:{frag}"
    # Body-level authwall class is set when LinkedIn forces a block
    has_authwall_body = _run_js(
        sb,
        "return document.body && document.body.classList.contains('authwall') ? 1 : 0",
    )
    if has_authwall_body:
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

    results: list[tuple[str, str]] = []

    sb = open_pure_cdp_browser("linkedin", config)
    try:
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
            open_page(sb, page_url)
            human_sleep(min_delay, max_delay)

            signal = _is_blocked(sb)
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
                listing_html = page_html(sb)
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
                _run_js(sb, f"window.scrollBy(0, {scroll_depth});")
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
                html = capture_detail_html(
                    sb,
                    url,
                    jd_selector,
                    min_delay,
                    max_delay,
                    log,
                    ready_check=_detail_ready,
                )
                if html is None:
                    break
                results.append((html, url))
                log.info("detail_fetched", idx=idx, url=url)
            except Exception as e:  # noqa: BLE001 — best-effort detail capture
                log.warning("detail_failed", url=url, error=str(e))
                continue
    finally:
        close_pure_cdp_browser(sb)

    return results


def _detail_ready(sb: Any, jd_selector: str) -> bool:
    signal = _is_blocked(sb)
    if signal:
        log.warning("block_signal", signal=signal, stage="detail", url=_current_url(sb))
        return False
    wait_for_selector(sb, jd_selector, timeout=20)
    return True


def _current_url(sb: Any) -> str:
    try:
        return sb.get_current_url() or ""
    except Exception:  # noqa: BLE001
        return ""


def fetch_linkedin_url(url: str) -> list[tuple[str, str]]:
    """Return rendered HTML for one LinkedIn detail URL."""
    config = load_site_config("linkedin")
    canonical_url = urljoin(config["base_url"], url.split("?")[0])
    return fetch_detail_url("linkedin", canonical_url, config, log, ready_check=_detail_ready)
