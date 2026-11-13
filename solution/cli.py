"""Command-line interface for the data ingestion and reporting pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.config import DEFAULT_DB_PATH, DEFAULT_REPORTS_DIR, DEFAULT_SCHEMA_DIR
from solution.db import db_transaction, init_db, sync_disposition_catalog
from solution.ingest_events import ingest_events_from_directory
from solution.ingest_leads import ingest_leads_from_directory
from solution.reports import generate_reports
from solution.schema_loader import SchemaRegistry


def run_ingest(drop_path: Path, db_path: Path, schema_dir: Path) -> None:
    """Run ingestion for a single drop directory containing leads/ and events/."""
    if not drop_path.exists():
        print(f"Error: Drop path does not exist: {drop_path}", file=sys.stderr)
        sys.exit(1)

    init_db(db_path)
    registry = SchemaRegistry(schema_dir)

    with db_transaction(db_path) as conn:
        sync_disposition_catalog(conn, registry.dispositions)

        # 1. Ingest leads first
        leads_dir = drop_path / "leads"
        if leads_dir.exists():
            print(f"Ingesting leads from {leads_dir}...")
            ingest_leads_from_directory(conn, leads_dir, registry)

        # 2. Ingest events
        events_dir = drop_path / "events"
        if events_dir.exists():
            print(f"Ingesting events from {events_dir}...")
            ingest_events_from_directory(conn, events_dir, registry)

    print(f"Successfully processed drop: {drop_path}")


def run_report(out_dir: Path, db_path: Path) -> None:
    """Generate all five report files into the destination directory."""
    if not db_path.exists():
        print(f"Error: Database file does not exist: {db_path}", file=sys.stderr)
        sys.exit(1)

    init_db(db_path)
    with db_transaction(db_path) as conn:
        print(f"Generating reports into {out_dir}...")
        generate_reports(conn, out_dir)

    print(f"Reports successfully written to: {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agentic Universe - Interaction Ledger & Reporting Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(DEFAULT_DB_PATH),
        help="Path to the SQLite database file",
    )
    parser.add_argument(
        "--schema-dir",
        type=Path,
        default=Path(DEFAULT_SCHEMA_DIR),
        help="Path to the schema directory containing JSON schemas and dispositions.csv",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a data drop directory")
    ingest_parser.add_argument(
        "drop_path",
        type=Path,
        help="Path to the drop folder (e.g. data/drop_1)",
    )

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate all reports")
    report_parser.add_argument(
        "--out",
        dest="out_dir",
        type=Path,
        default=Path(DEFAULT_REPORTS_DIR),
        help="Output directory for generated CSV and JSON reports",
    )

    args = parser.parse_args()

    if args.command == "ingest":
        run_ingest(args.drop_path, args.db, args.schema_dir)
    elif args.command == "report":
        run_report(args.out_dir, args.db)


if __name__ == "__main__":
    main()

