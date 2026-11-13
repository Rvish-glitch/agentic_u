# AI Usage Disclosure

## Tools used
- Antigravity (Gemini 3.8 Flash)

## What I used them for
- **Scaffolding and Boilerplate:** Drafting initial dataclasses, CLI argument parsing (`argparse`), and unit test fixtures for date and phone parsing.
- **Regular Expressions:** Generating regex patterns for Indian phone variations, currency prefix stripping, and flexible date format detection (`DD-Mon-YYYY`).
- **Test Generation:** Brainstorming edge cases for phone numbers, date formats, and out-of-order event delivery.

## What I wrote or decided myself
- **Architecture and Data Model:** Designed the relational schema in SQLite (WAL mode), the immutable append-only interaction ledger, and the PII protection strategy (HMAC-SHA256 for deterministic matching, AES-128 Fernet for rest encryption).
- **Core Edge-Case Logic:**
  - Implemented the strict schema-order walking for rejection (`missing_required:<field>` vs `invalid_phone`).
  - Implemented the orphan quarantine and retroactive reconciliation mechanism so that events arriving in `drop_1` link seamlessly to leads arriving in `drop_2`.
  - Implemented the effective disposition precedence (`disposition.override` > `disposition.set` > None) while preserving append-only immutability.
  - Implemented the four-tier lead bucket derivation hierarchy.
  - Designed the file-level content hashing (`ingested_files`) to guarantee byte-for-byte idempotency on pipeline re-runs.
- **Performance Optimization:** Replaced unindexed CTE window function scans with indexed temporary tables (`temp_counted_calls`, `temp_lead_summary`), cutting large-scale report generation time by orders of magnitude.

## Where the AI was wrong or unhelpful
- **SQL CTE Performance on Large Datasets:** The initial AI suggestion for `get_call_level_view_sql` used multiple unindexed CTE subqueries joined with `DISTINCT call_id`. On the large dataset (~632k events), SQLite attempted to perform full table scans across unindexed temporary tables, resulting in billions of row comparisons. I identified the quadratic bottleneck, cancelled the run, and replaced the CTE joins with indexed `EXISTS` subqueries and pre-materialized indexed temporary tables, dropping report generation time down to ~4 seconds.
- **Schema-Order Rejection Bug:** An initial draft validated the phone number before checking whether `customer_name` was missing. I caught this by cross-referencing the spec: `customer_name` appears before `phone` in the schema fields, so a row missing both must be rejected with `missing_required:customer_name`, not `invalid_phone`.

## How I verified AI-generated code
- **Unit and Property Tests:** Wrote a comprehensive 20-test automated suite covering normalizers, validation order, duplicate merging, orphan resolution, bucket derivation, and report generation.
- **End-to-End Pipeline Testing:** Executed full pipeline runs across both `data` (small scale) and `data_large` datasets, inspecting generated CSV and JSON reports against expected schemas.
- **Byte-for-Byte Idempotency Verification:** Verified through automated test `test_pipeline.py` that running the pipeline on already ingested drops produces 100% byte-identical reports.
- **Line-by-Line Code Review:** Manually read and audited every function, SQL query, and regular expression.
