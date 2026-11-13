"""Unit tests for lead ingestion, validation, rejection order, deduplication and merging."""

import csv
import json
import sqlite3
import pytest
from pathlib import Path
from solution.db import init_db
from solution.ingest_leads import ingest_lead_file
from solution.schema_loader import SchemaRegistry


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test.db"
    init_db(db_file)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def registry():
    schema_path = Path("assignment-a-data/schemas")
    return SchemaRegistry(schema_path)


def test_schema_order_rejection(tmp_path, test_db, registry):
    """Walk fields in schema order:
    1. customer_name (required)
    2. phone (required)
    3. policy_number (required)
    """
    wf = registry.get_workflow("motor_renewal")
    csv_file = tmp_path / "batch_001.csv"

    # Row 1: missing customer_name AND bad phone -> must reject with missing_required:customer_name
    # Row 2: valid customer_name, bad phone -> must reject with invalid_phone
    # Row 3: valid customer_name, missing phone -> must reject with missing_required:phone
    # Row 4: valid customer_name, valid phone, missing policy_number -> must reject with missing_required:policy_number
    # Row 5: blank row -> skipped, not counted
    # Row 6: completely valid row
    rows = [
        ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"],
        ["", "bad_phone", "MOT/2026/001", "2026-10-01", "5000", "en"],
        ["Rahul", "12345", "MOT/2026/002", "2026-10-01", "5000", "en"],
        ["Rahul", "", "MOT/2026/003", "2026-10-01", "5000", "en"],
        ["Rahul", "9876543210", "", "2026-10-01", "5000", "en"],
        ["", "", "", "", "", ""],  # Blank row
        ["Aarav", "9876543210", "MOT/2026/005", "2026-10-01", "5000", "en"],
    ]

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    stats = ingest_lead_file(test_db, csv_file, wf)

    assert stats["rows_total"] == 5  # 5 non-blank rows (blank row skipped)
    assert stats["rows_rejected"] == 4
    assert stats["rejections_by_reason"] == {
        "missing_required:customer_name": 1,
        "invalid_phone": 1,
        "missing_required:phone": 1,
        "missing_required:policy_number": 1,
    }

    # Verify rejections table
    cur = test_db.execute("SELECT row_number, reason FROM lead_rejections ORDER BY row_number")
    rej_rows = cur.fetchall()
    assert len(rej_rows) == 4
    assert rej_rows[0]["row_number"] == 1
    assert rej_rows[0]["reason"] == "missing_required:customer_name"
    assert rej_rows[1]["row_number"] == 2
    assert rej_rows[1]["reason"] == "invalid_phone"
    assert rej_rows[2]["row_number"] == 3
    assert rej_rows[2]["reason"] == "missing_required:phone"
    assert rej_rows[3]["row_number"] == 4
    assert rej_rows[3]["reason"] == "missing_required:policy_number"


def test_optional_invalid_field_warnings_and_defaults(tmp_path, test_db, registry):
    wf = registry.get_workflow("motor_renewal")
    csv_file = tmp_path / "batch_001.csv"

    # Row 1: valid lead, but invalid date '31/02/2026' and invalid amount 'N/A' -> 2 warnings, fields become null
    # Row 2: valid lead with no preferred_language -> gets default 'en'
    rows = [
        ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"],
        ["Aarav", "9876543210", "MOT/2026/001", "31/02/2026", "N/A", "hi"],
        ["Meera", "9876543211", "MOT/2026/002", "2026-10-01", "5000", ""],
    ]

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    stats = ingest_lead_file(test_db, csv_file, wf)

    assert stats["rows_total"] == 2
    assert stats["rows_rejected"] == 0
    assert stats["field_warnings"] == 2

    # Check Aarav's attributes: policy_expiry and premium_amount should be None
    cur1 = test_db.execute("SELECT attributes_json FROM leads WHERE external_ref = 'MOT/2026/001'")
    attrs1 = json.loads(cur1.fetchone()["attributes_json"])
    assert attrs1.get("policy_expiry") is None
    assert attrs1.get("premium_amount") is None
    assert attrs1.get("preferred_language") == "hi"

    # Check Meera's default language
    cur2 = test_db.execute("SELECT attributes_json FROM leads WHERE external_ref = 'MOT/2026/002'")
    attrs2 = json.loads(cur2.fetchone()["attributes_json"])
    assert attrs2.get("preferred_language") == "en"


def test_lead_deduplication_and_merging(tmp_path, test_db, registry):
    wf = registry.get_workflow("motor_renewal")
    csv1 = tmp_path / "batch_001.csv"
    csv2 = tmp_path / "batch_002.csv"

    # First batch: creates lead
    rows1 = [
        ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"],
        ["Aarav", "9876543210", "MOT/2026/001", "2026-10-01", "5000", "en"],
    ]
    with open(csv1, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows1)

    # Second batch: same phone, but updated amount, null language, updated policy_number
    rows2 = [
        ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"],
        ["Aarav Sharma", "9876543210", "MOT/2026/001-UPDATED", "", "6500", ""],
    ]
    with open(csv2, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows2)

    stats1 = ingest_lead_file(test_db, csv1, wf)
    assert stats1["duplicates_merged"] == 0

    stats2 = ingest_lead_file(test_db, csv2, wf)
    assert stats2["duplicates_merged"] == 1

    cur = test_db.execute("SELECT batch_id, external_ref, attributes_json FROM leads")
    lead = cur.fetchone()
    # Permanent batch_id from first file
    assert lead["batch_id"] == "batch_001"
    # Overwritten reference
    assert lead["external_ref"] == "MOT/2026/001-UPDATED"

    attrs = json.loads(lead["attributes_json"])
    # Overwritten premium amount
    assert attrs["premium_amount"] == 6500.0
    # Retained policy_expiry because duplicate had null
    assert attrs["policy_expiry"] == "2026-10-01"
    # Retained preferred_language because duplicate had null
    assert attrs["preferred_language"] == "en"

