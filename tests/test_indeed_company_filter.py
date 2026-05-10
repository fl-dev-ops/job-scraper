"""Indeed company search filter tests."""

from __future__ import annotations

from job_scraper.fetchers.indeed import _expand_company_queries


def test_expand_company_queries_adds_indeed_company_operator():
    queries = _expand_company_queries(
        ["junior software engineer", "fresher developer"],
        ["Amazon", "Tata Consultancy Services"],
    )

    assert queries == [
        'junior software engineer company:"Amazon"',
        'junior software engineer company:"Tata Consultancy Services"',
        'fresher developer company:"Amazon"',
        'fresher developer company:"Tata Consultancy Services"',
    ]


def test_expand_company_queries_keeps_queries_when_no_companies_configured():
    queries = _expand_company_queries(["junior software engineer"], [])

    assert queries == ["junior software engineer"]
