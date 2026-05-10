"""Persist a JobPosting as a Markdown file with YAML frontmatter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from .schema import JobPosting

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "jobs"


def get_output_path(site: str, job_id: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / site / f"{job_id}.md"


def _serialize_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _build_frontmatter(posting: JobPosting) -> dict:
    fm = posting.model_dump(exclude={"full_job_description"})
    return {k: _serialize_value(v) for k, v in fm.items()}


def _build_body(posting: JobPosting) -> str:
    parts: list[str] = []
    company = posting.company or "Unknown"
    parts.append(f"# {posting.title} — {company}")

    meta_bits: list[str] = []
    if posting.location:
        meta_bits.append(f"**Location:** {posting.location}")
    if posting.experience_min_years is not None or posting.experience_max_years is not None:
        lo = posting.experience_min_years if posting.experience_min_years is not None else "?"
        hi = posting.experience_max_years if posting.experience_max_years is not None else "?"
        meta_bits.append(f"**Experience:** {lo}-{hi} years")
    if posting.work_mode:
        meta_bits.append(f"**Mode:** {posting.work_mode}")
    if meta_bits:
        parts.append("  ".join(meta_bits))

    if posting.key_technical_skills:
        parts.append("## Key Technical Skills")
        parts.extend(f"- {s}" for s in posting.key_technical_skills)

    if posting.other_skills_notes:
        parts.append("## Other Skills / Notes")
        parts.extend(f"- {s}" for s in posting.other_skills_notes)

    if posting.salary_inr_per_year_min or posting.salary_inr_per_year_max:
        lo = posting.salary_inr_per_year_min
        hi = posting.salary_inr_per_year_max
        parts.append(f"**Salary (INR / year):** {lo} - {hi}")

    parts.append("## Full Job Description")
    parts.append(posting.full_job_description)
    parts.append(f"[View original posting]({posting.source})")

    return "\n\n".join(parts)


def write(posting: JobPosting, data_dir: Path = DATA_DIR) -> bool:
    """Write the posting to disk. Returns True if written, False if skipped."""
    path = get_output_path(posting.site, posting.job_id, data_dir)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter_yaml = yaml.safe_dump(
        _build_frontmatter(posting),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    body = _build_body(posting)
    content = f"---\n{frontmatter_yaml}---\n\n{body}\n"
    path.write_text(content, encoding="utf-8")
    return True
