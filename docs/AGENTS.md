# Agent Notes

## Company-Level Searching

Use the repo CLI for broad configured scraping:

```bash
uv run python -m job_scraper linkedin --location India --max 30
uv run python -m job_scraper naukri --location India --max 30
uv run python -m job_scraper all --location India --max 30
```

The CLI path is the normal application entrypoint. It is best for running a
site from its config, checking end-to-end behavior, and comparing the shared
fetch/extract/write flow across sites.

The CLI is query-level, not company-level. If LinkedIn has 10 configured
companies, 2 queries, and the command uses `--max 10`, the CLI collects at most:

```text
2 queries x 10 jobs = 20 jobs
```

It does not collect:

```text
10 companies x 2 queries x 10 jobs = 200 jobs
```

For LinkedIn CLI runs, all configured `company_ids` are sent as one combined
`f_C` filter. LinkedIn controls the ranking, so one company can dominate the
first 10 results for a query while jobs from other configured companies remain
below the collection limit.

```text
Query: backend developer
Companies: A, B, C, D, E, F, G, H, I, J
--max 10

LinkedIn result order:
1-7   Company A
8-10  Company B
11-15 Company C

CLI collects only results 1-10. Company C and later companies are missed.
```

Use the single-file company runners for company-level searches:

```bash
uv run python scripts/scrape_linkedin_per_company.py --max 5
uv run python scripts/scrape_naukri_search.py --location India --max 1
```

These scripts intentionally run one configured company at a time:

- LinkedIn reads `company_ids` from `config/linkedin.yaml`.
- Naukri reads `company_names` from `config/naukri.yaml`.
- `--max` means max jobs per query per company, not total jobs for the run.
- `--start-company` and `--limit-companies` are for resuming or sampling the
  company list without editing config.

Do not replace the single-file runners with the module CLI when the task is to
debug one company, resume from a company index, or explain company-level job
counts. The CLI and the single-file scripts share the same extraction/storage
layers, but they have different execution intent:

```text
CLI:
  site/query-level run from app entrypoint

single-file runner:
  company/query-level run for targeted search or debugging
```
