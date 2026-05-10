"""Human-like pacing helpers.

Jittered sleep between requests mimics human browsing cadence.
Every fetcher calls human_sleep(site_config) between page loads.

Never use time.sleep(fixed_value) in fetchers — that pattern is
trivially detected by timing analysis.
"""

from __future__ import annotations

import random
import time


def human_sleep(min_seconds: float, max_seconds: float) -> None:
    """Sleep a random duration in [min_seconds, max_seconds]."""
    duration = random.uniform(min_seconds, max_seconds)
    time.sleep(duration)


def scroll_pause(base_seconds: float = 1.5) -> None:
    """Short jittered pause after a scroll action."""
    time.sleep(base_seconds + random.uniform(0, 0.8))
