"""Database access layer and schema definitions using SQLite with WAL mode."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator
from contextlib import contextmanager
from solution.config import DEFAULT_DB_PATH


def get_db_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create a high-performance SQLite connection configured for concurrent WAL access."""
    conn = sqlite3.connect(str(db_path), timeout=120.0)
    conn.row_factory = sqlite3.Row
    # Configure high-performance SQLite pragmas
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = 100000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db_transaction(db_path: str | Path = DEFAULT_DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Context manager yielding a transaction-bound connection."""
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """Initialize database tables, constraints, and indexes."""
    with db_transaction(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS batches (
            workflow_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            PRIMARY KEY (workflow_id, batch_id)
        );

        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            phone_hash TEXT NOT NULL,
            phone_masked TEXT NOT NULL,
            phone_encrypted TEXT NOT NULL,
            customer_name_encrypted TEXT NOT NULL,
            external_ref TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            attributes_json TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            UNIQUE(workflow_id, phone_hash)
        );

        CREATE INDEX IF NOT EXISTS idx_leads_wf_hash ON leads(workflow_id, phone_hash);
        CREATE INDEX IF NOT EXISTS idx_leads_wf_ref ON leads(workflow_id, external_ref);
        CREATE INDEX IF NOT EXISTS idx_leads_wf_batch ON leads(workflow_id, batch_id);

        CREATE TABLE IF NOT EXISTS lead_rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            reason TEXT NOT NULL,
            UNIQUE(workflow_id, batch_id, row_number)
        );

        CREATE INDEX IF NOT EXISTS idx_rejections_sort ON lead_rejections(workflow_id, batch_id, row_number);

        CREATE TABLE IF NOT EXISTS disposition_catalog (
            workflow_id TEXT NOT NULL,
            code TEXT NOT NULL,
            label TEXT NOT NULL,
            is_terminal INTEGER NOT NULL,
            is_success INTEGER NOT NULL,
            PRIMARY KEY (workflow_id, code)
        );

        CREATE TABLE IF NOT EXISTS interaction_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            call_id TEXT NOT NULL,
            occurred_at_ist TEXT NOT NULL,
            call_date_ist TEXT NOT NULL,
            phone_hash TEXT,
            phone_masked TEXT,
            lead_id INTEGER REFERENCES leads(id),
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_ledger_event_id ON interaction_ledger(event_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_lead_id ON interaction_ledger(lead_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_call_id ON interaction_ledger(call_id);
        CREATE INDEX IF NOT EXISTS idx_ledger_call_type ON interaction_ledger(call_id, event_type);
        CREATE INDEX IF NOT EXISTS idx_ledger_call_disp ON interaction_ledger(call_id, event_type, occurred_at_ist DESC);
        CREATE INDEX IF NOT EXISTS idx_ledger_wf_hash ON interaction_ledger(workflow_id, phone_hash);
        CREATE INDEX IF NOT EXISTS idx_ledger_status ON interaction_ledger(status);
        CREATE INDEX IF NOT EXISTS idx_ledger_type ON interaction_ledger(event_type);
        
        -- High-efficiency partial indexes for orphan reconciliation
        CREATE INDEX IF NOT EXISTS idx_ledger_orphan_hash ON interaction_ledger(workflow_id, phone_hash) WHERE lead_id IS NULL;
        CREATE INDEX IF NOT EXISTS idx_ledger_orphan_call ON interaction_ledger(call_id) WHERE lead_id IS NULL;

        CREATE TABLE IF NOT EXISTS ingested_files (
            file_path TEXT UNIQUE NOT NULL,
            file_type TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            ingested_at TIMESTAMP NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_stats (
            key TEXT PRIMARY KEY,
            val INTEGER NOT NULL DEFAULT 0
        );
        """)


def sync_disposition_catalog(conn: sqlite3.Connection, dispositions_dict: dict) -> None:
    """Sync disposition catalogue into the SQLite database."""
    rows = []
    for (wf, code), info in dispositions_dict.items():
        rows.append((
            wf,
            code,
            info.label,
            1 if info.is_terminal else 0,
            1 if info.is_success else 0,
        ))
    conn.executemany("""
        INSERT OR REPLACE INTO disposition_catalog (workflow_id, code, label, is_terminal, is_success)
        VALUES (?, ?, ?, ?, ?)
    """, rows)


def increment_stat(conn: sqlite3.Connection, key: str, amount: int = 1) -> None:
    """Atomically increment a pipeline metric counter."""
    conn.execute("""
        INSERT INTO pipeline_stats (key, val) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET val = val + ?
    """, (key, amount, amount))


def get_stat(conn: sqlite3.Connection, key: str) -> int:
    """Get the current value of a pipeline metric counter."""
    cursor = conn.execute("SELECT val FROM pipeline_stats WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row["val"] if row else 0
