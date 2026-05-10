"""Normalizer unit tests — no network, no LLM, no browser."""

from __future__ import annotations

import pytest

# TODO: from job_scraper.normalizer import compute_job_id, normalize_skills, normalize_location


def test_compute_job_id_is_stable():
    """Same URL always produces the same job_id."""
    # TODO: assert compute_job_id("https://naukri.com/job/123") == compute_job_id("https://naukri.com/job/123")


def test_compute_job_id_differs_for_different_urls():
    # TODO: assert compute_job_id("https://naukri.com/job/123") != compute_job_id("https://naukri.com/job/456")


def test_normalize_skills_deduplicates():
    # TODO: assert normalize_skills(["Python", "python", "PYTHON"]) == ["python"]


def test_normalize_skills_sorts():
    # TODO: assert normalize_skills(["react", "django", "aws"]) == ["aws", "django", "react"]


def test_normalize_skills_strips_whitespace():
    # TODO: assert normalize_skills(["  node.js ", "Go "]) == ["go", "node.js"]


def test_normalize_location_strips_india_suffix():
    # TODO: assert normalize_location("Bangalore, India") == "Bangalore"
    # TODO: assert normalize_location("Chennai, IN") == "Chennai"


def test_normalize_location_none():
    # TODO: assert normalize_location(None) is None
