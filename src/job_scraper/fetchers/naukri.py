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
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from selectolax.parser import HTMLParser

from ..utils.logging import get_logger
from ..utils.pacing import human_sleep
from ..utils.seleniumbase_compat import (
    close_pure_cdp_browser,
    has_selector,
    open_pure_cdp_browser,
)
from .base import load_site_config
from .browser import capture_detail_html, fetch_detail_url, open_page, page_html

log = get_logger("naukri")


def _slugify(value: str) -> str:
    return "-".join(value.strip().lower().split())


def _query_path_slug(query: str) -> str:
    tokens = query.strip().split()
    if tokens and tokens[-1].lower() == "jobs":
        tokens = tokens[:-1]
    return _slugify(" ".join(tokens))


def _build_search_url(
    template: str,
    query: str,
    location: str,
    company_name: str | None = None,
) -> str:
    effective_query = query.strip()
    if company_name and company_name.strip():
        effective_query = f"{effective_query} at {company_name.strip()}"

    parsed = urlparse(template.format(query=_query_path_slug(effective_query)))
    query_slug = _query_path_slug(effective_query)
    location_slug = _slugify(location)

    path = f"/{query_slug}-jobs"
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["k"] = effective_query

    if location_slug and location_slug != "india":
        path = f"{path}-in-{location_slug}"
        params["l"] = location.strip()
    else:
        params.pop("l", None)

    encoded_params = urlencode(params).replace("+", "%20")
    return urlunparse(parsed._replace(path=path, query=encoded_params))


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


def _has_selector(sb: Any, selector: str) -> bool:
    return has_selector(sb, selector)


def fetch_naukri(
    query: str,
    location: str,
    max_jobs: int,
    company_name: str | None = None,
) -> list[tuple[str, str]]:
    """Return up to max_jobs (raw_html, canonical_url) pairs for the query."""
    config = load_site_config("naukri")
    selectors = config["selectors"]
    base_url = config["base_url"]
    search_url = _build_search_url(config["search_url"], query, location, company_name)
    min_delay = float(config.get("min_delay_seconds", 3))
    max_delay = float(config.get("max_delay_seconds", 8))

    results: list[tuple[str, str]] = []
    log.info("naukri_search", url=search_url, query=query, company=company_name)

    sb = open_pure_cdp_browser("naukri", config)
    try:
        open_page(sb, search_url)
        human_sleep(min_delay, max_delay)

        seen: set[str] = set()
        urls: list[str] = []

        while len(urls) < max_jobs:
            urls.extend(
                _collect_listing_urls_from_html(
                    html=page_html(sb),
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
                html = capture_detail_html(
                    sb,
                    url,
                    jd_selector,
                    min_delay,
                    max_delay,
                    log,
                )
                if html is None:
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
    return fetch_detail_url("naukri", url, config, log)
