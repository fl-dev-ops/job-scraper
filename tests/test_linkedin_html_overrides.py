"""LinkedIn top-card override tests."""

from __future__ import annotations

from datetime import UTC, datetime

from job_scraper.extractor import apply_html_overrides
from job_scraper.fetchers.base import load_site_config
from job_scraper.schema import JobPosting


def _posting(company: str | None) -> JobPosting:
    return JobPosting(
        title="Data Engineer",
        company=company,
        role="data",
        location=None,
        work_mode=None,
        experience_min_years=None,
        experience_max_years=None,
        key_technical_skills=[],
        other_skills_notes=[],
        salary_inr_per_year_min=None,
        salary_inr_per_year_max=None,
        education_requirement=None,
        job_id="job-1",
        source="https://in.linkedin.com/jobs/view/data-engineer-at-tata-consultancy-services-4404739518",
        site="linkedin",
        scraped_at=datetime.now(UTC),
        full_job_description="Job Title: Google Data Engineer / Lead",
    )


def test_linkedin_company_top_card_overrides_llm_company():
    html = """
    <main>
      <a class="topcard__org-name-link">Tata Consultancy Services</a>
      <div class="show-more-less-html__markup">Job Title: Google Data Engineer / Lead</div>
    </main>
    """

    posting = apply_html_overrides(
        _posting(company="Google"),
        html,
        company_selector="a.topcard__org-name-link",
    )

    assert posting.company == "Tata Consultancy Services"


def test_linkedin_company_top_card_fills_null_company():
    html = """
    <main>
      <a class="topcard__org-name-link">Tata Consultancy Services</a>
      <div class="show-more-less-html__markup">AWS Data Engineer</div>
    </main>
    """

    posting = apply_html_overrides(
        _posting(company=None),
        html,
        company_selector="a.topcard__org-name-link",
    )

    assert posting.company == "Tata Consultancy Services"


def test_linkedin_header_overrides_use_single_selectors():
    selectors = load_site_config("linkedin")["selectors"]

    assert selectors["title_selector"] == "h1.top-card-layout__title"
    assert selectors["company_selector"] == "a.topcard__org-name-link"
    assert selectors["location_selector"] == "span.topcard__flavor--bullet"
