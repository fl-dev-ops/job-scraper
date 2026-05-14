from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

from scripts import scrape_naukri_search

from job_scraper.fetchers.naukri import _build_search_url

TEMPLATE = "https://www.naukri.com/{query}-jobs?experience=0&experienceMax=2&freshness=1"


def test_build_search_url_matches_naukri_keyword_location_route() -> None:
    url = _build_search_url(TEMPLATE, "entry level", "chennai")

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.path == "/entry-level-jobs-in-chennai"
    assert "k=entry%20level" in parsed.query
    assert params["k"] == ["entry level"]
    assert params["l"] == ["chennai"]
    assert params["experience"] == ["0"]
    assert params["experienceMax"] == ["2"]
    assert params["freshness"] == ["1"]


def test_build_search_url_does_not_duplicate_trailing_jobs_token() -> None:
    url = _build_search_url(TEMPLATE, "entry level jobs", "India")

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.path == "/entry-level-jobs"
    assert params["k"] == ["entry level jobs"]
    assert "l" not in params


def test_build_search_url_appends_company_name_to_keyword() -> None:
    url = _build_search_url(TEMPLATE, "entry level", "chennai", company_name="Amazon")

    parsed = urlparse(url)
    params = parse_qs(urlparse(url).query)

    assert parsed.path == "/entry-level-at-amazon-jobs-in-chennai"
    assert params["k"] == ["entry level at Amazon"]


def test_queries_from_args_prefers_single_query() -> None:
    assert scrape_naukri_search._queries_from_args("entry level", None) == ["entry level"]


def test_company_names_from_config_reads_company_names() -> None:
    companies = scrape_naukri_search._company_names_from_config(
        {"company_names": ["Amazon", " Google ", ""]}
    )

    assert companies == ["Amazon", "Google"]


def test_scrape_company_query_passes_company_name_to_fetcher(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_fetch_naukri(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            observed["inside_active_loop"] = False
        else:
            observed["inside_active_loop"] = True
        observed["args"] = args
        observed["kwargs"] = kwargs
        return []

    monkeypatch.setattr(scrape_naukri_search, "fetch_naukri", fake_fetch_naukri)

    written = asyncio.run(
        scrape_naukri_search.scrape_company_query(
            company_name="Amazon",
            query="entry level",
            location="chennai",
            max_jobs=1,
            site_config={"selectors": {"jd_body": "body"}},
            extractor=object(),
            semaphore=asyncio.Semaphore(1),
            seen_ids=set(),
        )
    )

    assert written == 0
    assert observed["inside_active_loop"] is False
    assert observed["args"] == ("entry level", "chennai", 1)
    assert observed["kwargs"] == {"company_name": "Amazon"}
