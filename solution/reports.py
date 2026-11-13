"""Report generator: generates and exports the five contract report files."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from solution.aggregator import (
    compute_batch_summary,
    compute_daily_overview,
    compute_data_quality,
    compute_lead_report,
    compute_rejections_report,
)


def write_csv(filepath: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """Write rows to a CSV file with exact headers and UTF-8 encoding."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def generate_reports(conn: sqlite3.Connection, out_dir: str | Path) -> None:
    """Generate all five report files according to the output contract."""
    from solution.aggregator import prepare_report_views
    prepare_report_views(conn)

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. batch_summary.csv
    batch_rows = compute_batch_summary(conn)
    batch_fields = [
        "workflow_id",
        "batch_id",
        "total_leads",
        "yet_to_reach_out",
        "in_progress_telephony_failed",
        "in_progress_dialed",
        "completed",
    ]
    write_csv(out_path / "batch_summary.csv", batch_rows, batch_fields)

    # 2. daily_overview.csv
    daily_rows = compute_daily_overview(conn)
    daily_fields = [
        "workflow_id",
        "date",
        "total_calls",
        "connected_calls",
        "connectivity_rate",
        "success_leads",
    ]
    write_csv(out_path / "daily_overview.csv", daily_rows, daily_fields)

    # 3. lead_report.csv
    lead_rows = compute_lead_report(conn)
    lead_fields = [
        "workflow_id",
        "external_ref",
        "batch_id",
        "phone_masked",
        "bucket",
        "total_calls",
        "connected_calls",
        "last_call_at",
        "current_disposition",
        "ever_success",
    ]
    write_csv(out_path / "lead_report.csv", lead_rows, lead_fields)

    # 4. rejections.csv
    rejection_rows = compute_rejections_report(conn)
    rejection_fields = ["workflow_id", "batch_id", "row_number", "reason"]
    write_csv(out_path / "rejections.csv", rejection_rows, rejection_fields)

    # 5. data_quality.json
    dq_data = compute_data_quality(conn)
    dq_path = out_path / "data_quality.json"
    with open(dq_path, "w", encoding="utf-8") as f:
        json.dump(dq_data, f, indent=2)
