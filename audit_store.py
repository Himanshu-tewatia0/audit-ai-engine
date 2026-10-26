"""
Audit Store & Historical Persistence Layer (audit_store.py)
Provides SQLite-backed persistence for single and batch forensic audit runs,
enabling historical reporting, compliance audit trails, and ledger export.
"""

import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

# Default DB location in the project directory
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_history.db")


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a connection to SQLite database with row factory enabled."""
    target_path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(target_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initializes the SQLite database tables and indexes if they do not exist."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audits (
                    audit_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    reviewer_name TEXT NOT NULL,
                    audit_mode TEXT NOT NULL,
                    invoice_identifier TEXT NOT NULL,
                    total_billed REAL NOT NULL,
                    total_allowed REAL NOT NULL,
                    total_leakage REAL NOT NULL,
                    leakage_percent REAL NOT NULL,
                    details_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audits_timestamp ON audits(timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audits_mode ON audits(audit_mode)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audits_reviewer ON audits(reviewer_name)"
            )
    finally:
        conn.close()


def _to_float(val: Any) -> float:
    """Safely converts numeric or Decimal values to float for storage."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(str(val))
    except (ValueError, TypeError):
        return 0.0


def save_audit_run(
    reviewer_name: str,
    audit_mode: str,
    total_billed: Union[float, Decimal],
    total_allowed: Union[float, Decimal],
    total_leakage: Union[float, Decimal],
    leakage_percent: Union[float, Decimal],
    details: Union[Dict[str, Any], str],
    invoice_identifier: Optional[str] = None,
    audit_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    db_path: Optional[str] = None,
) -> str:
    """
    Saves a completed forensic audit run into the audits table.
    
    Args:
        reviewer_name: Certified auditor name from the sign-off gate.
        audit_mode: Mode of audit execution ('single' or 'batch').
        total_billed: Billed dollar amount.
        total_allowed: Contractually allowed dollar amount.
        total_leakage: Discrepancy / recoverable dollar amount.
        leakage_percent: Leakage percentage.
        details: Dict or JSON string with full audit report/summary breakdown.
        invoice_identifier: Invoice ID (e.g. INV-9042) or Batch label.
        audit_id: Optional UUID string; generated automatically if None.
        timestamp: Optional ISO 8601 timestamp string; defaults to UTC now.
        db_path: Optional custom path to SQLite database.
        
    Returns:
        The audit_id string of the saved record.
    """
    init_db(db_path)
    
    record_id = audit_id or str(uuid.uuid4())
    record_time = timestamp or datetime.now(timezone.utc).isoformat()
    
    if invoice_identifier is None:
        if isinstance(details, dict):
            invoice_identifier = details.get("invoice_number", f"{audit_mode.capitalize()} Run")
        else:
            invoice_identifier = f"{audit_mode.capitalize()} Run"
            
    if isinstance(details, str):
        details_str = details
    else:
        details_str = json.dumps(details, indent=2, default=str)
        
    billed_f = _to_float(total_billed)
    allowed_f = _to_float(total_allowed)
    leakage_f = _to_float(total_leakage)
    leakage_pct_f = _to_float(leakage_percent)
    
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO audits (
                    audit_id,
                    timestamp,
                    reviewer_name,
                    audit_mode,
                    invoice_identifier,
                    total_billed,
                    total_allowed,
                    total_leakage,
                    leakage_percent,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    record_time,
                    reviewer_name.strip(),
                    audit_mode.lower().strip(),
                    invoice_identifier.strip(),
                    billed_f,
                    allowed_f,
                    leakage_f,
                    leakage_pct_f,
                    details_str,
                ),
            )
    finally:
        conn.close()
        
    return record_id


def get_audit_history(
    limit: Optional[int] = None,
    audit_mode: Optional[str] = None,
    reviewer_name: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieves historical forensic audit records ordered by most recent first.
    
    Args:
        limit: Max number of records to return.
        audit_mode: Optional filter ('single' or 'batch').
        reviewer_name: Optional substring or exact match filter for reviewer.
        db_path: Optional custom SQLite database path.
        
    Returns:
        List of audit record dicts.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        query = "SELECT * FROM audits WHERE 1=1"
        params: List[Any] = []
        
        if audit_mode and audit_mode.lower() != "all":
            query += " AND audit_mode = ?"
            params.append(audit_mode.lower().strip())
            
        if reviewer_name and reviewer_name.strip():
            query += " AND reviewer_name LIKE ?"
            params.append(f"%{reviewer_name.strip()}%")
            
        query += " ORDER BY timestamp DESC"
        
        if limit and limit > 0:
            query += " LIMIT ?"
            params.append(limit)
            
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        results = []
        for r in rows:
            results.append({
                "audit_id": r["audit_id"],
                "timestamp": r["timestamp"],
                "reviewer_name": r["reviewer_name"],
                "audit_mode": r["audit_mode"],
                "invoice_identifier": r["invoice_identifier"],
                "total_billed": r["total_billed"],
                "total_allowed": r["total_allowed"],
                "total_leakage": r["total_leakage"],
                "leakage_percent": r["leakage_percent"],
                "details_json": r["details_json"],
            })
        return results
    finally:
        conn.close()


def get_audit_by_id(audit_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves a single audit record and parses its JSON details if found."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM audits WHERE audit_id = ?", (audit_id,))
        row = cursor.fetchone()
        if not row:
            return None
        res = {
            "audit_id": row["audit_id"],
            "timestamp": row["timestamp"],
            "reviewer_name": row["reviewer_name"],
            "audit_mode": row["audit_mode"],
            "invoice_identifier": row["invoice_identifier"],
            "total_billed": row["total_billed"],
            "total_allowed": row["total_allowed"],
            "total_leakage": row["total_leakage"],
            "leakage_percent": row["leakage_percent"],
            "details_json": row["details_json"],
        }
        try:
            res["details"] = json.loads(row["details_json"])
        except Exception:
            res["details"] = {}
        return res
    finally:
        conn.close()


def clear_history(db_path: Optional[str] = None) -> int:
    """Clears all records in the audits table and returns the count of deleted rows."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.execute("DELETE FROM audits")
            deleted_count = cursor.rowcount
            return deleted_count
    finally:
        conn.close()


def export_history_to_csv(
    records: Optional[List[Dict[str, Any]]] = None,
    db_path: Optional[str] = None,
) -> str:
    """
    Exports audit history records into a clean, RFC-4180 compliant CSV string.
    If records is None, fetches all history from the database.
    """
    if records is None:
        records = get_audit_history(db_path=db_path)
        
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Audit ID",
        "Timestamp (UTC)",
        "Audit Mode",
        "Invoice / Batch ID",
        "Auditor Sign-Off",
        "Total Billed ($)",
        "Contract Allowed ($)",
        "Recoverable Leakage ($)",
        "Leakage Rate (%)",
    ])
    
    for r in records:
        writer.writerow([
            r.get("audit_id", ""),
            r.get("timestamp", ""),
            r.get("audit_mode", "").upper(),
            r.get("invoice_identifier", ""),
            r.get("reviewer_name", ""),
            f"{float(r.get('total_billed', 0.0)):.2f}",
            f"{float(r.get('total_allowed', 0.0)):.2f}",
            f"{float(r.get('total_leakage', 0.0)):.2f}",
            f"{float(r.get('leakage_percent', 0.0)):.2f}%",
        ])
        
    return out.getvalue()
