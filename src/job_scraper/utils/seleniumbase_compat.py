"""Small compatibility helpers for SeleniumBase CDP mode."""

from __future__ import annotations

import asyncio
import os
import platform
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from seleniumbase import sb_cdp  # type: ignore[import-untyped]
from seleniumbase.core import browser_launcher  # type: ignore[import-untyped]


def chrome_for_testing_path() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    if system == "darwin":
        folder = "chrome-mac-arm64" if machine == "arm64" else "chrome-mac-x64"
        binary = (
            Path(browser_launcher.DRIVER_DIR_CFT)
            / folder
            / "Google Chrome for Testing.app"
            / "Contents"
            / "MacOS"
            / "Google Chrome for Testing"
        )
    elif system.startswith("linux"):
        binary = Path(browser_launcher.DRIVER_DIR_CFT) / "chrome-linux64" / "chrome"
    elif system.startswith("win"):
        folder = "chrome-win64" if "64" in machine else "chrome-win32"
        binary = Path(browser_launcher.DRIVER_DIR_CFT) / folder / "chrome.exe"
    else:
        raise RuntimeError(f"Unsupported platform for Chrome for Testing: {system}")

    if not binary.exists():
        raise RuntimeError("Chrome for Testing is missing. Run: uv run sbase get cft")
    return str(binary)


def open_pure_cdp_browser(site: str, site_config: dict[str, Any]) -> Any:
    """Open Chrome for Testing through SeleniumBase Pure CDP."""
    apply_mycdp_patches()
    proxy = (
        site_config.get("proxy_url")
        or os.getenv(f"{site.upper()}_PROXY_URL")
        or os.getenv("PROXY_URL")
        or None
    )
    return sb_cdp.Chrome(
        url=None,
        browser_executable_path=chrome_for_testing_path(),
        headless=bool(site_config.get("headless", False)),
        incognito=True,
        lang="en",
        proxy=proxy,
    )


def select_all(sb: Any, selector: str) -> list[Any]:
    try:
        return list(sb.select_all(selector) or [])
    except Exception:  # noqa: BLE001
        return []


def has_selector(sb: Any, selector: str) -> bool:
    return bool(select_all(sb, selector))


def wait_for_selector(sb: Any, selector: str, timeout: int = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if has_selector(sb, selector):
            return
        time.sleep(0.5)
    raise TimeoutError(f"Element {{{selector}}} was not found after {timeout} seconds!")


def apply_mycdp_patches() -> None:
    """Tolerate newer Chrome resource types that older mycdp doesn't know."""
    try:
        from mycdp import network
    except ImportError:
        return

    if getattr(network.ResourceType, "_job_scraper_patched", False):
        return

    def _safe_from_json(cls: type[Any], value: str) -> Any:
        try:
            return cls(value)
        except ValueError:
            return cls.OTHER

    network.ResourceType.from_json = classmethod(_safe_from_json)  # type: ignore[assignment,method-assign]
    network.ResourceType._job_scraper_patched = True  # type: ignore[attr-defined]


def _connection_loop(connection: Any) -> asyncio.AbstractEventLoop | None:
    listener = getattr(connection, "listener", None)
    task = getattr(listener, "task", None)
    if task:
        with suppress(Exception):
            return task.get_loop()
    return None


def _run_on_loop(loop: asyncio.AbstractEventLoop | None, awaitable: Any) -> None:
    if not loop or loop.is_closed():
        with suppress(Exception):
            awaitable.close()
        return
    with suppress(Exception):
        loop.run_until_complete(awaitable)


def _drain_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    if not loop or loop.is_closed():
        return
    with suppress(Exception):
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    with suppress(Exception):
        loop.run_until_complete(loop.shutdown_asyncgens())


def close_pure_cdp_browser(sb: Any) -> None:
    """Best-effort shutdown for SeleniumBase Pure CDP browser objects."""
    driver = getattr(sb, "driver", None)
    page = getattr(sb, "page", None)
    page_loop = getattr(sb, "loop", None)
    browser_connection = getattr(driver, "connection", None) if driver else None
    browser_loop = _connection_loop(browser_connection)
    process = getattr(driver, "_process", None) if driver else None

    previous_loop: asyncio.AbstractEventLoop | None = None
    with suppress(RuntimeError):
        previous_loop = asyncio.get_event_loop()

    if page and hasattr(page, "aclose"):
        _run_on_loop(page_loop, page.aclose())

    if browser_connection and hasattr(browser_connection, "aclose"):
        _run_on_loop(browser_loop, browser_connection.aclose())

    if driver:
        with suppress(Exception):
            driver.connection = None
        with suppress(Exception):
            driver.stop()
        with suppress(Exception):
            driver.connection = browser_connection

    if process and browser_loop and not browser_loop.is_closed():
        with suppress(Exception):
            browser_loop.run_until_complete(asyncio.wait_for(process.wait(), timeout=5))

    for loop in {page_loop, browser_loop}:
        _drain_loop(loop)
        if loop and not loop.is_closed():
            with suppress(Exception):
                loop.close()

    with suppress(Exception):
        if previous_loop and not previous_loop.is_closed():
            asyncio.set_event_loop(previous_loop)
        else:
            asyncio.set_event_loop(None)
