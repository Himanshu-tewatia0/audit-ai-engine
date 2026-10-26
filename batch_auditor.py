"""
AuditAI - Enterprise Multi-Invoice Batch Auditor & Duplicate Detection Engine (v1.0)

Capabilities:
- Batch ingestion of multiple vendor invoices against a single Master Service Agreement (MSA).
- Automated Duplicate Detection: Identifies duplicate invoice IDs and identical shipment claims.
- Cumulative Volume Rollup: Aggregates total monthly shipments and linehaul spend across the batch
  to automatically trigger tiered volume rebates without manual user estimation.
- Consolidated Master Recovery Ledger: Generates comprehensive multi-invoice audit metrics,
  summary KPIs, and one-click CSV ledger export.
"""

import copy
import csv
import io
import json
import os
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import List, Dict, Any, Optional, Union

from audit_engine import (
    ContractAuditor,
    AuditReport,
    Discrepancy,
    ExtractionResult,
    _d,
    _fmt,
    extract_invoice_from_pdf,
)


@dataclass
class DuplicateAlert:
    """Represents a flagged duplicate billing entry."""
    duplicate_type: str  # "DUPLICATE_INVOICE_NUMBER" | "DUPLICATE_SHIPMENT_CLAIM"
    invoice_number: str
    conflicting_invoice: str
    details: str
    amount: Decimal


@dataclass
class BatchAuditSummary:
    """Consolidated results of a multi-invoice batch audit."""
    contract_id: str
    vendor_name: str
    buyer_name: str
    total_invoices: int
    total_batch_billed: Decimal
    total_batch_allowed: Decimal
    total_batch_leakage: Decimal
    batch_leakage_percent: Decimal
    cumulative_shipments: int
    cumulative_spend_base: Decimal
    volume_threshold_reached: bool
    duplicate_alerts: List[DuplicateAlert] = field(default_factory=list)
    reports: List[AuditReport] = field(default_factory=list)
    ledger_records: List[Dict[str, Any]] = field(default_factory=list)

    def to_csv(self) -> str:
        """Exports the master batch ledger records to a CSV formatted string."""
        if not self.ledger_records:
            return ""
        output = io.StringIO()
        fieldnames = [
            "invoice_number",
            "vendor_name",
            "buyer_name",
            "total_billed",
            "total_allowed",
            "total_leakage",
            "leakage_percent",
            "discrepancy_count",
            "duplicate_flag",
            "audit_status",
            "top_finding",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in self.ledger_records:
            writer.writerow(row)
        return output.getvalue()

    def to_dict(self) -> Dict[str, Any]:
        """Convert entire batch summary to a JSON-serializable dictionary."""
        d = asdict(self)
        def _convert(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_convert(i) for i in obj]
            return obj
        return _convert(d)


class BatchAuditor:
    """
    Forensic batch audit engine for multi-invoice portfolios.
    Evaluates cross-invoice relationships, duplicates, and cumulative tiered volume thresholds.
    """

    def __init__(self, contract_source: Union[str, dict, ExtractionResult]):
        self.contract_source = contract_source
        self.auditor = ContractAuditor(contract_source)
        self.contract_terms = self.auditor.terms
        self.volume_threshold = int(self.contract_terms.get("volume_discount_threshold_shipments", 50))
        self.rebate_percent = _d(self.contract_terms.get("volume_discount_percent", 5.0))

    def _normalize_invoice(self, inv_input: Any) -> dict:
        """Helper to convert file path, Streamlit UploadedFile, tuple, ExtractionResult, or dict to normalized invoice dictionary."""
        import tempfile
        if hasattr(inv_input, "name") and (hasattr(inv_input, "read") or hasattr(inv_input, "getvalue")):
            filename = inv_input.name
            file_bytes = inv_input.getvalue() if hasattr(inv_input, "getvalue") else inv_input.read()
            ext = os.path.splitext(filename)[1].lower()
            if ext == ".json":
                return json.loads(file_bytes.decode("utf-8") if isinstance(file_bytes, bytes) else file_bytes)
            elif ext == ".pdf":
                temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                temp.write(file_bytes if isinstance(file_bytes, bytes) else file_bytes.encode("utf-8"))
                temp.close()
                try:
                    res = extract_invoice_from_pdf(temp.name)
                    res.human_confirmed = True
                    return res.to_dict()
                finally:
                    if os.path.exists(temp.name):
                        os.unlink(temp.name)
        elif isinstance(inv_input, tuple) and len(inv_input) == 2:
            filename, file_bytes = inv_input
            ext = os.path.splitext(filename)[1].lower()
            if ext == ".json":
                return json.loads(file_bytes.decode("utf-8") if isinstance(file_bytes, bytes) else file_bytes)
            elif ext == ".pdf":
                temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                temp.write(file_bytes if isinstance(file_bytes, bytes) else file_bytes.encode("utf-8"))
                temp.close()
                try:
                    res = extract_invoice_from_pdf(temp.name)
                    res.human_confirmed = True
                    return res.to_dict()
                finally:
                    if os.path.exists(temp.name):
                        os.unlink(temp.name)
        elif isinstance(inv_input, ExtractionResult):
            inv_input.human_confirmed = True
            return inv_input.to_dict()
        elif isinstance(inv_input, str):
            ext = os.path.splitext(inv_input)[1].lower()
            if ext == ".pdf":
                res = extract_invoice_from_pdf(inv_input)
                res.human_confirmed = True
                return res.to_dict()
            else:
                with open(inv_input, "r", encoding="utf-8") as f:
                    return json.load(f)
        elif isinstance(inv_input, dict):
            return copy.deepcopy(inv_input)
        else:
            raise ValueError(f"Unsupported invoice input format: {type(inv_input)}")

    def audit_batch(
        self,
        invoices: List[Union[str, dict, ExtractionResult]],
        buyer_telemetry_map: Optional[Dict[str, Dict[str, Any]]] = None,
        reviewer_name: Optional[str] = None,
    ) -> BatchAuditSummary:
        """
        Executes batch audit across a list of invoices with duplicate scanning and cumulative volume rollup.
        """
        if not invoices:
            return BatchAuditSummary(
                contract_id=self.auditor.contract.get("contract_id", "MSA-2026-US-LOG"),
                vendor_name=self.auditor.contract.get("vendor_name", "Vendor"),
                buyer_name=self.auditor.contract.get("buyer_name", "Buyer"),
                total_invoices=0,
                total_batch_billed=Decimal("0.00"),
                total_batch_allowed=Decimal("0.00"),
                total_batch_leakage=Decimal("0.00"),
                batch_leakage_percent=Decimal("0.00"),
                cumulative_shipments=0,
                cumulative_spend_base=Decimal("0.00"),
                volume_threshold_reached=False,
            )

        telemetry_map = buyer_telemetry_map or {}
        normalized_invoices: List[dict] = []
        for inv in invoices:
            normalized_invoices.append(self._normalize_invoice(inv))

        # -------------------------------------------------------------------
        # Phase 1: Duplicate Scanning
        # -------------------------------------------------------------------
        seen_invoice_ids: Dict[str, int] = {}  # invoice_number -> index
        seen_shipment_keys: Dict[str, str] = {}  # route_key -> invoice_number
        duplicate_alerts: List[DuplicateAlert] = []
        duplicate_indices: set = set()

        for idx, inv in enumerate(normalized_invoices):
            inv_num = str(inv.get("invoice_number", f"UNKNOWN-{idx}")).strip().upper()
            billed_total = _d(inv.get("total_billed_amount", 0.0))

            # 1. Exact Duplicate Invoice Number
            if inv_num in seen_invoice_ids:
                prev_idx = seen_invoice_ids[inv_num]
                alert = DuplicateAlert(
                    duplicate_type="DUPLICATE_INVOICE_NUMBER",
                    invoice_number=inv_num,
                    conflicting_invoice=f"Invoice #{inv_num} (batch item {prev_idx + 1})",
                    details=f"Invoice #{inv_num} was submitted multiple times in this batch. 100% of duplicate claim is disallowed.",
                    amount=billed_total,
                )
                duplicate_alerts.append(alert)
                duplicate_indices.add(idx)
            else:
                seen_invoice_ids[inv_num] = idx

            # 2. Duplicate Shipment Record (same miles, linehaul rate, and billed amount)
            lh = inv.get("line_items", {}).get("linehaul", {})
            if lh:
                miles = lh.get("billed_miles", 0)
                rate = lh.get("billed_rate_per_mile", 0.0)
                amount = lh.get("billed_amount", 0.0)
                # Key: (miles, rate, amount)
                shipment_key = f"{miles}_{rate:.2f}_{amount:.2f}"
                if shipment_key in seen_shipment_keys:
                    prev_inv = seen_shipment_keys[shipment_key]
                    if idx not in duplicate_indices:
                        alert = DuplicateAlert(
                            duplicate_type="DUPLICATE_SHIPMENT_CLAIM",
                            invoice_number=inv_num,
                            conflicting_invoice=prev_inv,
                            details=f"Linehaul shipment ({miles} miles @ ${rate:.2f} = ${_d(amount)}) matches shipment in invoice {prev_inv}.",
                            amount=_d(amount),
                        )
                        duplicate_alerts.append(alert)
                else:
                    seen_shipment_keys[shipment_key] = inv_num

        # -------------------------------------------------------------------
        # Phase 2: Cumulative Volume & Qualifying Spend Aggregation
        # -------------------------------------------------------------------
        cumulative_shipments = 0
        cumulative_spend_base = Decimal("0.00")

        for idx, inv in enumerate(normalized_invoices):
            if idx in duplicate_indices:
                continue  # duplicates do not contribute to volume credit

            inv_num = str(inv.get("invoice_number", f"INV-{idx}")).strip().upper()
            override = telemetry_map.get(inv_num, {})

            # Shipment count: buyer telemetry overrides vendor claimed
            if "shipment_count" in override and override["shipment_count"] > 0:
                ship_count = int(override["shipment_count"])
            else:
                ship_count = int(inv.get("monthly_volume_shipments_to_date", 1))

            cumulative_shipments += ship_count

            lh_amt = _d(inv.get("line_items", {}).get("linehaul", {}).get("billed_amount", 0.0))
            cumulative_spend_base += lh_amt

        volume_threshold_reached = cumulative_shipments > self.volume_threshold

        # -------------------------------------------------------------------
        # Phase 3: Forensic Auditing per Invoice
        # -------------------------------------------------------------------
        reports: List[AuditReport] = []
        ledger_records: List[Dict[str, Any]] = []

        total_batch_billed = Decimal("0.00")
        total_batch_allowed = Decimal("0.00")
        total_batch_leakage = Decimal("0.00")

        for idx, inv in enumerate(normalized_invoices):
            inv_num = str(inv.get("invoice_number", f"UNKNOWN-{idx}")).strip().upper()
            billed_total = _d(inv.get("total_billed_amount", 0.0))
            override = telemetry_map.get(inv_num, {})

            # Handle Duplicate Invoice Rejection
            if idx in duplicate_indices:
                disc = Discrepancy(
                    category="DUPLICATE INVOICE SUBMISSION",
                    billed_amount=billed_total,
                    allowed_amount=Decimal("0.00"),
                    overcharge=billed_total,
                    contract_clause="Section 2.1 (Billing Integrity): Duplicate invoice submissions are rejected in full.",
                    explanation=f"Invoice #{inv_num} is a duplicate submission of an earlier invoice in this batch.",
                    status="confirmed",
                )
                dup_report = AuditReport(
                    invoice_number=inv_num,
                    vendor_name=inv.get("vendor_name", self.auditor.contract.get("vendor_name", "Vendor")),
                    buyer_name=inv.get("buyer_name", self.auditor.contract.get("buyer_name", "Buyer")),
                    contract_id=self.auditor.contract.get("contract_id", "MSA-2026-US-LOG"),
                    total_billed=billed_total,
                    total_allowed=Decimal("0.00"),
                    total_leakage=billed_total,
                    leakage_percent=Decimal("100.00"),
                    discrepancies=[disc],
                    unreviewed_lines=[],
                    warnings=[f"REJECTED: Duplicate invoice submission for #{inv_num}."],
                    rebate_base_assumption="N/A - Duplicate invoice rejected in full.",
                    reviewer_name=reviewer_name,
                )
                reports.append(dup_report)
                total_batch_billed += billed_total
                total_batch_leakage += billed_total

                ledger_records.append({
                    "invoice_number": inv_num,
                    "vendor_name": dup_report.vendor_name,
                    "buyer_name": dup_report.buyer_name,
                    "total_billed": float(billed_total),
                    "total_allowed": 0.0,
                    "total_leakage": float(billed_total),
                    "leakage_percent": 100.0,
                    "discrepancy_count": 1,
                    "duplicate_flag": "DUPLICATE",
                    "audit_status": "REJECTED (DUPLICATE)",
                    "top_finding": "DUPLICATE INVOICE SUBMISSION",
                })
                continue

            # Prepare invoice with cumulative volume telemetry
            inv_copy = copy.deepcopy(inv)
            # Pass cumulative volume so tiered rebate threshold is evaluated across the batch
            inv_copy["shipment_count"] = cumulative_shipments

            if "actual_miles" in override and override["actual_miles"] > 0:
                if "linehaul" in inv_copy.get("line_items", {}):
                    inv_copy["line_items"]["linehaul"]["actual_miles"] = override["actual_miles"]

            if "num_stops" in override and override["num_stops"] > 0:
                if "detention" in inv_copy.get("line_items", {}):
                    inv_copy["line_items"]["detention"]["num_stops"] = override["num_stops"]

            # Run forensic audit on individual invoice
            rep = self.auditor.audit_invoice(inv_copy)
            rep.reviewer_name = reviewer_name
            reports.append(rep)

            total_batch_billed += rep.total_billed
            total_batch_allowed += rep.total_allowed
            total_batch_leakage += rep.total_leakage

            top_find = rep.discrepancies[0].category if rep.discrepancies else "COMPLIANT"
            status_str = "DISPUTED" if rep.total_leakage > Decimal("0.00") else "COMPLIANT"

            ledger_records.append({
                "invoice_number": inv_num,
                "vendor_name": rep.vendor_name,
                "buyer_name": rep.buyer_name,
                "total_billed": float(rep.total_billed),
                "total_allowed": float(rep.total_allowed),
                "total_leakage": float(rep.total_leakage),
                "leakage_percent": float(rep.leakage_percent),
                "discrepancy_count": len(rep.discrepancies),
                "duplicate_flag": "CLEAN",
                "audit_status": status_str,
                "top_finding": top_find,
            })

        batch_pct = (
            (total_batch_leakage / total_batch_billed * Decimal("100")).quantize(Decimal("0.01"))
            if total_batch_billed > Decimal("0.00")
            else Decimal("0.00")
        )

        return BatchAuditSummary(
            contract_id=self.auditor.contract.get("contract_id", "MSA-2026-US-LOG"),
            vendor_name=self.auditor.contract.get("vendor_name", "Vendor"),
            buyer_name=self.auditor.contract.get("buyer_name", "Buyer"),
            total_invoices=len(normalized_invoices),
            total_batch_billed=total_batch_billed,
            total_batch_allowed=total_batch_allowed,
            total_batch_leakage=total_batch_leakage,
            batch_leakage_percent=batch_pct,
            cumulative_shipments=cumulative_shipments,
            cumulative_spend_base=cumulative_spend_base,
            volume_threshold_reached=volume_threshold_reached,
            duplicate_alerts=duplicate_alerts,
            reports=reports,
            ledger_records=ledger_records,
        )
