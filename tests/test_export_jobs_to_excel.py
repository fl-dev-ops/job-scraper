from scripts import export_jobs_to_excel as script


def test_export_jobs_wraps_markdown_paths_with_tqdm(tmp_path, monkeypatch):
    input_dir = tmp_path / "jobs"
    input_dir.mkdir()
    (input_dir / "b.md").write_text("---\ntitle: B\n---\nBody", encoding="utf-8")
    (input_dir / "a.md").write_text("---\ntitle: A\n---\nBody", encoding="utf-8")

    tqdm_calls = []

    def fake_tqdm(iterable, **kwargs):
        paths = list(iterable)
        tqdm_calls.append((paths, kwargs))
        return paths

    monkeypatch.setattr(script, "tqdm", fake_tqdm)
    monkeypatch.setattr(script, "_write_xlsx", lambda records, output_path: None)
    monkeypatch.setattr(
        script,
        "_build_record",
        lambda path, **kwargs: script.JobRecord(
            title=path.stem,
            company="",
            role_type="",
            location="",
            role_summary="",
            key_responsibilities="",
            required_skills="",
            focus_rounds="",
            focus_round_pattern="",
            salary_min=None,
            salary_max=None,
            experience_min=None,
            experience_max=None,
        ),
    )

    count = script.export_jobs(
        input_dir,
        tmp_path / "jobs.xlsx",
        use_llm=False,
        model="x",
        limit=None,
    )

    assert count == 2
    assert tqdm_calls == [
        (
            [input_dir / "a.md", input_dir / "b.md"],
            {"desc": "Exporting jobs", "unit": "job"},
        )
    ]
