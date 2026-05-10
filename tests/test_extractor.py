"""Offline extractor tests.

Uses saved HTML fixtures (tests/fixtures/*.html) so no network calls are made.
These tests validate the full LLM → Pydantic pipeline including OpenRouter wiring.

To add a new fixture:
    1. Scrape a real JD page and save the HTML to tests/fixtures/{site}_sample.html
    2. Add a test case below asserting the fields you expect to be extracted
"""

from __future__ import annotations

import pytest

# TODO: from job_scraper.extractor import JobExtractor
# TODO: from job_scraper.schema import JobPosting
# TODO: from pathlib import Path

FIXTURES_DIR = None  # TODO: Path(__file__).parent / "fixtures"


@pytest.fixture
def extractor():
    # TODO: return JobExtractor(model="openrouter/...", api_key="...") using test env vars
    pytest.skip("extractor not yet implemented")


@pytest.fixture
def naukri_html():
    # TODO: return (FIXTURES_DIR / "naukri_sample.html").read_text()
    pytest.skip("fixture not yet added")


def test_naukri_extraction_returns_job_posting(extractor, naukri_html):
    """Extractor produces a valid JobPosting from a Naukri HTML fixture."""
    # TODO: posting = extractor.extract(naukri_html, "https://naukri.com/...", {})
    # TODO: assert isinstance(posting, JobPosting)
    # TODO: assert posting.title  # always present
    # TODO: assert posting.site == "naukri"
    # TODO: assert posting.full_job_description  # verbatim JD always stored


def test_extraction_does_not_hallucinate_salary(extractor, naukri_html):
    """When salary is not in the JD, both salary fields are null (not guessed)."""
    # TODO: use a fixture where salary is known to be absent
    # TODO: assert posting.salary_inr_per_year_min is None
    # TODO: assert posting.salary_inr_per_year_max is None


def test_extraction_does_not_hallucinate_experience(extractor, naukri_html):
    """When experience range is absent, both experience fields are null."""
    # TODO: assert posting.experience_min_years is None
    # TODO: assert posting.experience_max_years is None
