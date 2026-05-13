from __future__ import annotations

import asyncio

from scripts import scrape_linkedin_per_company as script


def test_scrape_company_query_runs_fetcher_outside_active_event_loop(monkeypatch) -> None:
    observed: dict[str, bool] = {}

    def fake_fetch_linkedin(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            observed["inside_active_loop"] = False
        else:
            observed["inside_active_loop"] = True
        return []

    monkeypatch.setattr(script, "fetch_linkedin", fake_fetch_linkedin)

    written = asyncio.run(
        script.scrape_company_query(
            company=script.LinkedInCompany(company_id="1586", name="Amazon"),
            query="software engineer",
            location="India",
            max_jobs=4,
            scrolls_per_page=1,
            site_config={"selectors": {"jd_body": "body"}},
            extractor=object(),
            semaphore=asyncio.Semaphore(1),
            seen_ids=set(),
        )
    )

    assert written == 0
    assert observed["inside_active_loop"] is False
