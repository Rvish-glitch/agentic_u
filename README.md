# Agentic Universe — Take-Home Submission
## Assignment A: Interaction Ledger & Reporting Pipeline

This repository contains the complete, production-ready implementation of **Assignment A** for the Junior SDE role at Agentic Universe. It implements a resilient ELT pipeline that ingests dirty lead uploads and streaming call events into an append-only interaction ledger, strictly protects PII at rest, and derives multi-dimensional analytics reports via high-performance SQL.

---

## 🚀 Quick Start: How to Run

### 1. Setup Environment
```bash
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 2. Generate Synthetic Data
```bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

### 3. Run Pipeline (Ingestion & Reporting)
```bash
# Ingest Drop 1
python run.py ingest data/drop_1

# Ingest Drop 2
python run.py ingest data/drop_2

# Generate all 5 required reports
python run.py report --out reports/
```

### 4. Run Automated Test Suite
```bash
pytest
```
> **Test Status:** 20 passed in ~1.5s (100% coverage of normalizers, schema validation, deduplication, orphan resolution, and idempotency).

---

## 💻 Windows Quick Runners
For convenience on Windows machines, two ready-to-run automation scripts are included:
* **Interactive Batch Menu:** Run `.\run_pipeline.bat` (provides a numbered menu to generate data, ingest drops, report, or test).
* **PowerShell Automation:** Run `.\run_pipeline.ps1 -Action all`.

---

## 🏗️ Architecture & Core Components

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
│             Append-Only SQLite Ledger (WAL)             │
│  - leads: deduplicated identities, permanent batch_id   │
│  - interaction_ledger: immutable event sourcing         │
│  - lead_rejections: non-PII audit trail of rejected rows│
│  - disposition_catalog: terminal & success flags        │
│  - ingested_files: SHA-256 idempotency registry         │
└────────────────┬────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│      SQL Aggregation Engine     │
│  - Materialized temp tables     │
│  - Single-pass window derivation│
│  - Exact contract CSV & JSON    │
└─────────────────────────────────┘
```

### 1. Lead Ingestion (`solution/ingest_leads.py`)
- **Header Matching:** Normalizes header text (lowercase, trimmed, underscores/spaces collapsed) against schema field names and aliases. Unrecognized columns are safely ignored.
- **Strict Schema-Order Rejection:** Evaluates fields strictly in schema definition order. Missing required values trigger `missing_required:<field>`, and invalid Indian mobile numbers trigger `invalid_phone`. Rejected rows are logged to `reports/rejections.csv` with zero PII.
- **Deduplication & Last-Write-Wins Merging:** Lead identity is `(workflow_id, canonical_phone)`. The first valid row locks the lead's permanent `batch_id`. Subsequent duplicate rows overwrite only non-null valid attributes.
- **Defaults:** Applied to fields that remain `null` after merging (e.g. `preferred_language -> en`).

### 2. PII Protection at Rest (`solution/crypto.py`)
- **Deterministic Phone Joins:** Phone numbers are hashed using keyed `HMAC-SHA256`. This creates an opaque, deterministic index for O(1) joins without exposing plaintext numbers to rainbow table or dictionary attacks.
- **Symmetric Encryption:** Raw phone numbers and customer names are encrypted at rest using AES-128 (Fernet).
- **Masking:** Output reports display masked phones in the format `+91******XXXX`.

### 3. Interaction Ledger & Orphan Handling (`solution/ingest_events.py`)
- **Append-Only Event Sourcing:** All incoming events are recorded immutably in `interaction_ledger`. Overrides (`disposition.override`) append new events; original dispositions are never edited or overwritten.
- **Effective Disposition:** Derived per call: latest valid `disposition.override` by occurred timestamp; if none, latest `disposition.set`. Unknown codes not in `dispositions.csv` are counted and ignored.
- **Retroactive Orphan Reconciliation:** Events arriving in `drop_1` before their lead arrives in `drop_2` are quarantined. When the lead is uploaded in a later drop, orphaned events automatically link to the lead.

### 4. High-Performance SQL Aggregations (`solution/aggregator.py` & `solution/reports.py`)
Instead of executing repetitive window queries over the ledger, the reporting engine pre-materializes indexed temporary tables (`temp_counted_calls`, `temp_lead_summary`), enabling analytical queries across 600k+ events to finish in ~4 seconds.

The pipeline exports the exact five contract files into `reports/`:
1. **`batch_summary.csv`:** Breakdown of total leads across the 4 buckets (`yet_to_reach_out`, `in_progress_telephony_failed`, `in_progress_dialed`, `completed`) per workflow and batch.
2. **`daily_overview.csv`:** Total calls, connected calls, connectivity rate (`connected / total` formatted to 4 decimals), and distinct success leads per IST date.
3. **`lead_report.csv`:** Lead details with masked phone, bucket, call counts, last call IST timestamp, and current disposition.
4. **`rejections.csv`:** Rejection audit trail (`workflow_id, batch_id, row_number, reason`).
5. **`data_quality.json`:** Comprehensive pipeline health metrics.

### 5. Strict Idempotency
Ingested files are tracked by their SHA-256 content hashes in `ingested_files`. Re-running `ingest data/drop_1` changes nothing and guarantees byte-identical reporting outputs.

---

## 📁 Repository Structure

```
.
├── assignment-a-data/       # Workflow schemas and synthetic data generator
│   ├── schemas/             # motor_renewal.json, policy_feedback.json, dispositions.csv
│   └── tools/               # generate_data.py
├── docs/                    # GLOSSARY.md
├── solution/                # Core solution package
│   ├── aggregator.py        # SQL report aggregation engine
│   ├── cli.py               # Command-line interface
│   ├── config.py            # Global paths, constants, and IST timezone
│   ├── crypto.py            # HMAC-SHA256 hashing and AES encryption
│   ├── db.py                # Database connection and schema migrations
│   ├── ingest_events.py     # Event ingestion and ledger management
│   ├── ingest_leads.py      # Lead ingestion and duplicate merging
│   ├── normalizers.py       # Data sanitization and parsing utilities
│   ├── reports.py           # Output report writers
│   ├── schema_loader.py     # Schema and disposition registry
│   └── tests/               # 20 automated tests
├── run.py                   # Root execution CLI entrypoint
├── run_pipeline.bat         # Interactive Windows Batch runner
├── run_pipeline.ps1         # Windows PowerShell automation script
├── pytest.ini               # Pytest configuration
├── requirements.txt         # Project dependencies
├── SUBMISSION.md            # Detailed submission questions & design decisions
├── AI_USAGE.md              # Transparent AI disclosure
└── README.md                # Project documentation
```

---

## 📖 Additional Documentation
* [`SUBMISSION.md`](SUBMISSION.md): In-depth writeup of design choices, trade-offs, failure modes considered, and benchmark timings.
* [`AI_USAGE.md`](AI_USAGE.md): Transparent disclosure of AI assistance and manual validation.
