"""Standalone Indeed probe using SeleniumBase Pure CDP + Chrome for Testing.

Run:
    uv run sbase get cft
    uv run python experiments/selenium_base_test.py

This uses a separate Chrome for Testing browser, not your normal Chrome app.
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path
from urllib.parse import urljoin

from job_scraper.utils.seleniumbase_compat import (
    apply_mycdp_patches,
    close_pure_cdp_browser,
)

DEFAULT_URL = (
    "https://in.indeed.com/jobs"
    "?q=junior+software+engineer"
    "&l=bangalore"
    "&explvl=entry_level"
    "&fromage=30"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Indeed with SeleniumBase Pure CDP.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Indeed search URL to open")
    parser.add_argument(
        "--browser-path",
        default="cft",
        help='Chrome binary path, or "cft" for SeleniumBase Chrome for Testing.',
    )
    parser.add_argument(
        "--settle-time",
        type=float,
        default=8,
        help="Seconds to wait after attempting the CAPTCHA click.",
    )
    parser.add_argument(
        "--captcha-attempts",
        type=int,
        default=2,
        help="Number of SeleniumBase CAPTCHA click attempts.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless. Not recommended for CAPTCHA debugging.",
    )
    return parser.parse_args()


def chrome_for_testing_path() -> str:
    from seleniumbase.core import browser_launcher

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
        raise SystemExit(f"Unsupported platform for Chrome for Testing: {system}")

    if not binary.exists():
        raise SystemExit("Chrome for Testing is missing. Run: uv run sbase get cft")
    return str(binary)


def resolve_browser_path(browser_path: str) -> str:
    if browser_path == "cft":
        return chrome_for_testing_path()
    path = Path(browser_path).expanduser()
    if not path.exists():
        raise SystemExit(f"Browser path does not exist: {path}")
    return str(path)


def main() -> int:
    args = parse_args()

    try:
        from seleniumbase import sb_cdp
    except ImportError:
        print("SeleniumBase is not installed. Run: uv sync", file=sys.stderr)
        return 2

    browser_path = resolve_browser_path(args.browser_path)
    apply_mycdp_patches()
    print(f"browser={browser_path}")
    print(f"opening={args.url}")

    sb = sb_cdp.Chrome(
        url=args.url,
        browser_executable_path=browser_path,
        headless=args.headless,
        incognito=True,
        lang="en",
    )
    try:
        for attempt in range(1, args.captcha_attempts + 1):
            print(f"captcha_attempt={attempt}/{args.captcha_attempts}")
            sb.sleep(3)
            sb.solve_captcha()
            sb.sleep(args.settle_time)

            title = sb.get_title()
            current_url = sb.get_current_url()
            job_links = sb.select_all("a.jcs-JobTitle")
            body_text = (sb.get_text("body") or "").lower()
            blocked = any(
                marker in body_text
                for marker in (
                    "additional verification required",
                    "just a moment",
                    "enable javascript and cookies",
                    "cloudflare",
                )
            )

            print(f"title={title!r}")
            print(f"url={current_url!r}")
            print(f"job_links={len(job_links)}")
            print(f"blocked={blocked}")

            if job_links and not blocked:
                href = job_links[0].get_attribute("href") or ""
                print(f"first_job={urljoin('https://in.indeed.com', href)}")
                print("PASSED: reached Indeed results.")
                return 0

        print("FAILED: did not reach Indeed job results.")
        return 1
    finally:
        close_pure_cdp_browser(sb)


if __name__ == "__main__":
    raise SystemExit(main())
