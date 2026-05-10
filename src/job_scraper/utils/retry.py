"""Retry / backoff helpers built on tenacity."""

from __future__ import annotations

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

SITE_RETRY_CONFIG: dict[str, dict] = {
    "naukri":   {"attempts": 3, "base_wait": 2,  "max_wait": 10},
    "indeed":   {"attempts": 3, "base_wait": 5,  "max_wait": 30},
    "linkedin": {"attempts": 2, "base_wait": 10, "max_wait": 60},
}


def with_retry(site: str):
    cfg = SITE_RETRY_CONFIG.get(site, SITE_RETRY_CONFIG["naukri"])
    return retry(
        stop=stop_after_attempt(cfg["attempts"]),
        wait=wait_exponential(
            multiplier=cfg["base_wait"], max=cfg["max_wait"]
        ),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        reraise=True,
    )
