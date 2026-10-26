"""
Unit tests for audit_store.py (SQLite Audit Trail & Persistence Layer)
"""

import json
import os
import tempfile
from decimal import Decimal

from audit_store import (
    init_db,
    save_audit_run,
    get_audit_history,
    get_audit_by_id,
    clear_history,
    export_history_to_csv,
)

passed = 0
failed = 0


def check(name: str, condition: bool, err_msg: str = ""):
    global passed, failed
    if condition:
        print(f"[PASS] {name}")
        passed += 1
    else:
        print(f"[FAIL] {name}: {err_msg}")
        failed += 1


# Use a dedicated temp DB for unit tests
temp_db = tempfile.mktemp(suffix=".db")

try:
    # Test 1: DB Initialization
    init_db(temp_db)
    check("H0 init_db creates database file and tables", os.path.exists(temp_db))

    # Test 2: Save single audit run with Decimals
    id_single = save_audit_run(
        reviewer_name="Eleanor Vance",
        audit_mode="single",
        total_billed=Decimal("5176.35"),
        total_allowed=Decimal("3823.60"),
        total_leakage=Decimal("1352.75"),
        leakage_percent=Decimal("26.13"),
        invoice_identifier="INV-9042",
        details={
            "invoice_number": "INV-9042",
            "vendor_name": "SwiftLine",
            "findings": ["Linehaul rate overcharge: $652.50"],
        },
        db_path=temp_db,
    )
    check("H1 save_audit_run returns valid UUID string for single audit", isinstance(id_single, str) and len(id_single) > 10)

    # Test 3: Save batch audit run
    id_batch = save_audit_run(
        reviewer_name="Marcus Reed",
        audit_mode="batch",
        total_billed=10250.00,
        total_allowed=7500.00,
        total_leakage=2750.00,
        leakage_percent=26.83,
        invoice_identifier="Batch (3 Invoices)",
        details={
            "total_invoices": 3,
            "cumulative_shipments": 55,
            "duplicate_alerts": 1,
        },
        db_path=temp_db,
    )
    check("H2 save_audit_run stores batch audit record", isinstance(id_batch, str) and id_batch != id_single)

    # Test 4: Retrieve audit history ordered DESC
    history = get_audit_history(db_path=temp_db)
    check(
        "H3 get_audit_history returns all records sorted newest first",
        len(history) == 2 and history[0]["audit_id"] == id_batch and history[1]["audit_id"] == id_single,
        f"history order: {[h['audit_id'] for h in history]}"
    )

    # Test 5: Audit mode filter
    single_only = get_audit_history(audit_mode="single", db_path=temp_db)
    batch_only = get_audit_history(audit_mode="batch", db_path=temp_db)
    check(
        "H4 Filtering by audit_mode isolates single and batch records",
        len(single_only) == 1 and single_only[0]["audit_id"] == id_single and len(batch_only) == 1 and batch_only[0]["audit_id"] == id_batch,
        f"single: {len(single_only)}, batch: {len(batch_only)}"
    )

    # Test 6: Reviewer name filter
    eleanor_runs = get_audit_history(reviewer_name="Eleanor", db_path=temp_db)
    marcus_runs = get_audit_history(reviewer_name="Marcus", db_path=temp_db)
    check(
        "H5 Filtering by reviewer_name matches substrings",
        len(eleanor_runs) == 1 and eleanor_runs[0]["reviewer_name"] == "Eleanor Vance" and len(marcus_runs) == 1,
        f"eleanor: {len(eleanor_runs)}, marcus: {len(marcus_runs)}"
    )

    # Test 7: Get audit by ID & parse details JSON
    rec_detail = get_audit_by_id(id_single, db_path=temp_db)
    check(
        "H6 get_audit_by_id retrieves parsed JSON details",
        rec_detail is not None
        and rec_detail["total_billed"] == 5176.35
        and rec_detail["total_leakage"] == 1352.75
        and rec_detail["details"]["invoice_number"] == "INV-9042",
        f"rec_detail: {rec_detail}"
    )

    # Test 8: Export history to CSV
    csv_out = export_history_to_csv(db_path=temp_db)
    check(
        "H7 export_history_to_csv produces valid CSV headers and rows",
        "Audit ID" in csv_out
        and "INV-9042" in csv_out
        and "Batch (3 Invoices)" in csv_out
        and "5176.35" in csv_out,
        f"csv_out: {csv_out[:200]}"
    )

    # Test 9: Clear history
    del_count = clear_history(db_path=temp_db)
    post_clear_hist = get_audit_history(db_path=temp_db)
    check(
        "H8 clear_history empties database table and returns rowcount",
        del_count == 2 and len(post_clear_hist) == 0,
        f"del_count: {del_count}, remaining: {len(post_clear_hist)}"
    )

finally:
    if os.path.exists(temp_db):
        try:
            os.unlink(temp_db)
        except OSError:
            pass

print(f"\nAUDIT STORE TEST SUMMARY: {passed} passed, {failed} failed")
if failed > 0:
    exit(1)
