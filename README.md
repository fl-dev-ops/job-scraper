# job-scraper

Scrapes CS graduate / early-career job postings (0–2 years experience) from **Naukri, Indeed, and LinkedIn**. Extracts structured fields via LLM (OpenRouter) and stores each posting as a Markdown file with full verbatim JD.

## Stack

| Layer | Tool |
|---|---|
| Browser + anti-detection | SeleniumBase CDP mode for Indeed; Botasaurus for Naukri/LinkedIn |
| Fast DOM extraction | selectolax |
| LLM field extraction | Crawl4AI → LiteLLM → OpenRouter |
| Schema validation | Pydantic v2 |
| CLI | Typer |

## Setup

```bash
# Install uv if not already present
curl -Ls https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Install Playwright browser binaries (Crawl4AI dependency)
uv run playwright install chromium

# Copy and fill environment variables
cp .env.example .env
# Edit .env: set OPENROUTER_API_KEY at minimum
```

## Usage

```bash
# Scrape all configured queries for a site
uv run python -m job_scraper naukri --location bangalore --max 50

# Override with a specific query
uv run python -m job_scraper indeed --query "junior python developer" --max 20

# Scrape all three sites sequentially
uv run python -m job_scraper all --location india --max 30
```

Company filters are only applied before fetching detail pages.

For LinkedIn, use `company_ids`. LinkedIn's URL filter uses company IDs, not
names:

```yaml
company_ids:
  - "1586"
```

For Indeed, use `company_names`. The fetcher expands each configured query with
Indeed's official company search operator:

```yaml
company_names:
  - Amazon
```

Naukri does not currently have a configured company filter. Its public search
supports company names in the keyword box, so use company-specific query text
when needed.

Output files land in `data/jobs/{site}/{job_id}.md`.

## Output format

Each `.md` file contains YAML frontmatter with all structured fields and a `## Full Job Description` section with the verbatim scraped JD text.

Fields: `company`, `role`, `title`, `location`, `work_mode`, `experience_min_years`, `experience_max_years`, `key_technical_skills`, `other_skills_notes`, `salary_inr_per_year_min`, `salary_inr_per_year_max`, `education_requirement`, `posted_date`, `source`, `site`, `scraped_at`, `job_id`.

Missing fields are `null` — never inferred.

## Development

```bash
uv run pytest          # run tests
uv run ruff check .    # lint
uv run mypy src/       # type check
```

## Known limitations

- **Naukri**: reliable without proxies.
- **Indeed**: reliable; add `INDEED_PROXY_URL` (residential) for sustained runs.
- **LinkedIn**: Botasaurus handles fingerprint-level detection. LinkedIn additionally applies session/behavioral analysis (request cadence, navigation patterns). Mitigations are in place (slow pacing, session rotation, headless=false), but intermittent blocks at volume are a property of LinkedIn's detection system — not a tool limitation. Start with `--max 10` to gauge your session tolerance before scaling up. Set `LINKEDIN_PROXY_URL` to a residential proxy for best results.
