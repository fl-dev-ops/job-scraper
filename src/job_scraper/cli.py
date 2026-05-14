"""CLI entrypoint.

Usage:
    uv run python -m job_scraper linkedin --location bangalore --max 5
    uv run python -m job_scraper linkedin --query "junior react developer" --max 10
    uv run python -m job_scraper linkedin --location India --max 5 --parallel
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, Any

import typer
from dotenv import load_dotenv

from .extractor import JobExtractor, build_extractor
from .fetchers.base import load_site_config
from .normalizer import normalize_posting
from .schema import JobPosting, SiteName
from .storage import write
from .utils.logging import configure_logging, get_logger

load_dotenv()

app = typer.Typer(name="job-scraper", add_completion=False)

SUPPORTED_SITES: tuple[str, ...] = ("naukri", "indeed", "linkedin")
SearchFetcher = Callable[[str, str, int], list[tuple[str, str]]]
DetailFetcher = Callable[[str], list[tuple[str, str]]]


def _selector_list(value: object) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return None


async def _extract_and_write_one(
    extractor: JobExtractor,
    html: str,
    url: str,
    jd_selector: str,
    site: SiteName,
    title_selector: str | None,
    location_selector: str | None,
    company_selector: str | None,
    llm_context_selectors: list[str] | None,
    seen_ids: set[str],
    log: Any,
) -> JobPosting | None:
    """Extract one job and write it immediately if new."""
    posting = await extractor.extract_async(
        html,
        url,
        jd_selector,
        site,
        title_selector,
        location_selector,
        company_selector,
        llm_context_selectors,
    )
    if posting is None:
        return None
    if posting.job_id in seen_ids:
        return None
    seen_ids.add(posting.job_id)
    posting = normalize_posting(posting)
    if write(posting):
        log.info("wrote", job_id=posting.job_id, title=posting.title, company=posting.company)
        return posting
    log.info("skipped_existing", job_id=posting.job_id)
    return None


async def _extract_and_write(
    semaphore: asyncio.Semaphore,
    extractor: JobExtractor,
    html: str,
    url: str,
    jd_selector: str,
    site: SiteName,
    title_selector: str | None,
    location_selector: str | None,
    company_selector: str | None,
    llm_context_selectors: list[str] | None,
    seen_ids: set[str],
    log: Any,
) -> JobPosting | None:
    """Extract one job under the semaphore and write it immediately if new."""
    async with semaphore:
        return await _extract_and_write_one(
            extractor,
            html,
            url,
            jd_selector,
            site,
            title_selector,
            location_selector,
            company_selector,
            llm_context_selectors,
            seen_ids,
            log,
        )


async def _extract_pages_for_site(
    site: SiteName,
    pages: list[tuple[str, str]],
    query: str,
    extractor: JobExtractor,
    jd_selector: str,
    title_selector: str | None,
    location_selector: str | None,
    company_selector: str | None,
    llm_context_selectors: list[str] | None,
    parallelism: int,
    seen_ids: set[str],
) -> int:
    log = get_logger(site)
    semaphore = asyncio.Semaphore(parallelism)
    tasks = [
        _extract_and_write(
            semaphore,
            extractor,
            html,
            url,
            jd_selector,
            site,
            title_selector,
            location_selector,
            company_selector,
            llm_context_selectors,
            seen_ids,
            log,
        )
        for html, url in pages
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    written = 0
    for r in results:
        if isinstance(r, Exception):
            log.error("extraction_task_failed", query=query, error=str(r))
        elif r is not None:
            written += 1
    return written


def _run_for_site(
    site: SiteName,
    queries: list[str],
    location: str,
    max_jobs: int,
    url: str | None = None,
    parallel: bool = False,
) -> int:
    log = get_logger(site)
    site_config = load_site_config(site)
    jd_selector = site_config["selectors"]["jd_body"]
    title_selector: str | None = site_config["selectors"].get("title_selector")
    company_selector: str | None = site_config["selectors"].get("company_selector")
    location_selector: str | None = site_config["selectors"].get("location_selector")
    llm_context_selectors = _selector_list(site_config["selectors"].get("llm_context_selector"))
    parallelism = int(site_config.get("parallel_extractions", 5))

    fetcher: SearchFetcher
    detail_fetcher: DetailFetcher
    if site == "linkedin":
        from .fetchers.linkedin import fetch_linkedin, fetch_linkedin_url

        fetcher = fetch_linkedin
        detail_fetcher = fetch_linkedin_url
    elif site == "naukri":
        from .fetchers.naukri import fetch_naukri, fetch_naukri_url

        fetcher = fetch_naukri
        detail_fetcher = fetch_naukri_url
    elif site == "indeed":
        from .fetchers.indeed import _expand_company_queries, fetch_indeed, fetch_indeed_url

        fetcher = fetch_indeed
        detail_fetcher = fetch_indeed_url
        queries = _expand_company_queries(
            queries,
            [str(company) for company in site_config.get("company_names", [])],
        )
    else:
        log.error("fetcher_not_implemented", site=site)
        return 0

    extractor = build_extractor()
    written = 0
    seen_ids: set[str] = set()

    if url:
        pages = detail_fetcher(url)
        log.info("fetched", query="url", pages=len(pages))
        written = asyncio.run(
            _extract_pages_for_site(
                site=site,
                pages=pages,
                query="url",
                extractor=extractor,
                jd_selector=jd_selector,
                title_selector=title_selector,
                location_selector=location_selector,
                company_selector=company_selector,
                llm_context_selectors=llm_context_selectors,
                parallelism=parallelism,
                seen_ids=seen_ids,
            )
        )
        log.info("site_done", site=site, written=written)
        return written

    # ── LinkedIn + --parallel: concurrent detail scraping ────────────────
    if site == "linkedin" and parallel:
        from .fetchers.linkedin_parallel import run_linkedin_site

        async def _extract_and_write_linkedin(html: str, url: str) -> JobPosting | None:
            return await _extract_and_write_one(
                extractor,
                html,
                url,
                jd_selector,
                "linkedin",
                title_selector,
                location_selector,
                company_selector,
                llm_context_selectors,
                seen_ids,
                log,
            )

        written = run_linkedin_site(
            queries=queries,
            location=location,
            max_jobs=max_jobs,
            extract_and_write=_extract_and_write_linkedin,
            seen_ids=seen_ids,
        )
        log.info("site_done", site=site, written=written)
        return written

    # ── Default: sequential fetch per query, parallel LLM extraction ────
    for query in queries:
        log.info("query_start", query=query, max_per_query=max_jobs, parallelism=parallelism)
        try:
            pages = fetcher(query, location, max_jobs)
        except Exception as e:  # noqa: BLE001
            log.error("fetcher_crashed", query=query, error=str(e))
            continue

        log.info("fetched", query=query, pages=len(pages))
        written += asyncio.run(
            _extract_pages_for_site(
                site=site,
                pages=pages,
                query=query,
                extractor=extractor,
                jd_selector=jd_selector,
                title_selector=title_selector,
                location_selector=location_selector,
                company_selector=company_selector,
                llm_context_selectors=llm_context_selectors,
                parallelism=parallelism,
                seen_ids=seen_ids,
            )
        )

    log.info("site_done", site=site, written=written)
    return written


async def _run_sites(
    site_runs: list[tuple[SiteName, list[str]]],
    location: str,
    max_jobs: int,
    url: str | None,
    parallel: bool,
) -> int:
    log = get_logger()
    tasks = [
        asyncio.to_thread(_run_for_site, site, queries, location, max_jobs, url, parallel)
        for site, queries in site_runs
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    total = 0
    for result in results:
        if isinstance(result, Exception):
            log.error("site_task_failed", error=str(result))
        else:
            total += result
    return total


@app.command()
def scrape(
    site: Annotated[
        str,
        typer.Argument(help=f"Site: {' | '.join(SUPPORTED_SITES)} | all"),
    ],
    query: Annotated[
        str | None,
        typer.Option(help="Override default query list with a single query"),
    ] = None,
    url: Annotated[
        str | None,
        typer.Option(help="Scrape one detail URL directly."),
    ] = None,
    location: Annotated[str, typer.Option(help="Location filter")] = "India",
    max: Annotated[
        int,
        typer.Option(help="Max job postings to collect per query"),
    ] = 25,
    parallel: Annotated[
        bool,
        typer.Option(help="Enable parallel detail scraping (LinkedIn only)."),
    ] = False,
    log_level: Annotated[str, typer.Option(help="Log level")] = "INFO",
) -> None:
    """Scrape entry-level CS jobs and write per-job .md files."""
    configure_logging(log_level)
    log = get_logger()

    site_lc = site.lower()
    if url and site_lc == "all":
        log.error("url_requires_single_site")
        raise typer.Exit(code=2)
    if site_lc == "all":
        sites: list[SiteName] = list(SUPPORTED_SITES)  # type: ignore[arg-type]
    elif site_lc in SUPPORTED_SITES:
        sites = [site_lc]  # type: ignore[list-item]
    else:
        log.error("unknown_site", site=site)
        raise typer.Exit(code=2)

    site_runs: list[tuple[SiteName, list[str]]] = []
    for s in sites:
        cfg = load_site_config(s)
        queries = [query] if query else list(cfg.get("queries", []))
        if not url and not queries:
            log.error("no_queries", site=s)
            continue
        site_runs.append((s, queries))

    if len(site_runs) > 1:
        total = asyncio.run(_run_sites(site_runs, location, max, url, parallel))
    elif site_runs:
        site_name, site_queries = site_runs[0]
        total = _run_for_site(site_name, site_queries, location, max, url, parallel)
    else:
        total = 0

    log.info("done", total_written=total)


if __name__ == "__main__":
    app()
