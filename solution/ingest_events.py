"""Event ingestion pipeline: streaming parser, deduplication, orphan handling, and ledger insertion."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from solution.config import IST
from solution.crypto import get_phone_hash, mask_phone
from solution.db import increment_stat
from solution.ingest_leads import compute_file_hash, reconcile_orphaned_events
from solution.normalizers import format_ist_iso, parse_phone, parse_timestamp
from solution.schema_loader import SchemaRegistry


def ingest_event_file(
    conn: sqlite3.Connection,
    filepath: Path,
    registry: SchemaRegistry,
    seen_event_ids: set[str],
    leads_cache: dict[tuple[str, str], int],
    call_to_lead_cache: dict[str, int],
) -> dict[str, int]:
    """Ingest a single JSONL event file with high-performance batching and in-memory caches."""
    events_total = 0
    malformed_events = 0
    duplicate_events_dropped = 0

    batch_records = []
    BATCH_SIZE = 10000

    # Valid disposition set for fast O(1) checks
    valid_disp_codes = set(registry.dispositions.keys())

    with open(filepath, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            events_total += 1

            # 1. Malformed check
            try:
                ev = json.loads(line)
            except Exception:
                malformed_events += 1
                continue

            if not isinstance(ev, dict):
                malformed_events += 1
                continue

            event_id = ev.get("event_id")
            if not event_id or not isinstance(event_id, str) or not event_id.strip():
                malformed_events += 1
                continue

            event_id = event_id.strip()

            # 2. Duplicate event_id check
            if event_id in seen_event_ids:
                duplicate_events_dropped += 1
                continue

            seen_event_ids.add(event_id)

            # Extract fields
            event_type = ev.get("event_type", "").strip()
            workflow_id = ev.get("workflow_id", "").strip()
            call_id = ev.get("call_id", "").strip()
            occurred_at_str = ev.get("occurred_at", "")
            payload = ev.get("payload", {})
            raw_phone = ev.get("phone")

            if not isinstance(payload, dict):
                payload = {}

            # Parse timestamp to aware IST
            try:
                dt_ist = parse_timestamp(occurred_at_str)
                occurred_at_ist = format_ist_iso(dt_ist)
                call_date_ist = dt_ist.strftime("%Y-%m-%d")
            except Exception:
                malformed_events += 1
                continue

            # Phone normalisation & hashing
            phone_hash = None
            phone_masked = None
            if raw_phone:
                canonical = parse_phone(str(raw_phone))
                if canonical:
                    phone_hash = get_phone_hash(canonical)
                    phone_masked = mask_phone(canonical)

            # 3. Linking to lead
            lead_id = None
            if phone_hash:
                lead_id = leads_cache.get((workflow_id, phone_hash))
                if lead_id and call_id:
                    call_to_lead_cache[call_id] = lead_id
            elif event_type == "disposition.override" and call_id:
                lead_id = call_to_lead_cache.get(call_id)

            # 4. Determine status
            if lead_id is None:
                status = "orphan"
            else:
                if event_type.startswith("disposition."):
                    disp_code = str(payload.get("disposition_code", "")).strip()
                    if (workflow_id, disp_code) in valid_disp_codes:
                        status = "linked"
                    else:
                        status = "unknown_disposition"
                else:
                    status = "linked"

            batch_records.append((
                event_id,
                event_type,
                workflow_id,
                call_id,
                occurred_at_ist,
                call_date_ist,
                phone_hash,
                phone_masked,
                lead_id,
                json.dumps(payload, ensure_ascii=False),
                status,
            ))

            if len(batch_records) >= BATCH_SIZE:
                conn.executemany("""
                    INSERT INTO interaction_ledger (
                        event_id, event_type, workflow_id, call_id,
                        occurred_at_ist, call_date_ist, phone_hash,
                        phone_masked, lead_id, payload_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch_records)
                batch_records.clear()

    if batch_records:
        conn.executemany("""
            INSERT INTO interaction_ledger (
                event_id, event_type, workflow_id, call_id,
                occurred_at_ist, call_date_ist, phone_hash,
                phone_masked, lead_id, payload_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch_records)

    return {
        "events_total": events_total,
        "malformed_events": malformed_events,
        "duplicate_events_dropped": duplicate_events_dropped,
    }


def ingest_events_from_directory(
    conn: sqlite3.Connection,
    events_dir: Path,
    registry: SchemaRegistry,
) -> None:
    """Scan and ingest all event JSONL files under a drop's events/ directory."""
    if not events_dir.exists():
        return

    # Warm in-memory caches for fast processing
    seen_event_ids: set[str] = {
        row[0] for row in conn.execute("SELECT event_id FROM interaction_ledger")
    }

    leads_cache: dict[tuple[str, str], int] = {
        (row[0], row[1]): row[2]
        for row in conn.execute("SELECT workflow_id, phone_hash, id FROM leads")
    }

    call_to_lead_cache: dict[str, int] = {
        row[0]: row[1]
        for row in conn.execute("SELECT call_id, lead_id FROM interaction_ledger WHERE lead_id IS NOT NULL")
    }

    for jsonl_file in sorted(events_dir.glob("*.jsonl")):
        file_key = str(jsonl_file.resolve())
        file_hash = compute_file_hash(jsonl_file)

        cur = conn.execute("SELECT file_hash FROM ingested_files WHERE file_path = ?", (file_key,))
        existing = cur.fetchone()
        if existing and existing["file_hash"] == file_hash:
            continue

        stats = ingest_event_file(
            conn, jsonl_file, registry, seen_event_ids, leads_cache, call_to_lead_cache
        )

        increment_stat(conn, "events_total", stats["events_total"])
        increment_stat(conn, "malformed_events", stats["malformed_events"])
        increment_stat(conn, "duplicate_events_dropped", stats["duplicate_events_dropped"])

        conn.execute("""
            INSERT OR REPLACE INTO ingested_files (file_path, file_type, file_hash, ingested_at)
            VALUES (?, 'event', ?, ?)
        """, (file_key, file_hash, datetime.now(IST).isoformat()))

    # Reconcile orphaned events once at the end of the event directory ingestion
    reconcile_orphaned_events(conn)
