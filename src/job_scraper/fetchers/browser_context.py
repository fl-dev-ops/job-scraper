"""Shared browser context — set once, access from anywhere.

Usage in orchestrator (cli.py, scripts):
    sb = open_pure_cdp_browser(site, config)
    try:
        with browser_session(sb, config):
            # All fetcher calls inside this block share the same browser
            ...
    finally:
        close_pure_cdp_browser(sb)

Usage in fetchers:
    sb, config = get_browser()   # raises if no session is active
    # or
    if has_browser():
        sb, config = get_browser()
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Generator

_Pair = tuple[Any, dict[str, Any]] | None
_current: ContextVar[_Pair] = ContextVar("browser_context", default=None)


def set_browser(sb: Any, config: dict[str, Any]) -> None:
    _current.set((sb, config))


def get_browser() -> tuple[Any, dict[str, Any]]:
    pair = _current.get()
    if pair is None:
        raise RuntimeError("No browser session active. Wrap calls in browser_session().")
    return pair


def has_browser() -> bool:
    return _current.get() is not None


def clear_browser() -> None:
    _current.set(None)


@contextmanager
def browser_session(sb: Any, config: dict[str, Any]) -> Generator[None, None, None]:
    """Context manager that makes (sb, config) available via get_browser()."""
    token: Token[_Pair] = _current.set((sb, config))
    try:
        yield
    finally:
        _current.reset(token)
