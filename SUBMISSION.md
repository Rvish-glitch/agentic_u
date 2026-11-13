# Submission

## Candidate
- **Name:** Vishwajeet
- **Assignment chosen:** A (Data: Interaction Ledger & Reporting Pipeline)
- **Time spent (honest estimate):** ~5.5 hours

## How to run
```bash
# 1. Environment setup
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
# .venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Generate test data (small scale)
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# 4. Ingest drops sequentially
python run.py ingest data/drop_1
python run.py ingest data/drop_2

# 5. Generate output reports
python run.py report --out reports/

# 6. Run automated test suite
pytest
```

## Design

### Architecture Overview
The solution is structured as a modular, production-grade ELT data pipeline built on Python 3.11+ and SQLite with Write-Ahead Logging (WAL) mode. The core architecture follows these design principles:

```
[Drop Ingestion: CSV / JSONL]
           │
           ▼
┌─────────────────────────────────┐
│       Normalisation & PII       │
│  - Header alias matching        │
│  - Strict schema-order reject   │
│  - HMAC-SHA256 phone hashing    │
│  - Fernet AES-128 encryption    │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────┐
│               Relational SQLite Database                │
│  - leads: deduplicated identities, permanent batch_id   │
│  - interaction_ledger: append-only immutable events     │
│  - lead_rejections: non-PII audit trail of rejected rows│
│  - disposition_catalog: terminal & success flags        │
│  - ingested_files: SHA-256 idempotency registry         │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│      SQL Aggregation Engine     │
│  - Indexed temporary tables     │
│  - Single-pass window derivation│
│  - Exact contract CSV & JSON    │
└─────────────────────────────────┘
```

### Database Schema & Design Rationale
1. **`leads` Table:**
   - Primary identity: `(workflow_id, phone_hash)` with `UNIQUE` constraint.
   - `phone_hash` stores an HMAC-SHA256 digest of the canonical Indian mobile number (`+91XXXXXXXXXX`), keyed with an application secret. This enables deterministic O(1) joins and uniqueness checks without storing raw phone numbers in plaintext.
   - `phone_masked` (`+91******XXXX`) is stored for zero-overhead report generation.
   - `phone_encrypted` and `customer_name_encrypted` store ciphertexts encrypted with AES-128/Fernet.
   - `external_ref` (`policy_number` / `order_id`) and non-PII attributes (`attributes_json`) are stored with last-write-wins merging.
   - Permanent `batch_id`: locked to the file stem of the first valid row and never overwritten by subsequent duplicate rows.

2. **`interaction_ledger` Table:**
   - Strict append-only record of every valid, non-duplicate event.
   - Columns: `event_id`, `event_type`, `workflow_id`, `call_id`, `occurred_at_ist`, `call_date_ist`, `phone_hash`, `lead_id`, `payload_json`, `status`.
   - Immutable: overrides are appended as new events; the original classification is never edited or deleted.
   - B-Tree indexes on `call_id`, `(call_id, event_type)`, `(workflow_id, phone_hash)`, and partial indexes on orphaned records ensure high-speed lookups and incremental linking.

3. **`lead_rejections` Table:**
   - Stores `(workflow_id, batch_id, row_number, reason)` with zero PII.
   - Indexed on `(workflow_id, batch_id, row_number)` for sorting.

4. **`ingested_files` Table:**
   - Tracks file paths, types, and SHA-256 hashes. If a drop is re-run, existing files are skipped, guaranteeing strict idempotency without corrupting metrics.

### PII Protection at Rest
- **At Rest:** Raw phone numbers and customer names are never stored in plaintext in the database or reports.
- **Deterministic Joins:** Phone matching uses `HMAC-SHA256(secret_key, canonical_phone)`. Unlike unkeyed MD5/SHA256, HMAC prevents rainbow-table and dictionary attacks on the finite space of 10-digit Indian phone numbers.
- **Masking:** Output reports display `+91******` followed by the last 4 digits.

## Decisions & Assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| **HMAC-SHA256 for phone matching** | 10-digit phone numbers have a limited space (~$4 \times 10^9$) vulnerable to rainbow tables. HMAC with an application secret prevents offline dictionary attacks. | Standard SHA-256 (vulnerable to precomputation) or full homomorphic encryption (prohibitive performance overhead). |
| **Blank rows definition** | Rows where every cell is empty or whitespace are skipped and not counted in `lead_rows_total` or 1-based row numbering. | Treating blank rows as rejected (rejected because spec explicitly says "skipped and not counted"). |
| **Schema-order rejection evaluation** | Spec strictly requires walking fields in schema order: `missing_required:<field>` evaluated before `invalid_phone` if the required field appears earlier in the schema. | Validating phone first (rejected: would misclassify rows where customer_name was missing). |
| **Field warning criteria** | Field warnings increment only for optional fields present but invalid, and only on rows that are *not* rejected. | Counting warnings on rejected rows (rejected: spec explicitly says "Count warnings only for rows that are not rejected"). |
| **Idempotency via File Content Hashing** | SHA-256 hash stored in `ingested_files`. Re-ingesting a drop skips re-processing, preventing duplicate counts in `data_quality.json`. | Relying solely on DB primary key conflict resolution (which would miscalculate `events_total` on re-runs). |
| **Cross-Drop Orphan Reconciliation** | Events arriving before leads are quarantined with `lead_id = NULL, status = 'orphan'`. When the matching lead arrives in a subsequent drop, orphaned events are linked immediately. | Rejecting orphan events permanently or buffering them in memory. |
| **SQL Temporary Table Materialization** | Materializing `temp_counted_calls` and `temp_lead_summary` during report generation speeds up reporting from minutes to seconds by avoiding duplicate window function CTE scans. | Repeated CTE subqueries across all 5 reports (caused massive query repetition). |

## Failure modes considered
- **Malformed Input & Corrupt JSON:** Skipped, logged, and tallied in `malformed_events` without aborting the transaction.
- **Duplicate Events Across Drops:** Detected via in-memory set and ledger index; dropped and counted in `duplicate_events_dropped`.
- **Pipeline Re-runs & Crashes:** Transactions wrap each file; partial writes roll back cleanly. Idempotency tracker ensures re-running `ingest data/drop_1` changes nothing.
- **Out-of-Order Overrides:** `disposition.override` arriving before other call events is temporarily quarantined and reconciled once other events of that `call_id` arrive.
- **Non-Standard Timezones:** Naive timestamps are normalized to IST; UTC (`Z`) and offset strings are parsed to aware datetime objects and formatted to IST.
- **What is NOT handled:** Corrupted SQLite database files on disk requiring manual recovery; network storage locks on NFS/SMB network drives.

## Testing
- **Test suite (`solution/tests/`):** 20 comprehensive automated tests runnable with `pytest`.
- **`test_normalizers.py`:**
  - Phone normalization: 10-digit formats, stripping `+91`, `0`, `91-`, brackets, spaces; rejection of invalid prefixes, landlines, and international numbers.
  - Date formats: `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, `DD-Mon-YYYY`, invalid dates (e.g. `31/02/2026`).
  - Currency & Decimals: `₹`, `Rs`, `Rs.`, `INR`, thousands commas, negative values, and rounding.
  - Enums: Case-insensitivity and whitespace stripping.
  - PII: Deterministic HMAC hashing and Fernet encryption/decryption.
- **`test_leads.py`:** Schema-order rejection, blank row skipping, field warnings, deduplication, and last-write-wins merging.
- **`test_events.py`:** Malformed JSON, duplicate event drops, cross-drop orphan resolution, and override linking.
- **`test_aggregation.py`:** All 4 bucket conditions (`yet_to_reach_out`, `completed`, `in_progress_telephony_failed`, `in_progress_dialed`), effective disposition hierarchy, connectivity rates, and success leads.
- **`test_pipeline.py`:** Full integration test running `drop_1`, `drop_2`, report generation, re-ingestion, and file-diff byte-for-byte idempotency check.

## Results
- **Small Dataset (~2k leads, ~8.7k events):**
  - Ingestion: ~3 seconds.
  - Report Generation: ~0.5 seconds.
  - Test Suite: 20 tests pass in **1.51 seconds**.
- **Data Quality Highlights (Seed 42, Small):**
  - `lead_rows_total`: 2,057
  - `lead_rows_rejected`: 108
  - `duplicate_lead_rows_merged`: 57
  - `distinct_leads_created`: 1,892 (exact check: 2057 - 108 - 57 = 1892)
  - `events_total`: 8,745
  - `malformed_events`: 8
  - `duplicate_events_dropped`: 237
  - `orphan_events`: 462
  - `unknown_disposition_events`: 18
- **Large Dataset (~140k leads, ~632k events):**
  - Ingest drop 1: ~182s.
  - Ingest drop 2: ~183s.
  - Report generation with indexed temporary tables: **~4.2 seconds**.
  - Memory consumption: constant (~45 MB RAM).

## If I had another day
1. **Columnar OLAP Storage (DuckDB):** Integrate an embedded DuckDB storage adapter alongside SQLite for sub-second analytical reporting over 10M+ events.
2. **Streaming Event Ingestion:** Replace micro-batch JSONL file reading with an asynchronous streaming consumer (e.g. Kafka/Redpanda or AWS SQS) using Python's `asyncio`.
3. **Dead-Letter Queue (DLQ) & Remediation Dashboard:** Build a simple lightweight CLI/web interface to inspect quarantined orphan events and trigger manual reassignment.
4. **PostgreSQL Migration Driver:** Provide zero-code toggle between SQLite and PostgreSQL for distributed deployments.
