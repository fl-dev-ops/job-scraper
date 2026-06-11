"""Generate and save interview round topics for job postings.

Output is saved to data/rounds/<job_id>.md. Already-saved files are
skipped unless --force is passed (avoids redundant LLM calls).

Usage:
    uv run python scripts/preview_rounds.py                   # first file in data/jobs/linkedin
    uv run python scripts/preview_rounds.py --file data/jobs/linkedin/<id>.md
    uv run python scripts/preview_rounds.py --index 2
    uv run python scripts/preview_rounds.py --dir data/jobs/naukri
    uv run python scripts/preview_rounds.py --all                  # process every file in --dir
    uv run python scripts/preview_rounds.py --all --force          # regenerate even if saved
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from litellm import completion
from tqdm import tqdm

from job_scraper.llm import build_llm_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "jobs" / "linkedin"
ROUNDS_DIR = PROJECT_ROOT / "data" / "rounds"

_PROMPT_TEMPLATE = """\
You are an interview round analyzer. Given a Job Description, do the following:

STEP 1 – Extract Context Profile:
- Company type (startup/MNC/SME)
- Role type (sales/technical/ops/hybrid)
- Seniority level (fresher/mid/senior)
- Key personality traits mentioned
- Domain/industry

STEP 2 – Using ONLY the context profile and JD content,
generate topics for each applicable round:

SCREENING:
- Always include: self-introduction, strengths, weaknesses,
  career goal alignment
- Add logistics topics (location, targets, availability)
  if mentioned in JD
- Add motivation topics based on company type and domain
- Trigger rule: self-awareness topics are always included
  IF personality traits appear in the JD
- NEVER include technical skills, tools, frameworks, or
  domain competencies — those belong only in the Technical round
- NEVER name specific technologies (e.g. C++, Python, AWS) in
  Screening topics — rephrase as general motivation or domain interest

BEHAVIORAL:
- Extract past experience and situational topics
  ONLY from responsibilities listed in JD
- Each topic must map to a specific responsibility or
  experience requirement
- For sales roles: always include at least one
  scenario/role-play style question simulating a
  real client interaction (e.g. objection handling,
  competitor comparison, hesitant customer)
- Negotiation and objection handling must appear as
  explicit topics if "convincing" or "negotiation"
  is mentioned in JD

TECHNICAL:
- For technical roles: extract hard skills, tools,
  domain knowledge from JD
- For non-technical/sales roles: assess role-relevant
  acumen instead:
  - Product/service knowledge (what the company sells)
  - Industry/domain awareness
  - Process knowledge (sales cycle, CRM, lead management)
- Never skip this round

CULTURE FIT:
- Extract from personality traits, work style descriptors,
  and company/team context in JD
- Include values alignment if domain/industry is specific

For each round, list exactly 3-4 topics (minimum 3, maximum 4).
Prioritise the most important and JD-specific ones. Each topic must be:
- Specific to this JD (not generic)
- Labeled with the JD signal that justified it
- Topic name: 3-4 words maximum (terse keyword phrase, NOT a sentence)
- JD Signal: one short phrase or direct quote, not a full sentence

OUTPUT FORMAT:
Context Profile: <key values>
Round | Topic (3-4 words) | JD Signal (short phrase or quote)

Title: {title}
Company: {company}

Job Description:
{jd_text}"""


def _split_frontmatter(markdown: str, path: Path) -> tuple[dict[str, Any], str]:
    if not markdown.startswith("---\n"):
        raise ValueError(f"Missing YAML frontmatter: {path}")
    try:
        _, frontmatter, body = markdown.split("---\n", 2)
    except ValueError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {path}") from exc
    parsed = yaml.safe_load(frontmatter) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Frontmatter is not a mapping: {path}")
    return parsed, body


def _extract_full_jd(body: str) -> str:
    marker = "## Full Job Description"
    if marker not in body:
        return body.strip()
    jd = body.split(marker, 1)[1]
    jd = re.sub(r"\[View original posting\]\([^)]+\)\s*$", "", jd.strip())
    return re.sub(r"\n{3,}", "\n\n", jd).strip()


def _parse_markdown(path: Path) -> tuple[dict[str, Any], str]:
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"), path)
    return frontmatter, _extract_full_jd(body)


def _save_path(job_id: str) -> Path:
    return ROUNDS_DIR / f"{job_id}.md"


def _call_llm(title: str, company: str, jd_text: str) -> str:
    prompt = _PROMPT_TEMPLATE.format(title=title, company=company, jd_text=jd_text[:12000])
    llm_config = build_llm_config()
    response = completion(
        **llm_config.litellm_kwargs(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=1500,
    )
    return response.choices[0].message.content or "(no response)"


def process(path: Path, *, force: bool = False) -> Path | None:
    """Generate and save round topics for one job file.

    Returns the saved path if written, None if skipped (already exists).
    """
    frontmatter, jd_text = _parse_markdown(path)
    title = str(frontmatter.get("title") or "Unknown role")
    company = str(frontmatter.get("company") or "Unknown company")
    job_id = str(frontmatter.get("job_id") or path.stem)
    source = str(frontmatter.get("source") or "")

    out_path = _save_path(job_id)
    if out_path.exists() and not force:
        return None

    output = _call_llm(title, company, jd_text)

    ROUNDS_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = (
        f"# {title} — {company}\n\n"
        f"**Source job file:** `{path.name}`  \n"
        f"**Source URL:** {source}  \n"
        f"**Generated:** {generated_at}\n\n"
        f"---\n\n"
    )
    out_path.write_text(header + output + "\n", encoding="utf-8")
    return out_path


def preview(path: Path, *, force: bool = False) -> None:
    frontmatter, _ = _parse_markdown(path)
    title = str(frontmatter.get("title") or "Unknown role")
    company = str(frontmatter.get("company") or "Unknown company")

    out_path = process(path, force=force)

    sep = "=" * 64
    print(f"\n{sep}")
    print(f"Job:  {title} @ {company}")
    print(f"File: {path.name}")
    if out_path:
        print(f"Saved: {out_path.relative_to(PROJECT_ROOT)}")
    else:
        out_path = _save_path(str(frontmatter.get("job_id") or path.stem))
        print(f"Skipped (already saved): {out_path.relative_to(PROJECT_ROOT)}")
    print(sep)
    print()
    print(out_path.read_text(encoding="utf-8") if out_path.exists() else "(file missing)")
    print()


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description="Generate and save interview round topics.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--file", type=Path, help="Path to a specific .md job file")
    group.add_argument(
        "--index",
        type=int,
        default=0,
        help="Pick the Nth .md file from --dir (0-based, default: 0)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process every .md file in --dir",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory to pick files from (default: data/jobs/linkedin)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if a saved file already exists",
    )
    args = parser.parse_args()

    if args.all:
        files = sorted(args.dir.glob("*.md"))
        if not files:
            parser.error(f"No .md files found in {args.dir}")
        written = skipped = 0
        for path in tqdm(files, desc="Generating round topics", unit="job"):
            out = process(path, force=args.force)
            if out:
                written += 1
            else:
                skipped += 1
        rounds_rel = ROUNDS_DIR.relative_to(PROJECT_ROOT)
        print(f"\nDone — {written} generated, {skipped} skipped. Output: {rounds_rel}")
        return

    if args.file:
        path = args.file
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            parser.error(f"File not found: {path}")
    else:
        files = sorted(args.dir.glob("*.md"))
        if not files:
            parser.error(f"No .md files found in {args.dir}")
        if args.index >= len(files):
            parser.error(f"--index {args.index} out of range (found {len(files)} files)")
        path = files[args.index]

    preview(path, force=args.force)


if __name__ == "__main__":
    main()
