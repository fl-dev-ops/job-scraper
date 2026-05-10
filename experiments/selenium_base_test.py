"""Standalone SeleniumBase Cloudflare/Indeed validation script.

Run after installing project dependencies:
    uv run python selenium_base_test.py

If local Chrome startup fails, try Chrome for Testing:
    uv run sbase get cft
    uv run python selenium_base_test.py --binary-location cft
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_URL = (
    "https://in.indeed.com/jobs"
    "?q=junior+software+engineer"
    "&l=bangalore"
    "&explvl=entry_level"
    "&fromage=30"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Try SeleniumBase UC mode against an Indeed Cloudflare challenge."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="URL to open")
    parser.add_argument(
        "--mode",
        choices=("cdp", "uc"),
        default="cdp",
        help="SeleniumBase anti-detection mode to test",
    )
    parser.add_argument(
        "--reconnect-time",
        type=float,
        default=4,
        help="Seconds SeleniumBase disconnects WebDriver in UC mode",
    )
    parser.add_argument(
        "--settle-time",
        type=float,
        default=8,
        help="Seconds to wait after attempting the captcha click",
    )
    parser.add_argument(
        "--captcha-attempts",
        type=int,
        default=2,
        help="Number of SeleniumBase captcha click attempts",
    )
    parser.add_argument(
        "--incognito",
        action="store_true",
        help="Run the browser in incognito mode",
    )
    parser.add_argument(
        "--binary-location",
        default=None,
        help='Browser binary path, or "cft" for Chrome for Testing after running `sbase get cft`',
    )
    parser.add_argument(
        "--driver-version",
        default=None,
        help="Optional ChromeDriver/UC driver major version override",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        from seleniumbase import SB
    except ImportError:
        print(
            "SeleniumBase is not installed. Run:\n"
            "  uv sync",
            file=sys.stderr,
        )
        return 2

    print(
        "Starting SeleniumBase "
        f"mode={args.mode!r} incognito={args.incognito} "
        f"binary_location={args.binary_location!r}",
        flush=True,
    )

    with SB(
        uc=True,
        test=True,
        locale="en",
        incognito=args.incognito,
        binary_location=args.binary_location,
        driver_version=args.driver_version,
    ) as sb:
        print(f"Opening: {args.url}")
        if args.mode == "cdp":
            sb.activate_cdp_mode(args.url)
        else:
            sb.uc_open_with_reconnect(args.url, reconnect_time=args.reconnect_time)

        for attempt in range(1, args.captcha_attempts + 1):
            print(f"Captcha attempt {attempt}/{args.captcha_attempts}")
            if args.mode == "cdp":
                sb.solve_captcha()
            else:
                sb.uc_gui_click_captcha(retry=True)
            sb.sleep(args.settle_time)

            title = sb.get_title()
            current_url = sb.get_current_url()
            print(f"title={title!r}")
            print(f"url={current_url!r}")

            page_text = (sb.get_text("body") or "").lower()
            still_blocked = any(
                marker in page_text
                for marker in (
                    "additional verification required",
                    "just a moment",
                    "enable javascript and cookies",
                    "cloudflare",
                )
            )
            has_jobs = bool(sb.find_elements("a.jcs-JobTitle"))
            print(f"still_blocked={still_blocked}")
            print(f"job_links={len(sb.find_elements('a.jcs-JobTitle'))}")

            if has_jobs and not still_blocked:
                print("PASSED: reached Indeed results.")
                return 0

        print("FAILED: did not reach Indeed job results.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
