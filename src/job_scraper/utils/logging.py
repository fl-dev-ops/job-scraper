"""Structured logging configuration."""

from __future__ import annotations

import logging

import structlog

_configured = False


def configure_logging(log_level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level.upper()),
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(site: str | None = None):
    if not _configured:
        configure_logging()
    log = structlog.get_logger()
    if site:
        log = log.bind(site=site)
    return log
