"""LinkedIn listing-page company filter tests."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

from job_scraper.fetchers.linkedin import (
    _build_paginated_search_url,
    _build_search_url,
    _collect_listing_urls_from_html,
)

SELECTORS = {
    "job_cards": "ul.jobs-search__results-list > li",
    "job_link": "a.base-card__full-link",
}


def test_collect_listing_urls_keeps_all_native_company_filter_results() -> None:
    html = """
    <ul class="jobs-search__results-list">
      <li>
        <a class="base-card__full-link" href="/jobs/view/1?ref=search"></a>
        <h4 class="base-search-card__subtitle">Amazon</h4>
      </li>
      <li>
        <a class="base-card__full-link" href="/jobs/view/2?ref=search"></a>
        <h4 class="base-search-card__subtitle">Google</h4>
      </li>
    </ul>
    """

    urls = _collect_listing_urls_from_html(
        html=html,
        selectors=SELECTORS,
        base_url="https://www.linkedin.com",
        seen=set(),
        max_jobs=10,
    )

    assert urls == [
        "https://www.linkedin.com/jobs/view/1",
        "https://www.linkedin.com/jobs/view/2",
    ]


def test_build_search_url_adds_linkedin_company_ids_to_native_filter() -> None:
    url = _build_search_url(
        config={
            "search_url": (
                "https://www.linkedin.com/jobs/search?"
                "keywords={query}&location={location}&f_E=2&f_TPR=r2592000"
            ),
            "company_ids": ["1586", 1441],
        },
        query="entry level engineer",
        location="India",
    )

    assert url == (
        "https://www.linkedin.com/jobs/search?"
        "keywords=entry+level+engineer&location=India&f_E=2&f_TPR=r2592000&f_C=1586%2C1441"
    )


def test_build_search_url_can_override_company_ids_for_per_company_runs() -> None:
    url = _build_search_url(
        config={
            "search_url": (
                "https://www.linkedin.com/jobs/search?"
                "keywords={query}&location={location}&f_TPR=r2592000&f_C=old"
            ),
            "company_ids": ["1586", "1441"],
        },
        query="software engineer",
        location="India",
        company_ids_override=["1035"],
    )

    assert url == (
        "https://www.linkedin.com/jobs/search?"
        "keywords=software+engineer&location=India&f_TPR=r2592000&f_C=1035"
    )


def test_build_paginated_search_url_advances_listing_window() -> None:
    url = _build_paginated_search_url(
        (
            "https://www.linkedin.com/jobs/search?"
            "keywords=entry+level+engineer&location=India&f_E=2&pageNum=0&start=0"
        ),
        page_index=2,
        page_size=25,
    )

    parsed = urlsplit(url)
    params = dict(parse_qsl(parsed.query))

    assert parsed.scheme == "https"
    assert parsed.netloc == "www.linkedin.com"
    assert parsed.path == "/jobs/search"
    assert params["keywords"] == "entry level engineer"
    assert params["location"] == "India"
    assert params["f_E"] == "2"
    assert params["pageNum"] == "2"
    assert params["start"] == "50"
