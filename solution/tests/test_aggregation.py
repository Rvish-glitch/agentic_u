"""Unit tests for SQL aggregations: bucket logic, effective disposition, and report correctness."""

import json
import sqlite3
import pytest
from pathlib import Path
from solution.aggregator import (
    compute_batch_summary,
    compute_daily_overview,
    compute_data_quality,
    compute_lead_report,
)
from solution.db import init_db, sync_disposition_catalog
from solution.ingest_events import ingest_event_file
from solution.ingest_leads import ingest_lead_file
from solution.schema_loader import SchemaRegistry


@pytest.fixture
def populated_db(tmp_path):
    """Fixture creating 4 leads demonstrating all 4 bucket conditions and effective disposition."""
    db_file = tmp_path / "test_agg.db"
    init_db(db_file)
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    registry = SchemaRegistry(Path("assignment-a-data/schemas"))
    sync_disposition_catalog(conn, registry.dispositions)
    wf = registry.get_workflow("motor_renewal")

    # Ingest 4 leads
    csv_file = tmp_path / "batch_001.csv"
    leads = [
        "customer_name,phone,policy_number",
        "Lead Zero,9876543210,MOT/2026/000",   # 0 calls -> yet_to_reach_out
        "Lead Term,9876543211,MOT/2026/001",   # 1 call renewed (terminal) -> completed
        "Lead Fail,9876543212,MOT/2026/002",   # 2 calls both failed -> in_progress_telephony_failed
        "Lead Dial,9876543213,MOT/2026/003",   # 1 call connected callback_requested (non-terminal) -> in_progress_dialed
    ]
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("\n".join(leads) + "\n")
    ingest_lead_file(conn, csv_file, wf)

    # Ingest calls and events
    events = [
        # Call for Lead Term: initiated, connected, disposition.set renewed
        {"event_id": "e1", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:00:00", "phone": "9876543211"},
        {"event_id": "e2", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:00:10", "phone": "9876543211"},
        {"event_id": "e3", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:05:00", "phone": "9876543211", "payload": {"disposition_code": "renewed"}},

        # Calls for Lead Fail: two calls, both telephony_failed
        {"event_id": "e4", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c2", "occurred_at": "2026-10-01 11:00:00", "phone": "9876543212"},
        {"event_id": "e5", "event_type": "call.telephony_failed", "workflow_id": "motor_renewal", "call_id": "c2", "occurred_at": "2026-10-01 11:00:05", "phone": "9876543212", "payload": {"reason": "carrier_rejected"}},
        {"event_id": "e6", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c3", "occurred_at": "2026-10-02 11:00:00", "phone": "9876543212"},
        {"event_id": "e7", "event_type": "call.telephony_failed", "workflow_id": "motor_renewal", "call_id": "c3", "occurred_at": "2026-10-02 11:00:05", "phone": "9876543212", "payload": {"reason": "sip_503"}},

        # Call for Lead Dial: initiated, connected, callback_requested (non-terminal)
        {"event_id": "e8", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-01 14:00:00", "phone": "9876543213"},
        {"event_id": "e9", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-01 14:00:15", "phone": "9876543213"},
        {"event_id": "e10", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c4", "occurred_at": "2026-10-01 14:05:00", "phone": "9876543213", "payload": {"disposition_code": "callback_requested"}},
    ]
    jsonl_file = tmp_path / "events.jsonl"
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")

    leads_cache = {
        (row[0], row[1]): row[2]
        for row in conn.execute("SELECT workflow_id, phone_hash, id FROM leads")
    }
    ingest_event_file(conn, jsonl_file, registry, set(), leads_cache, {})

    yield conn
    conn.close()


def test_bucket_derivations_and_batch_summary(populated_db):
    batch_rows = compute_batch_summary(populated_db)
    assert len(batch_rows) == 1
    b = batch_rows[0]
    assert b["total_leads"] == 4
    assert b["yet_to_reach_out"] == 1
    assert b["completed"] == 1
    assert b["in_progress_telephony_failed"] == 1
    assert b["in_progress_dialed"] == 1


def test_lead_report_correctness(populated_db):
    leads = compute_lead_report(populated_db)
    lead_by_ref = {l["external_ref"]: l for l in leads}

    # Lead Zero: no calls
    lz = lead_by_ref["MOT/2026/000"]
    assert lz["bucket"] == "yet_to_reach_out"
    assert lz["total_calls"] == 0
    assert lz["connected_calls"] == 0
    assert lz["last_call_at"] == ""
    assert lz["current_disposition"] == ""
    assert lz["ever_success"] == "false"

    # Lead Term: 1 call, connected, renewed
    lt = lead_by_ref["MOT/2026/001"]
    assert lt["bucket"] == "completed"
    assert lt["total_calls"] == 1
    assert lt["connected_calls"] == 1
    assert lt["last_call_at"] == "2026-10-01T10:00:00+05:30"
    assert lt["current_disposition"] == "renewed"
    assert lt["ever_success"] == "true"

    # Lead Fail: 2 calls, 0 connected
    lf = lead_by_ref["MOT/2026/002"]
    assert lf["bucket"] == "in_progress_telephony_failed"
    assert lf["total_calls"] == 2
    assert lf["connected_calls"] == 0
    assert lf["ever_success"] == "false"

    # Lead Dial: 1 call, connected, callback_requested
    ld = lead_by_ref["MOT/2026/003"]
    assert ld["bucket"] == "in_progress_dialed"
    assert ld["total_calls"] == 1
    assert ld["connected_calls"] == 1
    assert ld["current_disposition"] == "callback_requested"
    assert ld["ever_success"] == "false"


def test_daily_overview(populated_db):
    daily = compute_daily_overview(populated_db)
    daily_by_date = {d["date"]: d for d in daily}

    # On 2026-10-01: c1, c2, c4 (3 calls, 2 connected) -> rate = 2/3 = 0.6667
    # Distinct success leads: Lead Term (1)
    d1 = daily_by_date["2026-10-01"]
    assert d1["total_calls"] == 3
    assert d1["connected_calls"] == 2
    assert d1["connectivity_rate"] == "0.6667"
    assert d1["success_leads"] == 1

    # On 2026-10-02: c3 (1 call, 0 connected) -> rate = 0.0000
    d2 = daily_by_date["2026-10-02"]
    assert d2["total_calls"] == 1
    assert d2["connected_calls"] == 0
    assert d2["connectivity_rate"] == "0.0000"
    assert d2["success_leads"] == 0

