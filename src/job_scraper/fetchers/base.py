"""Shared utilities for site fetchers.

Selectolax is used ONLY to:
  1. Locate navigation elements
  2. Strip HTML tags from the JD container -> plain text for the LLM

It does NOT extract structured field values. All field extraction goes through
the LLM in extractor.py to avoid silently wrong data from fragile CSS selectors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from selectolax.parser import HTMLParser

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"


def load_site_config(site: str) -> dict[str, Any]:
    path = CONFIG_DIR / f"{site}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Site config not found: {path}")
    with path.open() as f:
        return yaml.safe_load(f)


def extract_html_field(html: str, selector: str, separator: str = "") -> str | None:
    """Extract text from the first element matching selector. Returns None if not found."""
    parser = HTMLParser(html)
    for sel in (s.strip() for s in selector.split(",")):
        node = parser.css_first(sel)
        if node:
            text = node.text(separator=separator, strip=True)
            if text:
                return text
    return None


def extract_jd_text(html: str, jd_body_selector: str) -> str:
    """Select the JD container, strip tags, return plain text.

    Falls back to body if selector misses — LLM still finds the content.
    """
    parser = HTMLParser(html)
    node = parser.css_first(jd_body_selector)
    if node is None:
        node = parser.css_first("body")
    if node is None:
        return parser.text(separator="\n", strip=True) or ""
    return node.text(separator="\n", strip=True) or ""
