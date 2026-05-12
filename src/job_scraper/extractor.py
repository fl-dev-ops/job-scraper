"""LLM-powered field extractor.

Uses Crawl4AI's LLMExtractionStrategy with the `raw://` URL prefix to extract
structured fields from JD plain text. LiteLLM under the hood routes the
provider string to OpenRouter.

Pipeline:
    raw HTML  -> extract_jd_text() -> plain text
    plain text -> Crawl4AI raw:// + LLMExtractionStrategy -> JSON
    JSON -> LLMExtractedFields (Pydantic) -> JobPosting (with metadata)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from crawl4ai import (
    LLMConfig,
    LLMExtractionStrategy,
)
from pydantic import ValidationError

from .fetchers.base import extract_html_field, extract_jd_text
from .schema import JobPosting, LLMExtractedFields, SiteName
from .utils.logging import get_logger

log = get_logger("extractor")

_EXTRACTION_INSTRUCTION = """\
You are extracting structured fields from a single job posting.

Rules:
1. Return null for any field NOT explicitly stated in the text. Do NOT infer,
   estimate, or guess. Hallucinations are worse than nulls.
2. salary_inr_per_year_min / salary_inr_per_year_max: integer INR/year.
     "6 LPA"                          -> min=600000, max=600000
     "6-9 LPA"                        -> min=600000, max=900000
     "INR 4,00,000 - 7,00,000 / year" -> min=400000, max=700000
     "Not disclosed" or absent        -> both null
3. experience_min_years / experience_max_years: integer years.
     "0-2 years" -> min=0, max=2
     "Fresher"   -> min=0, max=0
     absent      -> both null
4. key_technical_skills: programming languages, frameworks, libraries,
   databases, cloud platforms, dev tools ONLY. No soft skills here.
5. other_skills_notes: soft skills, domain knowledge, certifications, nice-to-haves.
6. role: classify as one of: frontend, backend, fullstack, mobile, data, devops, other.
7. work_mode: only "remote", "hybrid", or "onsite". Null if not stated.
8. title: required; copy the exact title as posted.

Return a single JSON object matching the schema. No prose, no markdown fences.
"""


def apply_html_overrides(
    posting: JobPosting,
    html: str,
    title_selector: str | None = None,
    location_selector: str | None = None,
    company_selector: str | None = None,
) -> JobPosting:
    """Apply deterministic page-header fields that are more reliable than JD text."""
    updates: dict[str, Any] = {}
    if title_selector:
        css_title = extract_html_field(html, title_selector)
        if css_title:
            updates["title"] = css_title
    if location_selector:
        css_location = extract_html_field(html, location_selector)
        if css_location:
            updates["location"] = css_location
    if company_selector:
        css_company = extract_html_field(html, company_selector)
        if css_company:
            updates["company"] = css_company
    if not updates:
        return posting
    return posting.model_copy(update=updates)


class JobExtractor:
    """Singleton extractor reused across all sites."""

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or os.getenv(
            "OPENROUTER_MODEL", "anthropic/claude-haiku-4.5"
        )
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY missing. Set it in .env before running."
            )

        self._llm_config = LLMConfig(
            provider=f"openrouter/{self.model}",
            api_token=self.api_key,
        )
        self._strategy = LLMExtractionStrategy(
            llm_config=self._llm_config,
            schema=LLMExtractedFields.model_json_schema(),
            extraction_type="schema",
            instruction=_EXTRACTION_INSTRUCTION,
            apply_chunking=False,
            extra_args={"temperature": 0.0, "max_tokens": 2000},
        )

    async def _run_crawler(self, plain_text: str) -> str | None:
        """Invoke Crawl4AI's extraction strategy directly on plain text."""
        extracted = await self._strategy.arun("raw://job-posting", [plain_text])
        if not extracted:
            log.warning("crawl4ai_failed", error="empty extraction result")
            return None
        return json.dumps(extracted, indent=4, default=str, ensure_ascii=False)

    async def extract_async(
        self,
        html: str,
        source_url: str,
        jd_body_selector: str,
        site: SiteName,
        title_selector: str | None = None,
        location_selector: str | None = None,
        company_selector: str | None = None,
    ) -> JobPosting | None:
        plain_text = extract_jd_text(html, jd_body_selector)
        if not plain_text or len(plain_text) < 50:
            log.warning("jd_text_too_short", url=source_url, length=len(plain_text))
            return None

        llm_text = plain_text
        if title_selector:
            css_title = extract_html_field(html, title_selector)
            if css_title:
                llm_text = f"Job title: {css_title}\n\n{plain_text}"

        raw_json = await self._run_crawler(llm_text)
        if not raw_json:
            return None

        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as e:
            log.warning("json_decode_failed", url=source_url, error=str(e))
            return None

        if isinstance(parsed, list):
            if not parsed:
                log.warning("empty_extraction", url=source_url)
                return None
            fields_dict = parsed[0]
        else:
            fields_dict = parsed

        fields_dict.pop("error", None)

        try:
            llm_fields = LLMExtractedFields(**fields_dict)
        except ValidationError as e:
            log.warning(
                "validation_failed", url=source_url, errors=e.errors(include_url=False)
            )
            return None

        job_id = hashlib.sha1(source_url.encode()).hexdigest()
        posting = JobPosting(
            **llm_fields.model_dump(),
            job_id=job_id,
            source=source_url,
            site=site,
            scraped_at=datetime.now(UTC),
            full_job_description=plain_text,
        )
        return apply_html_overrides(
            posting, html, title_selector, location_selector, company_selector
        )

    def extract(
        self,
        html: str,
        source_url: str,
        jd_body_selector: str,
        site: SiteName,
        title_selector: str | None = None,
        location_selector: str | None = None,
        company_selector: str | None = None,
    ) -> JobPosting | None:
        return asyncio.run(
            self.extract_async(
                html,
                source_url,
                jd_body_selector,
                site,
                title_selector,
                location_selector,
                company_selector,
            )
        )


def build_extractor() -> JobExtractor:
    """Convenience factory honoring .env overrides."""
    return JobExtractor()


def get_extraction_token_usage() -> dict[str, Any] | None:
    """Optional: surface aggregated token usage if the strategy exposes it."""
    return None
