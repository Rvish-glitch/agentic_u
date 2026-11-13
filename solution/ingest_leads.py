"""Lead ingestion pipeline: header mapping, validation, rejection, deduplication & merging."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from solution.config import IST
from solution.crypto import encrypt_pii, get_phone_hash, mask_phone
from solution.db import increment_stat
from solution.normalizers import (
    parse_lead_date,
    parse_lead_decimal,
    parse_lead_enum,
    parse_phone,
)
from solution.schema_loader import SchemaRegistry, WorkflowSchema


def compute_file_hash(filepath: Path) -> str:
    """Compute SHA256 hash of a file for idempotency tracking."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def reconcile_orphaned_events(conn: sqlite3.Connection) -> None:
    """Reconcile previously orphaned events with newly ingested leads."""
    # Check if there are any orphaned events at all before executing updates
    cur = conn.execute("SELECT 1 FROM interaction_ledger WHERE lead_id IS NULL LIMIT 1")
    if not cur.fetchone():
        return

    # 1. Reconcile events with phone_hash
    conn.execute("""
        UPDATE interaction_ledger
        SET lead_id = leads.id,
            status = CASE
                WHEN interaction_ledger.event_type LIKE 'disposition.%'
                     AND json_extract(interaction_ledger.payload_json, '$.disposition_code') NOT IN (
                         SELECT code FROM disposition_catalog WHERE workflow_id = interaction_ledger.workflow_id
                     ) THEN 'unknown_disposition'
                ELSE 'linked'
            END
        FROM leads
        WHERE interaction_ledger.lead_id IS NULL
          AND interaction_ledger.phone_hash IS NOT NULL
          AND interaction_ledger.workflow_id = leads.workflow_id
          AND interaction_ledger.phone_hash = leads.phone_hash
    """)

    # 2. Reconcile disposition.override events via indexed call_id lookup
    conn.execute("""
        UPDATE interaction_ledger
        SET lead_id = (
            SELECT other.lead_id 
            FROM interaction_ledger other 
            WHERE other.call_id = interaction_ledger.call_id 
              AND other.lead_id IS NOT NULL 
            LIMIT 1
        ),
        status = CASE
            WHEN json_extract(interaction_ledger.payload_json, '$.disposition_code') NOT IN (
                SELECT code FROM disposition_catalog WHERE workflow_id = interaction_ledger.workflow_id
            ) THEN 'unknown_disposition'
            ELSE 'linked'
        END
        WHERE interaction_ledger.lead_id IS NULL
          AND interaction_ledger.event_type = 'disposition.override'
          AND EXISTS (
              SELECT 1 FROM interaction_ledger other 
              WHERE other.call_id = interaction_ledger.call_id 
                AND other.lead_id IS NOT NULL
          )
    """)


def ingest_lead_file(
    conn: sqlite3.Connection,
    filepath: Path,
    workflow_schema: WorkflowSchema,
    leads_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ingest a single lead CSV file with fast in-memory caching."""
    wf_id = workflow_schema.workflow_id
    batch_id = filepath.stem  # permanent file stem e.g. 'batch_001'

    # Register batch
    conn.execute("""
        INSERT OR IGNORE INTO batches (workflow_id, batch_id) VALUES (?, ?)
    """, (wf_id, batch_id))

    if leads_cache is None:
        leads_cache = {}
        cur = conn.execute("""
            SELECT id, phone_hash, customer_name_encrypted, external_ref, attributes_json
            FROM leads WHERE workflow_id = ?
        """, (wf_id,))
        for r in cur.fetchall():
            leads_cache[r["phone_hash"]] = {
                "id": r["id"],
                "customer_name_encrypted": r["customer_name_encrypted"],
                "external_ref": r["external_ref"],
                "attributes": json.loads(r["attributes_json"]),
            }

    rows_total = 0
    rows_rejected = 0
    rejections_by_reason: dict[str, int] = {}
    duplicates_merged = 0
    field_warnings = 0

    rejections_to_insert: list[tuple[str, str, int, str]] = []
    leads_to_insert: list[tuple] = []
    leads_to_update: list[tuple] = []

    # Read CSV
    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            raw_headers = next(reader)
        except StopIteration:
            return {
                "rows_total": 0,
                "rows_rejected": 0,
                "rejections_by_reason": {},
                "duplicates_merged": 0,
                "field_warnings": 0,
            }

        # Match headers
        col_to_field: dict[int, str] = {}
        for idx, h in enumerate(raw_headers):
            field_name = workflow_schema.match_header(h)
            if field_name:
                col_to_field[idx] = field_name

        data_row_num = 0
        now_ts = datetime.now(IST).isoformat()

        for raw_row in reader:
            if not raw_row or all(not cell.strip() for cell in raw_row):
                continue

            data_row_num += 1
            rows_total += 1

            row_raw_values: dict[str, str] = {}
            for col_idx, cell in enumerate(raw_row):
                if col_idx in col_to_field:
                    fname = col_to_field[col_idx]
                    trimmed = cell.strip()
                    if trimmed != "":
                        row_raw_values[fname] = trimmed

            # Walk fields in schema order
            rejected = False
            rejection_reason = ""
            row_warnings = 0
            validated_values: dict[str, Any] = {}

            for f_spec in workflow_schema.fields:
                fname = f_spec["name"]
                ftype = f_spec.get("type", "string")
                is_required = f_spec.get("required", False)
                raw_val = row_raw_values.get(fname)

                if is_required and raw_val is None:
                    rejected = True
                    rejection_reason = f"missing_required:{fname}"
                    break

                if ftype == "phone":
                    if raw_val is not None:
                        parsed_phone = parse_phone(raw_val)
                        if parsed_phone is None:
                            rejected = True
                            rejection_reason = "invalid_phone"
                            break
                        validated_values[fname] = parsed_phone
                    else:
                        validated_values[fname] = None

                elif ftype == "date":
                    if raw_val is not None:
                        parsed_date = parse_lead_date(raw_val)
                        if parsed_date is None:
                            row_warnings += 1
                            validated_values[fname] = None
                        else:
                            validated_values[fname] = parsed_date
                    else:
                        validated_values[fname] = None

                elif ftype == "decimal":
                    if raw_val is not None:
                        parsed_dec = parse_lead_decimal(raw_val)
                        if parsed_dec is None:
                            row_warnings += 1
                            validated_values[fname] = None
                        else:
                            validated_values[fname] = parsed_dec
                    else:
                        validated_values[fname] = None

                elif ftype == "enum":
                    allowed = f_spec.get("values", [])
                    if raw_val is not None:
                        parsed_enum = parse_lead_enum(raw_val, allowed)
                        if parsed_enum is None:
                            row_warnings += 1
                            validated_values[fname] = None
                        else:
                            validated_values[fname] = parsed_enum
                    else:
                        validated_values[fname] = None

                else:  # string
                    validated_values[fname] = raw_val

            if rejected:
                rows_rejected += 1
                rejections_by_reason[rejection_reason] = rejections_by_reason.get(rejection_reason, 0) + 1
                rejections_to_insert.append((wf_id, batch_id, data_row_num, rejection_reason))
                continue

            field_warnings += row_warnings
            canonical_phone = validated_values["phone"]
            phone_hash = get_phone_hash(canonical_phone)

            existing = leads_cache.get(phone_hash)

            if existing is None:
                # First valid row creates the lead
                for f_spec in workflow_schema.fields:
                    fname = f_spec["name"]
                    if validated_values.get(fname) is None and "default" in f_spec:
                        validated_values[fname] = f_spec["default"]

                cust_name = validated_values.get("customer_name", "")
                ext_ref = validated_values.get(workflow_schema.reference_field, "")
                attrs = {
                    k: v for k, v in validated_values.items()
                    if k not in ("customer_name", "phone", workflow_schema.reference_field)
                }

                c_name_enc = encrypt_pii(cust_name)
                leads_to_insert.append((
                    wf_id,
                    phone_hash,
                    mask_phone(canonical_phone),
                    encrypt_pii(canonical_phone),
                    c_name_enc,
                    ext_ref,
                    batch_id,
                    json.dumps(attrs),
                    now_ts,
                    now_ts,
                ))

                # Update in-memory cache placeholder (ID will be populated on bulk insert or is tracked)
                leads_cache[phone_hash] = {
                    "id": None,
                    "customer_name_encrypted": c_name_enc,
                    "external_ref": ext_ref,
                    "attributes": attrs,
                }
            else:
                # Duplicate row: merge non-null fields
                duplicates_merged += 1
                existing_attrs = dict(existing["attributes"])

                cust_name_enc = existing["customer_name_encrypted"]
                if validated_values.get("customer_name") is not None:
                    cust_name_enc = encrypt_pii(validated_values["customer_name"])

                ext_ref = existing["external_ref"]
                if validated_values.get(workflow_schema.reference_field) is not None:
                    ext_ref = validated_values[workflow_schema.reference_field]

                for k, v in validated_values.items():
                    if k not in ("customer_name", "phone", workflow_schema.reference_field):
                        if v is not None:
                            existing_attrs[k] = v

                existing["customer_name_encrypted"] = cust_name_enc
                existing["external_ref"] = ext_ref
                existing["attributes"] = existing_attrs

                leads_to_update.append((
                    cust_name_enc,
                    ext_ref,
                    json.dumps(existing_attrs),
                    now_ts,
                    wf_id,
                    phone_hash,
                ))

    # Perform batch inserts and updates
    if leads_to_insert:
        conn.executemany("""
            INSERT INTO leads (
                workflow_id, phone_hash, phone_masked, phone_encrypted,
                customer_name_encrypted, external_ref, batch_id,
                attributes_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, leads_to_insert)

    if leads_to_update:
        conn.executemany("""
            UPDATE leads
            SET customer_name_encrypted = ?,
                external_ref = ?,
                attributes_json = ?,
                updated_at = ?
            WHERE workflow_id = ? AND phone_hash = ?
        """, leads_to_update)

    if rejections_to_insert:
        conn.executemany("""
            INSERT OR REPLACE INTO lead_rejections (workflow_id, batch_id, row_number, reason)
            VALUES (?, ?, ?, ?)
        """, rejections_to_insert)

    reconcile_orphaned_events(conn)

    return {
        "rows_total": rows_total,
        "rows_rejected": rows_rejected,
        "rejections_by_reason": rejections_by_reason,
        "duplicates_merged": duplicates_merged,
        "field_warnings": field_warnings,
    }


def ingest_leads_from_directory(
    conn: sqlite3.Connection,
    leads_dir: Path,
    registry: SchemaRegistry,
) -> None:
    """Scan and ingest all lead files under a drop's leads/ directory."""
    if not leads_dir.exists():
        return

    for wf_dir in sorted(leads_dir.iterdir()):
        if not wf_dir.is_dir():
            continue
        wf_id = wf_dir.name
        schema = registry.get_workflow(wf_id)
        if not schema:
            continue

        # Cache leads for this workflow across files
        leads_cache: dict[str, dict[str, Any]] = {}
        cur = conn.execute("""
            SELECT id, phone_hash, customer_name_encrypted, external_ref, attributes_json
            FROM leads WHERE workflow_id = ?
        """, (wf_id,))
        for r in cur.fetchall():
            leads_cache[r["phone_hash"]] = {
                "id": r["id"],
                "customer_name_encrypted": r["customer_name_encrypted"],
                "external_ref": r["external_ref"],
                "attributes": json.loads(r["attributes_json"]),
            }

        for csv_file in sorted(wf_dir.glob("*.csv")):
            file_key = str(csv_file.resolve())
            file_hash = compute_file_hash(csv_file)

            cur_check = conn.execute("SELECT file_hash FROM ingested_files WHERE file_path = ?", (file_key,))
            existing = cur_check.fetchone()
            if existing and existing["file_hash"] == file_hash:
                continue

            stats = ingest_lead_file(conn, csv_file, schema, leads_cache)

            increment_stat(conn, "lead_rows_total", stats["rows_total"])
            increment_stat(conn, "lead_rows_rejected", stats["rows_rejected"])
            increment_stat(conn, "duplicate_lead_rows_merged", stats["duplicates_merged"])
            increment_stat(conn, "field_warnings", stats["field_warnings"])
            for reason, count in stats["rejections_by_reason"].items():
                increment_stat(conn, f"rejection:{reason}", count)

            conn.execute("""
                INSERT OR REPLACE INTO ingested_files (file_path, file_type, file_hash, ingested_at)
                VALUES (?, 'lead', ?, ?)
            """, (file_key, file_hash, datetime.now(IST).isoformat()))

    # Reconcile orphaned events once at the end of lead directory ingestion
    reconcile_orphaned_events(conn)
