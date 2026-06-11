# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run pytest                      # run all tests
uv run pytest tests/test_extractor.py  # run a single test file
uv run ruff check .                # lint
uv run mypy src/                   # type check

# Run the scraper
uv run python -m job_scraper naukri --location bangalore --max 50
uv run python -m job_scraper linkedin --location India --max 10
uv run python -m job_scraper all --location India --max 30

# Company-level (per-company iteration, not combined f_C filter)
uv run python scripts/scrape_linkedin_per_company.py --max 5
uv run python scripts/scrape_naukri_search.py --location India --max 1

# Scrape a single detail URL directly
uv run python -m job_scraper linkedin --url "https://..." --max 1
```

## Architecture

The pipeline for each job posting is:
1. **Fetch** — a site-specific fetcher (Naukri/LinkedIn/Indeed) drives a SeleniumBase Pure CDP browser, collects listing URLs, and fetches each detail page's raw HTML.
2. **Extract** — `extractor.py` strips the JD body to plain text via selectolax, then calls Crawl4AI's `LLMExtractionStrategy` with `raw://` to push the text through an LLM (OpenRouter or Ollama). Returns a validated `LLMExtractedFields` Pydantic model.
3. **HTML overrides** — deterministic fields (title, company, location) extracted by CSS selector in `apply_html_overrides()` are applied on top of LLM output. These win over the LLM because structured page headers are more reliable than JD prose.
4. **Normalize** — `normalizer.py` deduplicates and lowercases skills, strips ", India" suffixes from locations.
5. **Write** — `storage.py` writes a `data/jobs/{site}/{job_id}.md` file with YAML frontmatter + markdown body. Skips if `job_id` (SHA-1 of the URL) already exists.

Key source files:
- `src/job_scraper/schema.py` — `LLMExtractedFields` (LLM output) and `JobPosting` (full record with metadata). `LLMExtractedFields` is intentionally separate so the LLM is never asked about `job_id`, `source`, or `scraped_at`.
- `src/job_scraper/extractor.py` — `JobExtractor` singleton; `apply_html_overrides()` pattern.
- `src/job_scraper/llm.py` — `build_llm_config()` reads `LLM_PROVIDER`, `OPENROUTER_API_KEY`, `LLM_MODEL` env vars. Supports `openrouter` (default) and `ollama`.
- `src/job_scraper/fetchers/browser_context.py` — `ContextVar`-backed browser session shared across fetchers. Orchestrator calls `open_pure_cdp_browser` → `browser_session(sb, config)` → all fetcher calls inside share that browser instance.
- `src/job_scraper/fetchers/browser.py` — shared SeleniumBase Pure CDP helpers.
- `src/job_scraper/utils/seleniumbase_compat.py` — `open_pure_cdp_browser` / `close_pure_cdp_browser`.

## CLI vs. single-file scripts

**Use the module CLI** (`python -m job_scraper`) for end-to-end runs using all configured queries for a site.

**Use the single-file scripts** when you need per-company iteration:
- The CLI sends all `company_ids` as a single `f_C` filter — LinkedIn's ranking can bury companies past `--max`.
- `scripts/scrape_linkedin_per_company.py` and `scripts/scrape_naukri_search.py` loop one company at a time; `--max` means max jobs per query per company. Use `--start-company` / `--limit-companies` to resume.

## Config files

`config/{site}.yaml` controls queries, CSS selectors, anti-detection knobs (delays, headless, proxy), and company filters. Key notes:
- LinkedIn: `company_ids` are LinkedIn numeric IDs, used in the `f_C` URL filter.
- Indeed: `company_names` are expanded into per-query `company:` search operators by `_expand_company_queries()`.
- Naukri: no native company filter — embed company name in query text.
- Selector changes in YAML take effect immediately; CSS selectors are used only for navigation, JD container selection, and top-card field overrides. All field extraction goes through the LLM.

## LLM extraction rules

- The LLM instruction (in `extractor.py`) requires `null` for any field not explicitly in the JD. Do not change it to infer.
- `job_id` is SHA-1 of the source URL — changing the URL changes the ID and causes a duplicate file.
- `apply_html_overrides` runs after LLM extraction and overwrites `title`, `company`, `location` from CSS selectors when present. LLM values for these fields serve as fallback only.

## Anti-detection notes

- Naukri and LinkedIn run with `headless: false` in their configs. Do not change to `true` without testing.
- LinkedIn uses slow pacing (`min_delay_seconds: 4`, `max_delay_seconds: 10`). Start with `--max 10` to test session tolerance.
- Set `LINKEDIN_PROXY_URL` / `INDEED_PROXY_URL` / `NAUKRI_PROXY_URL` to a residential proxy for sustained runs.

## Apple Silicon gotcha

`SessionNotCreatedException: cannot connect to chrome at 127.0.0.1:9222` is usually a CPU architecture mismatch (x86_64 `uc_driver` under arm64 Python), not a URL problem. Run `experiments/sb_chrome_diagnostic.py` to diagnose. The production fetchers and `experiments/selenium_base_test.py` use Pure CDP with Chrome for Testing, which avoids the UC startup path.
