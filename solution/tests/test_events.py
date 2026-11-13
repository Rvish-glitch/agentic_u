"""Unit tests for event ingestion, deduplication, orphan linking, and override resolution."""

import json
import sqlite3
import pytest
from pathlib import Path
from solution.db import init_db, sync_disposition_catalog
from solution.ingest_events import ingest_event_file
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


def test_event_malformed_and_duplicate(tmp_path, test_db, registry):
    sync_disposition_catalog(test_db, registry.dispositions)
    jsonl_file = tmp_path / "events.jsonl"

    lines = [
        "not json content",  # 1. malformed
        json.dumps({"event_type": "call.initiated"}),  # 2. malformed (missing event_id)
        json.dumps({"event_id": "", "event_type": "call.initiated"}),  # 3. malformed (empty event_id)
        json.dumps({  # 4. valid event
            "event_id": "evt_001",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "call_001",
            "occurred_at": "2026-10-01T10:00:00Z",
            "phone": "+919876543210",
        }),
        json.dumps({  # 5. duplicate event_id
            "event_id": "evt_001",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "call_001",
            "occurred_at": "2026-10-01T10:00:00Z",
            "phone": "+919876543210",
        }),
    ]

    with open(jsonl_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    stats = ingest_event_file(test_db, jsonl_file, registry, set(), {}, {})
    assert stats["events_total"] == 5
    assert stats["malformed_events"] == 3
    assert stats["duplicate_events_dropped"] == 1


def test_cross_drop_orphan_event_resolution(tmp_path, test_db, registry):
    """An event arrives before its lead.
    In drop 1: event is orphaned.
    In drop 2: lead arrives -> event becomes linked!
    """
    sync_disposition_catalog(test_db, registry.dispositions)
    wf = registry.get_workflow("motor_renewal")

    # Step 1: Ingest event first (no lead exists yet)
    jsonl_file = tmp_path / "events_drop1.jsonl"
    event_data = {
        "event_id": "evt_001",
        "event_type": "call.initiated",
        "workflow_id": "motor_renewal",
        "call_id": "call_001",
        "occurred_at": "2026-10-01T10:00:00Z",
        "phone": "+919876543210",
    }
    with open(jsonl_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(event_data) + "\n")

    stats = ingest_event_file(test_db, jsonl_file, registry, set(), {}, {})
    assert stats["events_total"] == 1

    # Check event status in DB
    cur = test_db.execute("SELECT status, lead_id FROM interaction_ledger WHERE event_id = 'evt_001'")
    row = cur.fetchone()
    assert row["status"] == "orphan"
    assert row["lead_id"] is None

    # Step 2: Ingest lead in drop 2
    csv_file = tmp_path / "batch_003.csv"
    lead_rows = [
        "customer_name,phone,policy_number",
        "Aarav,+919876543210,MOT/2026/100",
    ]
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lead_rows) + "\n")

    ingest_lead_file(test_db, csv_file, wf)

    # Re-check event status in DB -> now linked!
    cur = test_db.execute("SELECT status, lead_id FROM interaction_ledger WHERE event_id = 'evt_001'")
    row = cur.fetchone()
    assert row["status"] == "linked"
    assert row["lead_id"] is not None


def test_disposition_override_resolution(tmp_path, test_db, registry):
    """disposition.override has no phone, links via call_id to lead."""
    sync_disposition_catalog(test_db, registry.dispositions)
    wf = registry.get_workflow("motor_renewal")

    # Ingest lead
    csv_file = tmp_path / "batch_001.csv"
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("customer_name,phone,policy_number\nAarav,+919876543210,MOT/2026/100\n")
    ingest_lead_file(test_db, csv_file, wf)

    # Ingest call.initiated, call.connected, disposition.set, disposition.override
    jsonl_file = tmp_path / "events.jsonl"
    events = [
        {"event_id": "e1", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543210"},
        {"event_id": "e2", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:00:10", "phone": "9876543210"},
        {"event_id": "e3", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:05:00", "phone": "9876543210", "payload": {"disposition_code": "renewed"}},
        {"event_id": "e4", "event_type": "disposition.override", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-02 12:00:00", "payload": {"disposition_code": "not_interested", "overridden_by": "supervisor_1", "reason": "qa_review"}},
    ]
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    leads_cache = {
        (row[0], row[1]): row[2]
        for row in test_db.execute("SELECT workflow_id, phone_hash, id FROM leads")
    }
    ingest_event_file(test_db, jsonl_file, registry, set(), leads_cache, {})

    # Check override event
    cur = test_db.execute("SELECT status, lead_id FROM interaction_ledger WHERE event_id = 'e4'")
    row = cur.fetchone()
    assert row["status"] == "linked"
    assert row["lead_id"] is not None

