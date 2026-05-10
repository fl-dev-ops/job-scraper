"""Pydantic models for job postings.

Two models:
- LLMExtractedFields: what the LLM returns. Pure JD-derived fields.
- JobPosting: the full record we persist. Composes LLMExtractedFields with
  metadata we set ourselves (job_id, source, site, scraped_at, full_job_description).

Splitting these prevents the LLM from being asked about job_id/source/etc.,
which it cannot know from the JD text alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RoleCategory = Literal[
    "frontend", "backend", "fullstack", "mobile", "data", "devops", "other"
]
WorkMode = Literal["remote", "hybrid", "onsite"]
SiteName = Literal["naukri", "indeed", "linkedin"]


class LLMExtractedFields(BaseModel):
    """Fields the LLM extracts directly from JD text.

    Every field is optional except `title` (which always exists in any posting).
    The LLM is instructed to return null for any missing field — never infer.
    """

    title: str = Field(description="Exact job title as posted, no trailing whitespace")
    company: str | None = Field(default=None, description="Company / organization name")
    role: RoleCategory | None = Field(
        default=None,
        description=(
            "Normalised role category. Pick one based on title and JD: "
            "frontend, backend, fullstack, mobile, data, devops, or other."
        ),
    )
    location: str | None = Field(default=None, description="Job location, e.g. 'Bangalore'")
    work_mode: WorkMode | None = Field(
        default=None, description="remote, hybrid, or onsite. Null if not stated."
    )
    experience_min_years: int | None = Field(
        default=None, description="Minimum experience in integer years. Null if not stated."
    )
    experience_max_years: int | None = Field(
        default=None, description="Maximum experience in integer years. Null if not stated."
    )
    key_technical_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Programming languages, frameworks, libraries, databases, cloud "
            "platforms, and dev tools mentioned. Empty list if none found."
        ),
    )
    other_skills_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Soft skills, domain knowledge, certifications, nice-to-haves. "
            "Empty list if none found."
        ),
    )
    salary_inr_per_year_min: int | None = Field(
        default=None,
        description=(
            "Lower bound in INR/year as integer. '6 LPA' -> 600000. Null if not stated."
        ),
    )
    salary_inr_per_year_max: int | None = Field(
        default=None,
        description=(
            "Upper bound in INR/year as integer. '6-9 LPA' -> max=900000. Null if not stated."
        ),
    )
    education_requirement: str | None = Field(
        default=None,
        description="Education requirement as stated, e.g. 'B.Tech/B.E.'. Null if not stated.",
    )


class JobPosting(LLMExtractedFields):
    """Full record persisted to disk. Adds metadata we set ourselves."""

    job_id: str
    source: str
    site: SiteName
    scraped_at: datetime
    posted_date: datetime | None = None
    full_job_description: str
