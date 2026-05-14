"""Scrape Naukri jobs one company name at a time.

Usage:
    uv run python scripts/scrape_naukri_search.py --location India --max 1
    uv run python scripts/scrape_naukri_search.py --limit-companies 1 --limit-queries 1
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from job_scraper.extractor import JobExtractor, build_extractor
from job_scraper.fetchers.base import load_site_config
from job_scraper.fetchers.naukri import fetch_naukri
from job_scraper.normalizer import compute_job_id, normalize_posting
from job_scraper.schema import JobPosting
from job_scraper.storage import get_output_path, write
from job_scraper.utils.logging import configure_logging, get_logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _company_names_from_config(site_config: dict[str, Any]) -> list[str]:
    companies = [str(item).strip() for item in site_config.get("company_names", [])]
    companies = [company for company in companies if company]
    if not companies:
        raise ValueError("No Naukri company_names configured")
    return companies


def _queries_from_args(query: str | None, limit_queries: int | None) -> list[str]:
    if query:
        return [query]

    site_config = load_site_config("naukri")
    queries = [str(item) for item in site_config.get("queries", [])]
    if limit_queries is not None:
        queries = queries[:limit_queries]
    if not queries:
        raise ValueError("No Naukri queries configured")
    return queries


async def extract_and_write(
    semaphore: asyncio.Semaphore,
    extractor: JobExtractor,
    html: str,
    url: str,
    site_config: dict[str, Any],
    seen_ids: set[str],
    log: Any,
) -> JobPosting | None:
    selectors = site_config["selectors"]
    async with semaphore:
        posting = await extractor.extract_async(
            html,
            url,
            selectors["jd_body"],
            "naukri",
            selectors.get("title_selector"),
            selectors.get("location_selector"),
            selectors.get("company_selector"),
            selectors.get("llm_context_selector"),
        )
        if posting is None:
            return None
        if posting.job_id in seen_ids:
            log.info("skipped_seen_in_run", job_id=posting.job_id, source=posting.source)
            return None

        seen_ids.add(posting.job_id)
        posting = normalize_posting(posting)
        if write(posting):
            log.info("wrote", job_id=posting.job_id, title=posting.title, company=posting.company)
            return posting

        log.info("skipped_existing", job_id=posting.job_id, source=posting.source)
        return None


async def scrape_company_query(
    company_name: str,
    query: str,
    location: str,
    max_jobs: int,
    site_config: dict[str, Any],
    extractor: JobExtractor,
    semaphore: asyncio.Semaphore,
    seen_ids: set[str],
) -> int:
    log = get_logger("naukri_per_company")
    log.info(
        "company_query_start",
        company=company_name,
        query=query,
        max_jobs=max_jobs,
    )

    try:
        pages = await asyncio.to_thread(
            fetch_naukri,
            query,
            location,
            max_jobs,
            company_name=company_name,
        )
    except BaseException as exc:  # noqa: BLE001 - continue the long run after one failure
        log.error(
            "fetcher_crashed",
            company=company_name,
            query=query,
            error=str(exc),
        )
        return 0

    pages_to_extract: list[tuple[str, str]] = []
    for html, url in pages:
        job_id = compute_job_id(url)
        if job_id in seen_ids:
            log.info("skipped_seen_before_extract", job_id=job_id, source=url)
            continue
        if get_output_path("naukri", job_id).exists():
            seen_ids.add(job_id)
            log.info("skipped_existing_before_extract", job_id=job_id, source=url)
            continue
        pages_to_extract.append((html, url))

    tasks = [
        extract_and_write(semaphore, extractor, html, url, site_config, seen_ids, log)
        for html, url in pages_to_extract
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    written = 0
    for result in results:
        if isinstance(result, Exception):
            log.error(
                "extraction_task_failed",
                company=company_name,
                query=query,
                error=str(result),
            )
        elif result is not None:
            written += 1

    log.info(
        "company_query_done",
        company=company_name,
        query=query,
        written=written,
    )
    return written


async def scrape_all(
    *,
    query: str | None,
    location: str,
    max_jobs: int,
    start_company: int,
    limit_companies: int | None,
    limit_queries: int | None,
) -> int:
    site_config = load_site_config("naukri")
    queries = _queries_from_args(query, limit_queries)

    companies = _company_names_from_config(site_config)
    if start_company < 1:
        raise ValueError("--start-company must be a 1-based index")
    companies = companies[start_company - 1 :]
    if limit_companies is not None:
        companies = companies[:limit_companies]

    parallelism = int(site_config.get("parallel_extractions", 5))
    extractor = build_extractor()
    semaphore = asyncio.Semaphore(parallelism)
    seen_ids: set[str] = set()
    log = get_logger("naukri_per_company")

    total_written = 0
    for company in companies:
        company_written = 0
        for current_query in queries:
            company_written += await scrape_company_query(
                company,
                current_query,
                location,
                max_jobs,
                site_config,
                extractor,
                semaphore,
                seen_ids,
            )
        total_written += company_written
        log.info(
            "company_done",
            company=company,
            written=company_written,
        )

    log.info(
        "per_company_scrape_done",
        companies=len(companies),
        queries=len(queries),
        total_written=total_written,
    )
    return total_written


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description="Scrape Naukri jobs per configured company name.")
    parser.add_argument(
        "--query",
        default=None,
        help="Single keyword query to run for every company",
    )
    parser.add_argument("--location", default="India")
    parser.add_argument("--max", type=int, default=1, help="Max jobs per query per company")
    parser.add_argument(
        "--start-company",
        type=int,
        default=1,
        help="1-based company index to start from",
    )
    parser.add_argument("--limit-companies", type=int, default=None)
    parser.add_argument(
        "--limit-queries",
        type=int,
        default=None,
        help="Limit configured queries when --query is not passed",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    configure_logging(args.log_level)
    asyncio.run(
        scrape_all(
            query=args.query,
            location=args.location,
            max_jobs=args.max,
            start_company=args.start_company,
            limit_companies=args.limit_companies,
            limit_queries=args.limit_queries,
        )
    )


if __name__ == "__main__":
    main()
