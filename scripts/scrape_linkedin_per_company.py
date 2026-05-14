"""Scrape LinkedIn jobs one company ID at a time.

Usage:
    uv run python scripts/scrape_linkedin_per_company.py --location India --max 5
    uv run python scripts/scrape_linkedin_per_company.py --limit-companies 1
"""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from job_scraper.extractor import JobExtractor, build_extractor
from job_scraper.fetchers.base import load_site_config
from job_scraper.fetchers.browser_context import browser_session
from job_scraper.fetchers.linkedin import fetch_linkedin
from job_scraper.normalizer import compute_job_id, normalize_posting
from job_scraper.schema import JobPosting
from job_scraper.storage import get_output_path, write
from job_scraper.utils.logging import configure_logging, get_logger
from job_scraper.utils.seleniumbase_compat import close_pure_cdp_browser, open_pure_cdp_browser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LINKEDIN_CONFIG = PROJECT_ROOT / "config" / "linkedin.yaml"
COMPANY_ID_PATTERN = re.compile(r'^\s*-\s*["\']?(\d+)["\']?\s*(?:#\s*(.+?))?\s*$')


@dataclass(frozen=True)
class LinkedInCompany:
    company_id: str
    name: str


def load_linkedin_companies(path: Path = LINKEDIN_CONFIG) -> list[LinkedInCompany]:
    """Load company IDs and their inline YAML comments for progress logging."""
    companies: list[LinkedInCompany] = []
    in_company_ids = False

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "company_ids:":
            in_company_ids = True
            continue
        if not in_company_ids:
            continue
        if stripped and not line.startswith((" ", "\t", "-")):
            break
        if not stripped or stripped.startswith("#"):
            continue

        match = COMPANY_ID_PATTERN.match(line)
        if match:
            company_id, comment = match.groups()
            companies.append(
                LinkedInCompany(company_id=company_id, name=(comment or company_id).strip())
            )

    if not companies:
        raise ValueError(f"No company_ids found in {path}")
    return companies


def _add_source_query_metadata(posting: JobPosting, query: str) -> None:
    path = get_output_path(posting.site, posting.job_id)
    if not path.exists():
        return

    content = path.read_text(encoding="utf-8")
    if "\nsource_query:" in content:
        return

    marker = "\nsource:"
    insert = f"\nsource_query: {query!r}"
    if marker in content:
        content = content.replace(marker, f"{insert}{marker}", 1)
    else:
        content = content.replace("---\n", f"---\nsource_query: {query!r}\n", 1)
    path.write_text(content, encoding="utf-8")


async def extract_and_write(
    semaphore: asyncio.Semaphore,
    extractor: JobExtractor,
    html: str,
    url: str,
    site_config: dict[str, Any],
    seen_ids: set[str],
    log: Any,
    query: str,
) -> JobPosting | None:
    selectors = site_config["selectors"]
    async with semaphore:
        posting = await extractor.extract_async(
            html,
            url,
            selectors["jd_body"],
            "linkedin",
            selectors.get("title_selector"),
            selectors.get("location_selector"),
            selectors.get("company_selector"),
        )
        if posting is None:
            return None
        if posting.job_id in seen_ids:
            log.info("skipped_seen_in_run", job_id=posting.job_id, source=posting.source)
            return None

        seen_ids.add(posting.job_id)
        posting = normalize_posting(posting)
        if write(posting):
            _add_source_query_metadata(posting, query)
            log.info("wrote", job_id=posting.job_id, title=posting.title, company=posting.company)
            return posting

        log.info("skipped_existing", job_id=posting.job_id, source=posting.source)
        return None


async def scrape_company_query(
    company: LinkedInCompany,
    query: str,
    location: str,
    max_jobs: int,
    scrolls_per_page: int,
    site_config: dict[str, Any],
    extractor: JobExtractor,
    semaphore: asyncio.Semaphore,
    seen_ids: set[str],
) -> int:
    log = get_logger("linkedin_per_company")
    log.info(
        "company_query_start",
        company=company.name,
        company_id=company.company_id,
        query=query,
        max_jobs=max_jobs,
    )

    try:
        pages = await asyncio.to_thread(
            fetch_linkedin,
            query,
            location,
            max_jobs,
            company_ids_override=[company.company_id],
            scrolls_per_listing_page_override=scrolls_per_page,
        )
    except BaseException as exc:  # noqa: BLE001 - continue the long run after one failure
        log.error(
            "fetcher_crashed",
            company=company.name,
            company_id=company.company_id,
            query=query,
            error=str(exc),
        )
        return 0

    log.info(
        "company_query_fetched",
        company=company.name,
        company_id=company.company_id,
        query=query,
        pages=len(pages),
    )

    pages_to_extract: list[tuple[str, str]] = []
    for html, url in pages:
        job_id = compute_job_id(url)
        if job_id in seen_ids:
            log.info("skipped_seen_before_extract", job_id=job_id, source=url)
            continue
        if get_output_path("linkedin", job_id).exists():
            seen_ids.add(job_id)
            log.info("skipped_existing_before_extract", job_id=job_id, source=url)
            continue
        pages_to_extract.append((html, url))

    tasks = [
        extract_and_write(semaphore, extractor, html, url, site_config, seen_ids, log, query)
        for html, url in pages_to_extract
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    written = 0
    for result in results:
        if isinstance(result, Exception):
            log.error(
                "extraction_task_failed",
                company=company.name,
                company_id=company.company_id,
                query=query,
                error=str(result),
            )
        elif result is not None:
            written += 1

    log.info(
        "company_query_done",
        company=company.name,
        company_id=company.company_id,
        query=query,
        written=written,
    )
    return written


async def _run_scrape_loop(
    companies: list[LinkedInCompany],
    queries: list[str],
    location: str,
    max_jobs: int,
    scrolls_per_page: int,
    site_config: dict[str, Any],
    extractor: JobExtractor,
    semaphore: asyncio.Semaphore,
    seen_ids: set[str],
    log: Any,
) -> int:
    """Run the scraping loop inside an active browser_session."""
    total_written = 0
    for company in companies:
        company_written = 0
        for current_query in queries:
            company_written += await scrape_company_query(
                company,
                current_query,
                location,
                max_jobs,
                scrolls_per_page,
                site_config,
                extractor,
                semaphore,
                seen_ids,
            )
        total_written += company_written
        log.info(
            "company_done",
            company=company.name,
            company_id=company.company_id,
            written=company_written,
        )
    return total_written


def scrape_all(
    *,
    location: str,
    max_jobs: int,
    scrolls_per_page: int,
    start_company: int,
    limit_companies: int | None,
    limit_queries: int | None,
) -> int:
    site_config = load_site_config("linkedin")
    queries = [str(query) for query in site_config.get("queries", [])]
    if limit_queries is not None:
        queries = queries[:limit_queries]
    if not queries:
        raise ValueError("No LinkedIn queries configured")

    companies = load_linkedin_companies()
    if start_company < 1:
        raise ValueError("--start-company must be a 1-based index")
    companies = companies[start_company - 1 :]
    if limit_companies is not None:
        companies = companies[:limit_companies]

    parallelism = int(site_config.get("parallel_extractions", 5))
    extractor = build_extractor()
    log = get_logger("linkedin_per_company")

    sb = open_pure_cdp_browser("linkedin", site_config)
    try:
        with browser_session(sb, site_config):
            seen_ids: set[str] = set()
            written = asyncio.run(
                _run_scrape_loop(
                    companies,
                    queries,
                    location,
                    max_jobs,
                    scrolls_per_page,
                    site_config,
                    extractor,
                    asyncio.Semaphore(parallelism),
                    seen_ids,
                    log,
                )
            )
    finally:
        close_pure_cdp_browser(sb)

    log.info(
        "per_company_scrape_done",
        companies=len(companies),
        queries=len(queries),
        total_written=written,
    )
    return written


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Scrape LinkedIn jobs per configured company ID.")
    parser.add_argument("--location", default="India")
    parser.add_argument("--max", type=int, default=5, help="Max jobs per query per company")
    parser.add_argument(
        "--scrolls-per-page",
        type=int,
        default=1,
        help="Listing scroll attempts per company/query page",
    )
    parser.add_argument(
        "--start-company",
        type=int,
        default=1,
        help="1-based company index to start from",
    )
    parser.add_argument("--limit-companies", type=int, default=None)
    parser.add_argument("--limit-queries", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(args.log_level)
    scrape_all(
        location=args.location,
        max_jobs=args.max,
        scrolls_per_page=args.scrolls_per_page,
        start_company=args.start_company,
        limit_companies=args.limit_companies,
        limit_queries=args.limit_queries,
    )


if __name__ == "__main__":
    main()
