"""End-to-end integration and idempotency tests for the complete pipeline."""

import filecmp
import json
import sqlite3
import pytest
from pathlib import Path
from solution.cli import run_ingest, run_report


def test_end_to_end_pipeline_and_idempotency(tmp_path):
    data_dir = Path("data")
    if not data_dir.exists():
        pytest.skip("data/ directory not present")

    db_path = tmp_path / "test_pipeline.db"
    schema_dir = Path("assignment-a-data/schemas")
    reports_run1 = tmp_path / "reports_run1"
    reports_run2 = tmp_path / "reports_run2"

    # Step 1: Ingest drop_1
    run_ingest(data_dir / "drop_1", db_path, schema_dir)

    # Step 2: Ingest drop_2
    run_ingest(data_dir / "drop_2", db_path, schema_dir)

    # Step 3: Generate reports
    run_report(reports_run1, db_path)

    # Verify all 5 files exist
    assert (reports_run1 / "batch_summary.csv").exists()
    assert (reports_run1 / "daily_overview.csv").exists()
    assert (reports_run1 / "lead_report.csv").exists()
    assert (reports_run1 / "rejections.csv").exists()
    assert (reports_run1 / "data_quality.json").exists()

    # Step 4: Re-run ingestion on both drops (must change nothing!)
    run_ingest(data_dir / "drop_1", db_path, schema_dir)
    run_ingest(data_dir / "drop_2", db_path, schema_dir)

    # Step 5: Generate reports again
    run_report(reports_run2, db_path)

    # Step 6: Diff files to assert byte-for-byte identity
    for filename in ["batch_summary.csv", "daily_overview.csv", "lead_report.csv", "rejections.csv", "data_quality.json"]:
        file1 = reports_run1 / filename
        file2 = reports_run2 / filename
        assert filecmp.cmp(file1, file2, shallow=False), f"File {filename} differed on re-run!"

