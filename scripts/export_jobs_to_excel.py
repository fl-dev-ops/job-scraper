"""Export scraped job Markdown files to an Excel workbook.

Usage:
    uv run python scripts/export_jobs_to_excel.py
    uv run python scripts/export_jobs_to_excel.py --input-dir data/jobs/linkedin
    uv run python scripts/export_jobs_to_excel.py --no-llm
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import yaml
from dotenv import load_dotenv
from tqdm import tqdm

from job_scraper.llm import ResolvedLLMConfig, build_llm_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "jobs" / "linkedin"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "exports" / "linkedin_jobs.xlsx"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"

HEADERS = [
    "Job title",
    "Company name",
    "Role category",
    "Role type",
    "Location",
    "Work mode",
    "Role summary",
    "Key responsibilities",
    "Required skills",
    "Other skills / notes",
    "Education requirement",
    "Screening",
    "Behavioural",
    "Technical",
    "Culture fit",
    "salary_inr_per_year_min",
    "salary_inr_per_year_max",
    "experience_min_years",
    "experience_max_years",
    "Source URL",
    "Full job description",
]


@dataclass
class JobRecord:
    title: str
    company: str
    role_category: str
    role_type: str
    location: str
    work_mode: str
    role_summary: str
    key_responsibilities: str
    required_skills: str
    other_skills_notes: str
    education_requirement: str
    round_screening: str
    round_behavioural: str
    round_technical: str
    round_culture_fit: str
    salary_min: int | None
    salary_max: int | None
    experience_min: int | None
    experience_max: int | None
    source_url: str
    full_jd: str


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _join_list(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(str(item) for item in value if str(item).strip())


def _limit_round_topics(value: str, *, max_topics: int = 4, max_words: int = 3) -> str:
    """Cap a round's topics to at most `max_topics`, each at most `max_words` words."""
    topics: list[str] = []
    for raw in value.split(";"):
        topic = raw.strip()
        if not topic:
            continue
        topic = " ".join(topic.split()[:max_words])
        topics.append(topic)
        if len(topics) == max_topics:
            break
    return "; ".join(topics)


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
        return _clean_text(body)

    jd = body.split(marker, 1)[1]
    jd = re.sub(r"\[View original posting\]\([^)]+\)\s*$", "", jd.strip())
    return _clean_text(jd)


def _parse_markdown(path: Path) -> tuple[dict[str, Any], str]:
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"), path)
    return frontmatter, _extract_full_jd(body)


def _fallback_summary(frontmatter: dict[str, Any], jd_text: str) -> dict[str, str]:
    title = str(frontmatter.get("title") or "Unknown role")
    company = str(frontmatter.get("company") or "Unknown company")
    location = str(frontmatter.get("location") or "Unknown location")

    sentences = re.split(r"(?<=[.!?])\s+", jd_text)
    role_summary = " ".join(sentences[:2]).strip()
    if not role_summary:
        role_summary = f"{title} role at {company} in {location}."

    responsibility_lines = [
        line.strip(" -•\t")
        for line in jd_text.splitlines()
        if re.search(r"\b(build|develop|design|work|collaborate|deliver|implement|manage)\b", line, re.I)
    ][:5]

    return {
        "role_summary": role_summary[:600],
        "key_responsibilities": "; ".join(responsibility_lines)[:900],
        "round_screening": "Background; Role fit; Motivation; Availability",
        "round_behavioural": "Teamwork; Communication; Conflict handling; Past projects",
        "round_technical": "Core fundamentals; Coding; Key technologies; System design",
        "round_culture_fit": "Values alignment; Long-term goals; Ways of working; Team fit",
    }


def _infer_role_type(frontmatter: dict[str, Any]) -> str:
    explicit = (
        frontmatter.get("source_query")
        or frontmatter.get("query")
        or frontmatter.get("role_type")
    )
    if explicit:
        return str(explicit).strip()

    title = str(frontmatter.get("title") or "").lower()
    role = str(frontmatter.get("role") or "").lower()

    title_rules = [
        (r"\bfresher\b", "Fresher jobs"),
        (r"\bassociate software engineer\b", "associate software engineer"),
        (r"\bandroid\b|\bkotlin\b|\bmobile\b", "android developer"),
        (r"\bdevops\b|\bsre\b|\bsite reliability\b", "devops engineer"),
        (r"\bdata\b|\banalyst\b|\banalytics\b|\bmachine learning\b|\bml\b|\bai\b|\bbi\b", "data engineer"),
        (r"\bfront\s*end\b|\bfrontend\b|\bui\b|\breact\b|\bangular\b", "frontend developer"),
        (r"\bback\s*end\b|\bbackend\b|\bserver\b|\bapi\b", "backend developer"),
        (r"\bfull\s*stack\b|\bfullstack\b", "full stack developer"),
        (r"\bsoftware engineer\b|\bsoftware developer\b", "software engineer"),
    ]
    for pattern, role_type in title_rules:
        if re.search(pattern, title):
            return role_type

    role_map = {
        "frontend": "frontend developer",
        "backend": "backend developer",
        "fullstack": "full stack developer",
        "mobile": "android developer",
        "data": "data engineer",
        "devops": "devops engineer",
        "other": "Fresher jobs",
    }
    return role_map.get(role, role or "")


def _summarize_with_llm(
    frontmatter: dict[str, Any],
    jd_text: str,
    llm_config: ResolvedLLMConfig,
) -> dict[str, str]:
    from litellm import completion

    title = frontmatter.get("title")
    company = frontmatter.get("company")
    prompt = f"""Extract concise spreadsheet fields from this job description.

This candidate will go through a FIXED 4-round interview process for every job:
1. Screening   - recruiter/initial screen: background, role fit, motivation, eligibility.
2. Behavioural - teamwork, communication, conflict handling, past-project STAR scenarios.
3. Technical   - the actual coding/fundamentals/technologies/system-design for THIS role.
4. Culture fit - company values, long-term goals, ways of working, team fit.

Return one JSON object with these exact string keys:
- role_summary: 1-2 sentences, no bullets.
- key_responsibilities: 3-6 concise bullet-like phrases separated by semicolons.
- round_screening: topics for the Screening round, separated by semicolons.
- round_behavioural: topics for the Behavioural round, separated by semicolons.
- round_technical: topics for the Technical round, separated by semicolons.
- round_culture_fit: topics for the Culture fit round, separated by semicolons.

For the four round_* fields: give AT MOST 4 topics each, and each topic must be AT MOST
3 words (terse keywords, not sentences). Make the topics SPECIFIC to this job by inferring
from the role title, role family, responsibilities, and required skills. The Technical
round especially should name the concrete technologies, frameworks, and problem areas from
the job description. For role_summary and key_responsibilities, use only the job
description; do not infer missing facts.

Title: {title}
Company: {company}

Job description:
{jd_text[:12000]}
"""
    response = completion(
        **llm_config.litellm_kwargs(),
        messages=[
            {"role": "system", "content": "Return valid JSON only. No markdown fences."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=900,
    )
    content = response.choices[0].message.content or "{}"
    parsed = _load_json_object(content)
    return {
        "role_summary": str(parsed.get("role_summary") or "").strip(),
        "key_responsibilities": str(parsed.get("key_responsibilities") or "").strip(),
        "round_screening": _limit_round_topics(str(parsed.get("round_screening") or "")),
        "round_behavioural": _limit_round_topics(str(parsed.get("round_behavioural") or "")),
        "round_technical": _limit_round_topics(str(parsed.get("round_technical") or "")),
        "round_culture_fit": _limit_round_topics(str(parsed.get("round_culture_fit") or "")),
    }


def _load_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON was not an object")
    return parsed


def _build_record(
    path: Path,
    *,
    use_llm: bool,
    llm_config: ResolvedLLMConfig | None,
) -> JobRecord:
    frontmatter, jd_text = _parse_markdown(path)
    summary = _fallback_summary(frontmatter, jd_text)

    if use_llm and llm_config and jd_text:
        try:
            llm_summary = _summarize_with_llm(frontmatter, jd_text, llm_config)
            summary.update({key: value for key, value in llm_summary.items() if value})
        except Exception as exc:  # noqa: BLE001 - keep export usable if one summary fails
            print(f"warning: LLM summary failed for {path.name}: {exc}")

    return JobRecord(
        title=str(frontmatter.get("title") or ""),
        company=str(frontmatter.get("company") or ""),
        role_category=str(frontmatter.get("role") or ""),
        role_type=_infer_role_type(frontmatter),
        location=str(frontmatter.get("location") or ""),
        work_mode=str(frontmatter.get("work_mode") or ""),
        role_summary=summary["role_summary"],
        key_responsibilities=summary["key_responsibilities"],
        required_skills=_join_list(frontmatter.get("key_technical_skills")),
        other_skills_notes=_join_list(frontmatter.get("other_skills_notes")),
        education_requirement=str(frontmatter.get("education_requirement") or ""),
        round_screening=summary["round_screening"],
        round_behavioural=summary["round_behavioural"],
        round_technical=summary["round_technical"],
        round_culture_fit=summary["round_culture_fit"],
        salary_min=frontmatter.get("salary_inr_per_year_min"),
        salary_max=frontmatter.get("salary_inr_per_year_max"),
        experience_min=frontmatter.get("experience_min_years"),
        experience_max=frontmatter.get("experience_max_years"),
        source_url=str(frontmatter.get("source") or ""),
        full_jd=jd_text,
    )


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell_xml(value: object, row_idx: int, col_idx: int, style: int = 0) -> str:
    cell_ref = f"{_column_name(col_idx)}{row_idx}"
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{cell_ref}"{style_attr}/>'
    if isinstance(value, int | float):
        return f'<c r="{cell_ref}"{style_attr}><v>{value}</v></c>'
    return (
        f'<c r="{cell_ref}" t="inlineStr"{style_attr}>'
        f"<is><t>{escape(str(value))}</t></is></c>"
    )


def _sheet_xml(rows: list[list[object]]) -> str:
    widths = [24, 22, 16, 24, 24, 12, 58, 70, 52, 52, 28, 50, 50, 60, 50, 18, 18, 18, 18, 46, 100]
    cols = "".join(
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate(widths, start=1)
    )
    row_xml = []
    for row_idx, row in enumerate(rows, start=1):
        style = 1 if row_idx == 1 else 2
        height = 24 if row_idx == 1 else 72
        cells = "".join(_cell_xml(value, row_idx, col_idx, style) for col_idx, value in enumerate(row, start=1))
        row_xml.append(f'<row r="{row_idx}" ht="{height}" customHeight="1">{cells}</row>')

    last_row = max(len(rows), 1)
    last_col = _column_name(len(HEADERS))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
    </sheetView>
  </sheetViews>
  <cols>{cols}</cols>
  <sheetData>{''.join(row_xml)}</sheetData>
  <autoFilter ref="A1:{last_col}{last_row}"/>
</worksheet>"""


def _write_xlsx(records: list[JobRecord], output_path: Path, sheet_name: str = "Jobs") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_rows = [
        [
            record.title,
            record.company,
            record.role_category,
            record.role_type,
            record.location,
            record.work_mode,
            record.role_summary,
            record.key_responsibilities,
            record.required_skills,
            record.other_skills_notes,
            record.education_requirement,
            record.round_screening,
            record.round_behavioural,
            record.round_technical,
            record.round_culture_fit,
            record.salary_min,
            record.salary_max,
            record.experience_min,
            record.experience_max,
            record.source_url,
            record.full_jd,
        ]
        for record in records
    ]
    rows: list[list[object]] = [HEADERS, *data_rows]
    created = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        "xl/workbook.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        "xl/styles.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="3">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
  </cellXfs>
</styleSheet>""",
        "xl/worksheets/sheet1.xml": _sheet_xml(rows),
        "docProps/core.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>job-scraper</dc:creator>
  <cp:lastModifiedBy>job-scraper</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>
</cp:coreProperties>""",
        "docProps/app.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>job-scraper</Application>
</Properties>""",
    }

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as xlsx:
        for name, content in files.items():
            xlsx.writestr(name, content)


def export_jobs(input_dir: Path, output_path: Path, *, use_llm: bool, model: str, limit: int | None) -> int:
    llm_config = None
    if use_llm:
        try:
            llm_config = build_llm_config(model=model)
        except RuntimeError as exc:
            print(f"warning: {exc} Using deterministic summary fallback")

    paths = sorted(input_dir.glob("*.md"))
    if limit is not None:
        paths = paths[:limit]
    if not paths:
        raise FileNotFoundError(f"No Markdown job files found in {input_dir}")

    records = [
        _build_record(path, use_llm=use_llm, llm_config=llm_config)
        for path in tqdm(paths, desc="Exporting jobs", unit="job")
    ]
    _write_xlsx(records, output_path, sheet_name=f"{input_dir.name.capitalize()} Jobs")
    return len(records)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Export scraped job Markdown files to Excel.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL") or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL),
    )
    parser.add_argument("--limit", type=int, default=None, help="Export only the first N markdown files")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM summaries")
    args = parser.parse_args()

    count = export_jobs(
        args.input_dir,
        args.output,
        use_llm=not args.no_llm,
        model=args.model,
        limit=args.limit,
    )
    print(f"Exported {count} jobs to {args.output}")


if __name__ == "__main__":
    main()
