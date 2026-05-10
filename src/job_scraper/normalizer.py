"""Post-LLM normalisation.

Only deterministic canonicalisation here — no string parsing of ambiguous
content (the LLM handles that).
"""

from __future__ import annotations

import hashlib

from .schema import JobPosting


def compute_job_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def normalize_skills(skills: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in skills:
        cleaned = s.strip().lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return sorted(out)


def normalize_location(location: str | None) -> str | None:
    if not location:
        return None
    loc = location.strip()
    if not loc:
        return None
    for suffix in (", India", ", india", ", IN", ", in"):
        if loc.endswith(suffix):
            loc = loc[: -len(suffix)]
            break
    return loc.strip()


def normalize_posting(posting: JobPosting) -> JobPosting:
    """Return a copy of the posting with normalised fields."""
    return posting.model_copy(
        update={
            "key_technical_skills": normalize_skills(posting.key_technical_skills),
            "other_skills_notes": normalize_skills(posting.other_skills_notes),
            "location": normalize_location(posting.location),
        }
    )
