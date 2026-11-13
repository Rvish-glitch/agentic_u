"""SQL aggregation engine for computing derived facts and reports."""

from __future__ import annotations

import sqlite3
from typing import Any
from solution.db import get_stat


def prepare_report_views(conn: sqlite3.Connection) -> None:
    """Pre-materialize counted calls and lead summary into indexed temp tables.
    
    This replaces expensive repeated CTE window scans across hundreds of thousands of events
    with a single-pass indexed temp table, speeding up reporting by orders of magnitude.
    """
    conn.execute("DROP TABLE IF EXISTS temp_counted_calls;")
    conn.execute("DROP TABLE IF EXISTS temp_lead_summary;")

    conn.execute("""
        CREATE TEMP TABLE temp_counted_calls AS
        SELECT 
            init.call_id,
            init.lead_id,
            init.workflow_id,
            init.occurred_at_ist AS initiated_at,
            init.call_date_ist AS call_date,
            CASE WHEN EXISTS (
                SELECT 1 FROM interaction_ledger conn_ev 
                WHERE conn_ev.call_id = init.call_id AND conn_ev.event_type = 'call.connected'
            ) THEN 1 ELSE 0 END AS is_connected,
            CASE WHEN EXISTS (
                SELECT 1 FROM interaction_ledger fail_ev 
                WHERE fail_ev.call_id = init.call_id AND fail_ev.event_type = 'call.telephony_failed'
            ) THEN 1 ELSE 0 END AS is_telephony_failed,
            COALESCE(
                (
                    SELECT json_extract(ov.payload_json, '$.disposition_code')
                    FROM interaction_ledger ov
                    JOIN disposition_catalog dc ON dc.workflow_id = ov.workflow_id 
                        AND dc.code = json_extract(ov.payload_json, '$.disposition_code')
                    WHERE ov.call_id = init.call_id AND ov.event_type = 'disposition.override'
                    ORDER BY ov.occurred_at_ist DESC
                    LIMIT 1
                ),
                (
                    SELECT json_extract(ds.payload_json, '$.disposition_code')
                    FROM interaction_ledger ds
                    JOIN disposition_catalog dc ON dc.workflow_id = ds.workflow_id 
                        AND dc.code = json_extract(ds.payload_json, '$.disposition_code')
                    WHERE ds.call_id = init.call_id AND ds.event_type = 'disposition.set'
                    ORDER BY ds.occurred_at_ist DESC
                    LIMIT 1
                )
            ) AS effective_disposition
        FROM interaction_ledger init
        WHERE init.event_type = 'call.initiated' AND init.lead_id IS NOT NULL;
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS temp_idx_call_lead ON temp_counted_calls(lead_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS temp_idx_call_wf_date ON temp_counted_calls(workflow_id, call_date);")

    conn.execute("""
        CREATE TEMP TABLE temp_lead_summary AS
        WITH call_with_meta AS (
            SELECT 
                c.*,
                COALESCE(dc.is_terminal, 0) AS is_terminal,
                COALESCE(dc.is_success, 0) AS is_success
            FROM temp_counted_calls c
            LEFT JOIN disposition_catalog dc 
              ON c.workflow_id = dc.workflow_id AND c.effective_disposition = dc.code
        ),
        lead_stats AS (
            SELECT 
                lead_id,
                COUNT(call_id) AS total_calls,
                SUM(is_connected) AS connected_calls,
                SUM(is_telephony_failed) AS telephony_failed_calls,
                MAX(is_terminal) AS has_terminal,
                MAX(is_success) AS ever_success_int,
                MAX(initiated_at) AS last_call_at
            FROM call_with_meta
            GROUP BY lead_id
        ),
        lead_latest_disp AS (
            SELECT lead_id, effective_disposition AS current_disposition
            FROM (
                SELECT lead_id, effective_disposition,
                       ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY initiated_at DESC, call_id DESC) AS rn
                FROM call_with_meta
                WHERE effective_disposition IS NOT NULL
            ) WHERE rn = 1
        )
        SELECT 
            l.id AS lead_id,
            l.workflow_id,
            l.external_ref,
            l.batch_id,
            l.phone_masked,
            COALESCE(ls.total_calls, 0) AS total_calls,
            COALESCE(ls.connected_calls, 0) AS connected_calls,
            COALESCE(ls.last_call_at, '') AS last_call_at,
            COALESCE(ld.current_disposition, '') AS current_disposition,
            CASE WHEN COALESCE(ls.ever_success_int, 0) = 1 THEN 'true' ELSE 'false' END AS ever_success,
            CASE 
                WHEN COALESCE(ls.total_calls, 0) = 0 THEN 'yet_to_reach_out'
                WHEN COALESCE(ls.has_terminal, 0) = 1 THEN 'completed'
                WHEN ls.telephony_failed_calls = ls.total_calls THEN 'in_progress_telephony_failed'
                ELSE 'in_progress_dialed'
            END AS bucket
        FROM leads l
        LEFT JOIN lead_stats ls ON l.id = ls.lead_id
        LEFT JOIN lead_latest_disp ld ON l.id = ld.lead_id;
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS temp_idx_ls_wf_batch ON temp_lead_summary(workflow_id, batch_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS temp_idx_ls_wf_ref ON temp_lead_summary(workflow_id, external_ref);")


def _ensure_report_views(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT name FROM sqlite_temp_master WHERE type='table' AND name='temp_lead_summary';")
    if not cur.fetchone():
        prepare_report_views(conn)


def compute_batch_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Compute Report 1: batch_summary.csv."""
    _ensure_report_views(conn)
    query = """
        SELECT 
            b.workflow_id,
            b.batch_id,
            COUNT(ls.lead_id) AS total_leads,
            COALESCE(SUM(CASE WHEN ls.bucket = 'yet_to_reach_out' THEN 1 ELSE 0 END), 0) AS yet_to_reach_out,
            COALESCE(SUM(CASE WHEN ls.bucket = 'in_progress_telephony_failed' THEN 1 ELSE 0 END), 0) AS in_progress_telephony_failed,
            COALESCE(SUM(CASE WHEN ls.bucket = 'in_progress_dialed' THEN 1 ELSE 0 END), 0) AS in_progress_dialed,
            COALESCE(SUM(CASE WHEN ls.bucket = 'completed' THEN 1 ELSE 0 END), 0) AS completed
        FROM batches b
        LEFT JOIN temp_lead_summary ls ON b.workflow_id = ls.workflow_id AND b.batch_id = ls.batch_id
        GROUP BY b.workflow_id, b.batch_id
        ORDER BY b.workflow_id, b.batch_id;
    """
    cursor = conn.execute(query)
    return [dict(row) for row in cursor.fetchall()]


def compute_daily_overview(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Compute Report 2: daily_overview.csv."""
    _ensure_report_views(conn)
    query = """
        SELECT 
            c.workflow_id,
            c.call_date AS date,
            COUNT(c.call_id) AS total_calls,
            SUM(c.is_connected) AS connected_calls,
            COUNT(DISTINCT CASE WHEN dc.is_success = 1 THEN c.lead_id ELSE NULL END) AS success_leads
        FROM temp_counted_calls c
        LEFT JOIN disposition_catalog dc 
          ON c.workflow_id = dc.workflow_id AND c.effective_disposition = dc.code
        GROUP BY c.workflow_id, c.call_date
        HAVING COUNT(c.call_id) >= 1
        ORDER BY c.workflow_id, c.call_date;
    """
    cursor = conn.execute(query)
    rows = []
    for r in cursor.fetchall():
        total = r["total_calls"]
        conn_count = r["connected_calls"]
        rate = (conn_count / total) if total > 0 else 0.0
        rows.append({
            "workflow_id": r["workflow_id"],
            "date": r["date"],
            "total_calls": total,
            "connected_calls": conn_count,
            "connectivity_rate": f"{rate:.4f}",
            "success_leads": r["success_leads"],
        })
    return rows


def compute_lead_report(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Compute Report 3: lead_report.csv."""
    _ensure_report_views(conn)
    query = """
        SELECT 
            workflow_id,
            external_ref,
            batch_id,
            phone_masked,
            bucket,
            total_calls,
            connected_calls,
            last_call_at,
            current_disposition,
            ever_success
        FROM temp_lead_summary
        ORDER BY workflow_id, external_ref;
    """
    cursor = conn.execute(query)
    return [dict(row) for row in cursor.fetchall()]


def compute_rejections_report(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Compute Report 4: rejections.csv."""
    query = """
        SELECT workflow_id, batch_id, row_number, reason
        FROM lead_rejections
        ORDER BY workflow_id, batch_id, row_number;
    """
    cursor = conn.execute(query)
    return [dict(row) for row in cursor.fetchall()]


def compute_data_quality(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compute Report 5: data_quality.json."""
    lead_rows_total = get_stat(conn, "lead_rows_total")
    duplicate_lead_rows_merged = get_stat(conn, "duplicate_lead_rows_merged")
    field_warnings = get_stat(conn, "field_warnings")

    rej_cur = conn.execute("""
        SELECT reason, COUNT(*) AS cnt
        FROM lead_rejections
        GROUP BY reason
        ORDER BY reason;
    """)
    rejections_by_reason: dict[str, int] = {row["reason"]: row["cnt"] for row in rej_cur.fetchall()}
    lead_rows_rejected = sum(rejections_by_reason.values())

    events_total = get_stat(conn, "events_total")
    malformed_events = get_stat(conn, "malformed_events")
    duplicate_events_dropped = get_stat(conn, "duplicate_events_dropped")

    cur_orphans = conn.execute("SELECT COUNT(*) AS cnt FROM interaction_ledger WHERE lead_id IS NULL;")
    orphan_events = cur_orphans.fetchone()["cnt"]

    cur_unknown = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM interaction_ledger
        WHERE lead_id IS NOT NULL AND status = 'unknown_disposition';
    """)
    unknown_disposition_events = cur_unknown.fetchone()["cnt"]

    return {
        "lead_rows_total": lead_rows_total,
        "lead_rows_rejected": lead_rows_rejected,
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": duplicate_lead_rows_merged,
        "field_warnings": field_warnings,
        "events_total": events_total,
        "malformed_events": malformed_events,
        "duplicate_events_dropped": duplicate_events_dropped,
        "orphan_events": orphan_events,
        "unknown_disposition_events": unknown_disposition_events,
    }
