"""
SeleniumBase Chrome startup diagnostic script.

Purpose:
    Checks whether SeleniumBase can start Chrome at all before testing Indeed.

Run:
    uv run --with seleniumbase python sb_chrome_diagnostic.py

Optional:
    uv run --with seleniumbase python sb_chrome_diagnostic.py \
      --binary-location "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

If this script fails, the issue is Chrome/SeleniumBase/driver startup,
not your Indeed automation code.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


MAC_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    "/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--binary-location",
        default=None,
        help="Full path to Chrome executable. Do not pass just 'cft'.",
    )
    parser.add_argument(
        "--driver-version",
        default=None,
        help="Optional ChromeDriver major version, for example 147.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Try headless mode.",
    )
    parser.add_argument(
        "--url",
        default="https://example.com",
        help="Safe startup test URL.",
    )
    return parser.parse_args()


def run_cmd(cmd: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
        return completed.returncode, completed.stdout.strip()
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def find_chrome_binary(explicit_path: str | None) -> str | None:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.exists():
            return str(path)
        raise FileNotFoundError(f"Chrome binary path does not exist: {path}")

    for path_str in MAC_CHROME_PATHS:
        path = Path(path_str).expanduser()
        if path.exists():
            return str(path)

    for executable_name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        found = shutil.which(executable_name)
        if found:
            return found

    return None


def print_environment(chrome_binary: str | None) -> None:
    print("=== Environment ===")
    print(f"sys.executable: {sys.executable}")
    print(f"python: {sys.version}")
    print(f"platform: {platform.platform()}")
    print(f"machine: {platform.machine()}")
    print(f"cwd: {Path.cwd()}")
    print(f"chrome_binary: {chrome_binary!r}")

    if chrome_binary:
        rc, out = run_cmd([chrome_binary, "--version"])
        print(f"chrome --version rc={rc}: {out}")

    print()


def cleanup_stale_chrome_processes() -> None:
    """
    Kills Chrome/ChromeDriver processes that may be holding remote-debugging ports
    or locked profiles. This is intentionally macOS-focused because your traceback
    is from macOS.
    """
    if sys.platform != "darwin":
        print("Skipping macOS Chrome cleanup because this is not macOS.")
        return

    print("=== Cleaning stale Chrome/Selenium processes ===")

    # commands = [
    #     ["pkill", "-f", "Google Chrome"],
    #     ["pkill", "-f", "Google Chrome for Testing"],
    #     ["pkill", "-f", "chromedriver"],
    #     ["pkill", "-f", "uc_driver"],
    # ]

    # for cmd in commands:
    #     rc, out = run_cmd(cmd)
    #     print(f"{' '.join(cmd)} -> rc={rc}")

    print()


def seleniumbase_startup_test(
    chrome_binary: str | None,
    driver_version: str | None,
    headless: bool,
    url: str,
) -> int:
    print("=== SeleniumBase startup test ===")

    try:
        from seleniumbase import SB
    except ImportError:
        print("SeleniumBase import failed.")
        print("Run: uv run --with seleniumbase python sb_chrome_diagnostic.py")
        return 2

    print("Imported SeleniumBase successfully.")

    sb_kwargs = {
        "uc": True,
        "test": True,
        "locale": "en",
        "headless": headless,
        "driver_version": driver_version,
    }

    if chrome_binary:
        sb_kwargs["binary_location"] = chrome_binary

    print("SB kwargs:")
    safe_kwargs = dict(sb_kwargs)
    print(safe_kwargs)

    try:
        with SB(**sb_kwargs) as sb:
            print(f"Opening safe URL: {url}")
            sb.open(url)
            sb.sleep(2)
            print(f"title={sb.get_title()!r}")
            print(f"url={sb.get_current_url()!r}")

            body_text = sb.get_text("body") or ""
            print(f"body starts with={body_text[:120]!r}")

            print("PASSED: SeleniumBase started Chrome and loaded the page.")
            return 0

    except Exception as exc:
        print()
        print("FAILED: SeleniumBase could not start or control Chrome.")
        print(f"{type(exc).__name__}: {exc}")
        print()
        print("Most likely causes:")
        print("1. Chrome binary not found or not executable.")
        print("2. Chrome/ChromeDriver/uc_driver version mismatch.")
        print("3. Stale Chrome or uc_driver process is blocking the debug port.")
        print("4. Existing Chrome profile lock or corrupted SeleniumBase driver cache.")
        print("5. macOS security/quarantine issue on downloaded Chrome/driver.")
        return 1


def main() -> int:
    args = parse_args()

    chrome_binary = find_chrome_binary(args.binary_location)

    print_environment(chrome_binary)

    if not chrome_binary:
        print("No Chrome binary found.")
        print("Install Google Chrome, or pass the full path:")
        print(
            'uv run --with seleniumbase python sb_chrome_diagnostic.py '
            '--binary-location "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"'
        )
        return 1

    cleanup_stale_chrome_processes()

    return seleniumbase_startup_test(
        chrome_binary=chrome_binary,
        driver_version=args.driver_version,
        headless=args.headless,
        url=args.url,
    )


if __name__ == "__main__":
    raise SystemExit(main())