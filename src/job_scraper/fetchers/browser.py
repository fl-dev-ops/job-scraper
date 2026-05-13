"""Shared browser helpers for fetchers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..utils.pacing import human_sleep
from ..utils.seleniumbase_compat import (
    close_pure_cdp_browser,
    open_pure_cdp_browser,
    wait_for_selector,
)

ReadyCheck = Callable[[Any, str], bool]


def open_page(sb: Any, url: str) -> None:
    sb.open(url)


def page_html(sb: Any) -> str:
    return sb.get_page_source(include_shadow_dom=False) or ""


def capture_detail_html(
    sb: Any,
    url: str,
    jd_selector: str,
    min_delay: float,
    max_delay: float,
    log: Any,
    ready_check: ReadyCheck | None = None,
) -> str | None:
    open_page(sb, url)
    human_sleep(min_delay, max_delay)
    if ready_check:
        if not ready_check(sb, jd_selector):
            return None
    else:
        wait_for_selector(sb, jd_selector, timeout=20)
    html = page_html(sb)
    if not html:
        log.warning("empty_html", url=url)
        return None
    return html


def fetch_detail_url(
    site: str,
    url: str,
    config: dict[str, Any],
    log: Any,
    ready_check: ReadyCheck | None = None,
) -> list[tuple[str, str]]:
    selectors = config["selectors"]
    min_delay = float(config.get("min_delay_seconds", 3))
    max_delay = float(config.get("max_delay_seconds", 8))
    jd_selector = selectors["jd_body"]

    log.info("detail_open", site=site, url=url)
    sb = open_pure_cdp_browser(site, config)
    try:
        html = capture_detail_html(
            sb,
            url,
            jd_selector,
            min_delay,
            max_delay,
            log,
            ready_check=ready_check,
        )
        if html is None:
            return []
        return [(html, url)]
    except Exception as e:  # noqa: BLE001
        log.warning("detail_failed", url=url, error=str(e))
        return []
    finally:
        close_pure_cdp_browser(sb)
