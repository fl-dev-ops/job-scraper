"""Resolve LinkedIn company IDs from configured pending company names.

The resolver uses public LinkedIn job result pages:
1. Search jobs with the company name as keyword.
2. Find result cards whose displayed company name approximately matches.
3. Open a matching full job URL and read the embedded companyId meta tag.

Companies with no matching LinkedIn job result are ignored.

Usage:
    uv run python scripts/resolve_linkedin_company_ids.py
    uv run python scripts/resolve_linkedin_company_ids.py --update
"""

from __future__ import annotations

import argparse
import html
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import httpx
import yaml
from selectolax.parser import HTMLParser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LINKEDIN_CONFIG = PROJECT_ROOT / "config" / "linkedin.yaml"
SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?keywords={query}&start=0"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class ResolvedCompany:
    company_id: str
    configured_name: str
    matched_name: str
    company_url: str
    job_url: str


def _norm(value: str) -> str:
    value = html.unescape(value).lower()
    value = value.replace("&", "and")
    value = re.sub(r"\b(ppo|ctdp|aeh|hackathon)\b", " ", value)
    value = re.sub(r"\b(pvt|private|limited|ltd|inc|llc|india)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _is_match(configured_name: str, matched_name: str) -> bool:
    configured = _norm(configured_name)
    matched = _norm(matched_name)
    if not configured or not matched:
        return False
    if configured == matched:
        return True

    parenthetical = re.search(r"\(([^)]+)\)", configured_name)
    if parenthetical and _norm(parenthetical.group(1)) == matched:
        return True

    # Allow common renamed/compound pages, but avoid broad subsidiary matches
    # such as "Airtel" -> "Airtel Payments Bank".
    shorter, longer = sorted((configured, matched), key=len)
    return len(shorter) >= 8 and shorter in longer and len(shorter) / len(longer) >= 0.55


def _text(node: object) -> str:
    if node is None:
        return ""
    return " ".join(getattr(node, "text")().split())


def _extract_cards(markup: str) -> list[tuple[str, str, str]]:
    parser = HTMLParser(markup)
    cards: list[tuple[str, str, str]] = []
    for card in parser.css("li"):
        job_link = card.css_first("a.base-card__full-link")
        company_link = card.css_first("h4 a")
        if job_link is None or company_link is None:
            continue
        job_href = job_link.attributes.get("href") or ""
        company_href = company_link.attributes.get("href") or ""
        company_name = _text(company_link)
        if not job_href or not company_href or not company_name:
            continue
        cards.append((company_name, company_href, job_href))
    return cards


def _extract_company_id(markup: str) -> str | None:
    patterns = [
        r'companyId"\s+content="(\d+)"',
        r"companyId&quot;\s+content=&quot;(\d+)&quot;",
        r"urn:li:company:(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, markup)
        if match:
            return match.group(1)
    return None


def resolve_one(client: httpx.Client, company_name: str) -> ResolvedCompany | None:
    search_url = SEARCH_URL.format(query=quote_plus(company_name))
    response = client.get(search_url)
    response.raise_for_status()

    for matched_name, company_url, job_url in _extract_cards(response.text):
        if not _is_match(company_name, matched_name):
            continue

        full_job_url = urljoin("https://www.linkedin.com", html.unescape(job_url).split("?")[0])
        detail = client.get(full_job_url)
        detail.raise_for_status()
        company_id = _extract_company_id(detail.text)
        if not company_id:
            continue
        return ResolvedCompany(
            company_id=company_id,
            configured_name=company_name,
            matched_name=matched_name,
            company_url=html.unescape(company_url).split("?")[0],
            job_url=full_job_url,
        )

    return None


def load_config() -> dict[str, object]:
    return yaml.safe_load(LINKEDIN_CONFIG.read_text(encoding="utf-8"))


def existing_company_ids(config_text: str) -> set[str]:
    return set(re.findall(r'^\s*-\s*["\']?(\d+)["\']?', config_text, flags=re.M))


def update_config(resolved: list[ResolvedCompany]) -> None:
    config_text = LINKEDIN_CONFIG.read_text(encoding="utf-8")
    existing_ids = existing_company_ids(config_text)
    new_lines = [
        f'  - "{item.company_id}" # {item.matched_name}'
        for item in resolved
        if item.company_id not in existing_ids
    ]
    if not new_lines:
        return

    marker = "\npending_company_names:"
    if marker not in config_text:
        raise ValueError("Could not find pending_company_names marker")

    insertion = "\n".join(new_lines) + "\n"
    config_text = config_text.replace(marker, "\n" + insertion + marker, 1)
    LINKEDIN_CONFIG.write_text(config_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.8)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    pending_names = [str(name) for name in config.get("pending_company_names", [])]
    if args.limit is not None:
        pending_names = pending_names[: args.limit]

    resolved: list[ResolvedCompany] = []
    unresolved: list[str] = []

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20) as client:
        for index, company_name in enumerate(pending_names, 1):
            try:
                item = resolve_one(client, company_name)
            except Exception as exc:  # noqa: BLE001 - keep resolving the rest
                unresolved.append(f"{company_name} ({exc})")
                print(f"[{index}/{len(pending_names)}] unresolved: {company_name} ({exc})")
                time.sleep(args.delay)
                continue

            if item is None:
                unresolved.append(company_name)
                print(f"[{index}/{len(pending_names)}] unresolved: {company_name}")
            else:
                resolved.append(item)
                print(
                    f"[{index}/{len(pending_names)}] {item.company_id}: "
                    f"{item.configured_name} -> {item.matched_name}"
                )
            time.sleep(args.delay)

    if args.update:
        update_config(resolved)

    print("\nresolved:")
    for item in resolved:
        print(f'  - "{item.company_id}" # {item.matched_name}')

    print(f"\nsummary: resolved={len(resolved)} unresolved={len(unresolved)}")


if __name__ == "__main__":
    main()
