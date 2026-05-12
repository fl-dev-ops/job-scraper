# Not test properly
"""Standalone LangExtract experiment for job-description field extraction.

Usage:
    uv run python experiments/langextract_job_extraction.py \
        --text "Junior Python Developer, Bengaluru. Skills: Python, SQL. 0-2 years."

    uv run python experiments/langextract_job_extraction.py \
        --html-file sample_job.html \
        --jd-selector "div#jobDescriptionText"

Requires LANGEXTRACT_API_KEY or GOOGLE_API_KEY for the actual LLM call.
"""

# ruff: noqa: E402, I001

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import langextract as lx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from job_scraper.fetchers.base import extract_jd_text  # noqa: E402


PROMPT = """\
Extract job-posting fields from the text.

Rules:
- Extract only values explicitly stated in the text.
- Do not infer missing fields.
- Use repeated extractions for skills.
- Salary must stay as the source text; do not normalize it here.
- Experience must stay as the source text; do not normalize it here.
"""


EXAMPLES = [
    lx.data.ExampleData(
        text=(
            "Junior Python Developer at Acme Labs, Bengaluru. "
            "Experience: 0-2 years. Skills: Python, Django, SQL. "
            "Salary: 6-8 LPA. Work from office."
        ),
        extractions=[
            lx.data.Extraction("title", "Junior Python Developer"),
            lx.data.Extraction("company", "Acme Labs"),
            lx.data.Extraction("location", "Bengaluru"),
            lx.data.Extraction("experience", "0-2 years"),
            lx.data.Extraction("key_technical_skill", "Python"),
            lx.data.Extraction("key_technical_skill", "Django"),
            lx.data.Extraction("key_technical_skill", "SQL"),
            lx.data.Extraction("salary", "6-8 LPA"),
            lx.data.Extraction("work_mode", "Work from office"),
        ],
    )
]


MULTI_VALUE_FIELDS = {"key_technical_skill", "other_skill"}


def _load_text(args: argparse.Namespace) -> str:
    if args.text:
        return args.text

    if args.html_file:
        html = Path(args.html_file).read_text()
        return extract_jd_text(html, args.jd_selector)

    raise SystemExit("Pass --text or --html-file.")


def _to_dict(result: Any) -> dict[str, Any]:
    output: dict[str, Any] = {}
    extractions = getattr(result, "extractions", [])
    for extraction in extractions:
        key = extraction.extraction_class
        value = extraction.extraction_text
        if key in MULTI_VALUE_FIELDS:
            output.setdefault(key, []).append(value)
        else:
            output[key] = value
    return output


def run(text: str, model_id: str, api_key: str) -> dict[str, Any]:
    result = lx.extract(
        text_or_documents=text,
        prompt_description=PROMPT,
        examples=EXAMPLES,
        model_id=model_id,
        api_key=api_key,
        format_type=lx.data.FormatType.JSON,
        temperature=0.0,
        extraction_passes=1,
        max_char_buffer=4000,
    )
    return _to_dict(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", help="Raw job-description text to extract from")
    parser.add_argument("--html-file", help="HTML file containing a job detail page")
    parser.add_argument("--jd-selector", default="body", help="CSS selector for the JD body")
    parser.add_argument("--model-id", default="gemini-2.5-flash")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prepared input without calling LangExtract",
    )
    args = parser.parse_args()

    text = _load_text(args)
    if args.dry_run:
        print(text)
        return

    api_key = os.getenv("LANGEXTRACT_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit("Set LANGEXTRACT_API_KEY or GOOGLE_API_KEY.")

    print(json.dumps(run(text, args.model_id, api_key), indent=2))


if __name__ == "__main__":
    main()
