"""Indeed India fetcher using SeleniumBase Pure CDP mode.

Strategy:
1. Build search URL with query, location, entry_level filter, and 30-day recency.
2. Open pages with Chrome for Testing via SeleniumBase Pure CDP.
3. Paginate via next-button (data-testid="pagination-page-next").
4. Collect job detail URLs from listing cards (a.jcs-JobTitle).
5. Navigate to each detail URL and capture rendered HTML.
6. Return list of (html, canonical_url).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin, urlparse

from ..utils.logging import get_logger
from ..utils.pacing import human_sleep
from ..utils.seleniumbase_compat import close_pure_cdp_browser, open_pure_cdp_browser
from .base import load_site_config
from .browser import fetch_detail_url
from .browser_context import browser_session, get_browser, has_browser

log = get_logger("indeed")

_CAPTCHA_SIGNALS = (
    "additional verification required",
    "just a moment",
    "enable javascript and cookies",
    "robot check",
    "are you a robot",
    "captcha",
    "cloudflare",
)


def _expand_company_queries(queries: list[str], company_names: list[str]) -> list[str]:
    companies = [company_name.strip() for company_name in company_names if company_name.strip()]
    if not companies:
        return queries
    return [f'{query} company:"{company_name}"' for query in queries for company_name in companies]


def _page_title(sb: Any) -> str:
    try:
        return sb.get_title() or ""
    except Exception:  # noqa: BLE001
        return ""


def _page_text(sb: Any) -> str:
    try:
        return (sb.get_text("body") or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _current_url(sb: Any) -> str:
    try:
        return sb.get_current_url() or ""
    except Exception:  # noqa: BLE001
        return ""


def _is_captcha(sb: Any) -> bool:
    return any(signal in _page_text(sb) for signal in _CAPTCHA_SIGNALS)


def _find_elements(sb: Any, selector: str) -> list[Any]:
    try:
        return list(sb.select_all(selector) or [])
    except Exception:  # noqa: BLE001
        return []


def _has_selector(sb: Any, selector: str) -> bool:
    return bool(_find_elements(sb, selector))


def _log_captcha_unsolved(sb: Any, stage: str) -> None:
    log.warning(
        "captcha_unsolved",
        stage=stage,
        title=_page_title(sb),
        url=_current_url(sb),
    )


def _solve_captcha_if_needed(
    sb: Any,
    stage: str,
    expected_selector: str,
    timeout_seconds: int,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    attempts = 0

    while time.monotonic() < deadline:
        if _has_selector(sb, expected_selector) and not _is_captcha(sb):
            if attempts:
                log.info("captcha_cleared", stage=stage, url=_current_url(sb))
            return True

        attempts += 1
        log.info(
            "captcha_attempt",
            stage=stage,
            attempt=attempts,
            title=_page_title(sb),
            url=_current_url(sb),
        )
        try:
            sb.solve_captcha()
        except Exception as e:  # noqa: BLE001
            log.warning("captcha_click_failed", stage=stage, error=str(e))

        remaining = max(0, int(deadline - time.monotonic()))
        if remaining <= 0:
            break
        time.sleep(min(8, max(2, remaining)))

    _log_captcha_unsolved(sb, stage)
    return False


def _open_page(
    sb: Any,
    url: str,
    stage: str,
    expected_selector: str,
    captcha_timeout: int,
) -> bool:
    log.info("indeed_open", stage=stage, url=url)
    sb.open(url)
    return _solve_captcha_if_needed(sb, stage, expected_selector, captcha_timeout)


def _page_html(sb: Any) -> str:
    try:
        return sb.get_page_source(include_shadow_dom=False) or ""
    except Exception:  # noqa: BLE001
        return sb.get_page_source() or ""


def _element_attr(element: Any, attr: str) -> str:
    try:
        return element.get_attribute(attr) or ""
    except Exception:  # noqa: BLE001
        return ""


def _normalize_job_url(base_url: str, href: str) -> str:
    absolute = urljoin(base_url, href)
    parsed = urlparse(absolute)
    query = parse_qs(parsed.query)
    jk = query.get("jk", [None])[0]
    if jk:
        return urljoin(base_url, f"/viewjob?{urlencode({'jk': jk})}")
    return absolute


def fetch_indeed(query: str, location: str, max_jobs: int) -> list[tuple[str, str]]:
    """Return up to max_jobs (raw_html, canonical_url) pairs for the query.

    Uses the shared browser from context if one is active; otherwise opens
    and closes its own browser instance.
    """
    if has_browser():
        return _fetch_indeed_impl(query, location, max_jobs)

    config = load_site_config("indeed")
    sb = open_pure_cdp_browser("indeed", config)
    try:
        with browser_session(sb, config):
            return _fetch_indeed_impl(query, location, max_jobs)
    finally:
        close_pure_cdp_browser(sb)


def _fetch_indeed_impl(query: str, location: str, max_jobs: int) -> list[tuple[str, str]]:
    sb, config = get_browser()
    selectors = config["selectors"]
    base_url = config["base_url"]
    search_url = config["search_url"].format(
        query=quote_plus(query),
        location=quote_plus(location or "India"),
    )
    min_delay = float(config.get("min_delay_seconds", 4))
    max_delay = float(config.get("max_delay_seconds", 10))
    captcha_timeout = int(config.get("captcha_solve_timeout_seconds", 90))

    results: list[tuple[str, str]] = []
    log.info("indeed_search", url=search_url, query=query)

    if not _open_page(
        sb,
        search_url,
        "listing",
        selectors["job_link"],
        captcha_timeout,
    ):
        return results
    human_sleep(min_delay, max_delay)

    seen: set[str] = set()
    urls: list[str] = []

    while len(urls) < max_jobs:
        cards = _find_elements(sb, selectors["job_link"])
        for card in cards:
            href = _element_attr(card, "href")
            if not href:
                continue
            canonical = _normalize_job_url(base_url, href)
            if canonical in seen:
                continue
            seen.add(canonical)
            urls.append(canonical)
            if len(urls) >= max_jobs:
                break

        log.info("listing_progress", collected=len(urls), target=max_jobs)
        if len(urls) >= max_jobs:
            break

        if not _has_selector(sb, selectors["pagination_next"]):
            log.info("no_next_page")
            break

        sb.click(selectors["pagination_next"])
        human_sleep(min_delay, max_delay)
        if not _solve_captcha_if_needed(
            sb,
            "pagination",
            selectors["job_link"],
            captcha_timeout,
        ):
            break

    log.info("listing_done", collected=len(urls))

    jd_selector = selectors["jd_body"]
    for idx, url in enumerate(urls[:max_jobs]):
        try:
            if not _open_page(sb, url, "detail", jd_selector, captcha_timeout):
                break
            human_sleep(min_delay, max_delay)

            html = _page_html(sb)
            if not html:
                log.warning("empty_html", url=url)
                continue
            results.append((html, url))
            log.info("detail_fetched", idx=idx, url=url)
        except Exception as e:  # noqa: BLE001
            log.warning("detail_failed", url=url, error=str(e))
            continue

    return results


def fetch_indeed_url(url: str) -> list[tuple[str, str]]:
    """Return rendered HTML for one Indeed detail URL."""
    config = load_site_config("indeed")
    canonical_url = _normalize_job_url(config["base_url"], url)
    captcha_timeout = int(config.get("captcha_solve_timeout_seconds", 90))

    def detail_ready(sb: Any, jd_selector: str) -> bool:
        return _solve_captcha_if_needed(sb, "detail", jd_selector, captcha_timeout)

    return fetch_detail_url("indeed", canonical_url, config, log, ready_check=detail_ready)
