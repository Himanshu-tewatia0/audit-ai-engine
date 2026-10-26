"""
Extended stress tests for audit_engine.py v2.1

Covers:
  - Real PDF/Document Extraction (extract_contract_from_pdf, extract_invoice_from_pdf)
  - Human Confirmation Gate enforcement (HumanConfirmationRequiredError)
  - Bad Invoice Data Validation (rate*qty, positive rebate charge, header mismatch)
  - V5: Header total reconciliation & strict validation
  - Rebate Rules (> threshold, configurable rebate_base)
  - Buyer-side Telemetry (actual_miles, shipment_count, per-stop detention)
  - Dispute Letter Safety & Section Separation (DRAFT, S7 Confirmed vs Needs-Review separation)
"""

import copy
import json
import os
import tempfile

from audit_engine import (
    ContractAuditor,
    MissingContractTermError,
    InvoiceValidationError,
    HumanConfirmationRequiredError,
    extract_contract_from_pdf,
    extract_invoice_from_pdf,
    ExtractionResult,
    ExtractedField,
    _fmt,
    Decimal,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CONTRACT_PDF = os.path.join(BASE_DIR, "sample_data", "sample_contract.pdf")
SAMPLE_INVOICE_PDF = os.path.join(BASE_DIR, "sample_data", "sample_invoice.pdf")

CONTRACT = {
    "contract_id": "MSA-2026-US-LOG",
    "vendor_name": "SwiftLine National Freight Corp.",
    "buyer_name": "Global Retail Group Inc.",
    "terms": {
        "base_rate_per_mile": 2.40,
        "max_fuel_surcharge_percent": 12.0,
        "free_detention_hours": 2.0,
        "detention_hourly_rate": 50.0,
        "volume_discount_threshold_shipments": 50,
        "volume_discount_percent": 5.0,
    },
    "clauses": {
        "section_4_1": "Section 4.1: linehaul shall not exceed $2.40 per route mile.",
        "section_5_3": "Section 5.3: fuel surcharge capped at 12.0% of base linehaul.",
        "section_6_2": "Section 6.2: first 2.0 hours free per stop; $50.00/hour after.",
        "section_8_4": "Section 8.4: 5.0% rebate if monthly volume exceeds 50 loads.",
    },
}

INVOICE = {
    "invoice_number": "INV-9042",
    "monthly_volume_shipments_to_date": 62,
    "total_billed_amount": 5176.35,
    "line_items": {
        "linehaul": {"billed_miles": 1450, "billed_rate_per_mile": 2.85, "billed_amount": 4132.50},
        "fuel_surcharge": {"billed_percent": 18.0, "billed_amount": 743.85},
        "detention": {"billed_hours": 4.0, "billed_hourly_rate": 75.0, "billed_amount": 300.0},
        "volume_discount": {"billed_amount": 0.0},
    },
}

passed = 0
failed = 0


def audit(contract_edit=None, invoice_edit=None, strict=False):
    c, i = copy.deepcopy(CONTRACT), copy.deepcopy(INVOICE)
    if contract_edit:
        contract_edit(c)
    if invoice_edit:
        invoice_edit(i)
    folder = tempfile.mkdtemp()
    cp, ip = os.path.join(folder, "c.json"), os.path.join(folder, "i.json")
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(c, f)
    with open(ip, "w", encoding="utf-8") as f:
        json.dump(i, f)
    report = ContractAuditor(cp).audit_invoice(ip, strict_validation=strict)
    return report


def reconciles(r):
    return round(r.total_billed - r.total_allowed - r.total_leakage, 2) == 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name}")
        print(f"       -> {detail}")


# ============================================================================
# ITEM 1: Real PDF Extraction & Human Confirmation Gate
# ============================================================================

# E1: extract_contract_from_pdf extracts terms with quote, page, confidence
try:
    c_res = extract_contract_from_pdf(SAMPLE_CONTRACT_PDF)
    base_rate_f = c_res.get_field("base_rate_per_mile")
    check(
        "E1  extract_contract_from_pdf extracts terms with quote, page, and confidence",
        base_rate_f is not None and base_rate_f.value == 2.40 and base_rate_f.page_number == 1 and base_rate_f.confidence >= 0.9,
        f"got: {base_rate_f}"
    )
except Exception as e:
    check("E1  extract_contract_from_pdf extracts terms with quote, page, and confidence", False, str(e))

# E2: extract_invoice_from_pdf extracts line items from real PDF
try:
    i_res = extract_invoice_from_pdf(SAMPLE_INVOICE_PDF)
    total_f = i_res.get_field("total_billed_amount")
    inv_num_f = i_res.get_field("invoice_number")
    vendor_f = i_res.get_field("vendor_name")
    buyer_f = i_res.get_field("buyer_name")
    check(
        "E2  extract_invoice_from_pdf extracts real invoice number (INV-9042), vendor, buyer, and lines",
        total_f is not None and total_f.value == 5176.35 and inv_num_f is not None and inv_num_f.value == "INV-9042" and vendor_f is not None and "SwiftLine" in vendor_f.value and buyer_f is not None and "Global Retail" in buyer_f.value,
        f"got inv_num: {inv_num_f}, vendor: {vendor_f}, buyer: {buyer_f}"
    )
except Exception as e:
    check("E2  extract_invoice_from_pdf extracts real invoice number (INV-9042), vendor, buyer, and lines", False, str(e))

# E3: Human confirmation gate enforces HumanConfirmationRequiredError when unconfirmed
try:
    c_res_unconf = extract_contract_from_pdf(SAMPLE_CONTRACT_PDF)
    c_res_unconf.human_confirmed = False
    ContractAuditor(c_res_unconf)
    check("E3  ContractAuditor raises HumanConfirmationRequiredError if unconfirmed", False, "did not raise")
except HumanConfirmationRequiredError:
    check("E3  ContractAuditor raises HumanConfirmationRequiredError if unconfirmed", True)
except Exception as e:
    check("E3  ContractAuditor raises HumanConfirmationRequiredError if unconfirmed", False, f"raised {type(e).__name__}")

# E4: audit_invoice raises HumanConfirmationRequiredError if invoice extraction unconfirmed
try:
    c_res_conf = extract_contract_from_pdf(SAMPLE_CONTRACT_PDF)
    c_res_conf.human_confirmed = True
    auditor = ContractAuditor(c_res_conf)

    i_res_unconf = extract_invoice_from_pdf(SAMPLE_INVOICE_PDF)
    i_res_unconf.human_confirmed = False
    auditor.audit_invoice(i_res_unconf)
    check("E4  audit_invoice raises HumanConfirmationRequiredError if unconfirmed", False, "did not raise")
except HumanConfirmationRequiredError:
    check("E4  audit_invoice raises HumanConfirmationRequiredError if unconfirmed", True)
except Exception as e:
    check("E4  audit_invoice raises HumanConfirmationRequiredError if unconfirmed", False, f"raised {type(e).__name__}")

# E5: Full audit passes from PDFs when human confirms both
try:
    c_res_conf = extract_contract_from_pdf(SAMPLE_CONTRACT_PDF)
    c_res_conf.human_confirmed = True
    c_res_conf.confirmed_by = "Audit Manager"

    i_res_conf = extract_invoice_from_pdf(SAMPLE_INVOICE_PDF)
    i_res_conf.human_confirmed = True
    i_res_conf.confirmed_by = "Audit Analyst"

    auditor = ContractAuditor(c_res_conf)
    r_pdf = auditor.audit_invoice(i_res_conf)
    check("E5  Full audit executes from real PDFs when human_confirmed=True",
          r_pdf.total_leakage == Decimal("1352.75") and reconciles(r_pdf),
          f"leakage: {r_pdf.total_leakage}")
except Exception as e:
    check("E5  Full audit executes from real PDFs when human_confirmed=True", False, str(e))

# E6: Alternative layout extraction & audit (carrier_bill_alt_layout.pdf)
try:
    alt_pdf_path = os.path.join(BASE_DIR, "sample_data", "carrier_bill_alt_layout.pdf")
    c_res_conf = extract_contract_from_pdf(SAMPLE_CONTRACT_PDF)
    c_res_conf.human_confirmed = True
    c_res_conf.confirmed_by = "Audit Lead"

    i_alt = extract_invoice_from_pdf(alt_pdf_path)
    inv_num_f = i_alt.get_field("invoice_number")
    total_f = i_alt.get_field("total_billed_amount")
    lines = i_alt.to_dict()["line_items"]

    has_alt_fields = (
        inv_num_f is not None and inv_num_f.value == "FB-77401"
        and total_f is not None and total_f.value == 5176.35
        and "linehaul" in lines and lines["linehaul"]["billed_amount"] == 4132.50
        and "fuel_surcharge" in lines and lines["fuel_surcharge"]["billed_amount"] == 743.85
        and "detention" in lines and lines["detention"]["billed_amount"] == 300.00
    )

    i_alt.human_confirmed = True
    i_alt.confirmed_by = "Audit Reviewer"
    r_alt = ContractAuditor(c_res_conf).audit_invoice(i_alt)

    check(
        "E6  Alternative layout PDF (FB-77401 / Motor Carrier Charge) extracts and audits to 1,352.75 leakage",
        has_alt_fields and r_alt.total_leakage == Decimal("1352.75") and reconciles(r_alt),
        f"fields_ok: {has_alt_fields}, leakage: {r_alt.total_leakage}"
    )
except Exception as e:
    check("E6  Alternative layout PDF (FB-77401 / Motor Carrier Charge) extracts and audits to 1,352.75 leakage", False, str(e))

# E7: Multimodal LLM module offline fallback
try:
    from extractor_llm import extract_contract as extract_c_llm, extract_invoice as extract_i_llm
    c_llm = extract_c_llm(SAMPLE_CONTRACT_PDF)
    i_llm = extract_i_llm(alt_pdf_path)
    check(
        "E7  extractor_llm provides graceful offline fallback with extraction_method populated",
        c_llm.extraction_method in ["pdfplumber-deterministic", "gemini-2.5-flash-vision", "gemini-2.5-flash", "pdfplumber-fallback"]
        and i_llm.get_field("invoice_number").value == "FB-77401",
        f"c_method: {c_llm.extraction_method}, i_num: {i_llm.get_field('invoice_number')}"
    )
except Exception as e:
    check("E7  extractor_llm provides graceful offline fallback with extraction_method populated", False, str(e))

# E8: Failed Gemini API call surfaces explicit extraction_warning and operator alert
try:
    from extractor_llm import extract_contract as extract_c_llm
    c_failed = extract_c_llm(SAMPLE_CONTRACT_PDF, api_key="INVALID_TEST_KEY_THAT_FORCES_API_FAILURE")
    has_warning = c_failed.extraction_warning is not None and "AI extraction failed" in c_failed.extraction_warning
    is_fallback = c_failed.extraction_method == "pdfplumber-fallback"

    c_failed.human_confirmed = True
    auditor_failed = ContractAuditor(c_failed)
    rep_failed = auditor_failed.audit_invoice(copy.deepcopy(INVOICE))
    notice_text = ContractAuditor.generate_dispute_notice(rep_failed)

    rep_has_warning = any("AI Extraction Alert" in w for w in rep_failed.warnings)
    notice_has_alert = "OPERATOR ALERT" in notice_text and "AI Extraction Alert" in notice_text

    check(
        "E8  Failed Gemini API call sets extraction_warning and surfaces alert in dispute notice",
        has_warning and is_fallback and rep_has_warning and notice_has_alert,
        f"has_warning={has_warning}, is_fallback={is_fallback}, rep_has_warning={rep_has_warning}, notice_alert={notice_has_alert}"
    )
except Exception as e:
    check("E8  Failed Gemini API call sets extraction_warning and surfaces alert in dispute notice", False, str(e))


# ============================================================================
# ITEM 5: Bad Invoice Data Validation
# ============================================================================

# V1: Rate * quantity != billed_amount generates a warning
def v1(i):
    i["line_items"]["linehaul"]["billed_amount"] = 3000.00
    i["total_billed_amount"] = 4043.85
r = audit(invoice_edit=v1)
check("V1  Rate*qty != amount generates a warning in the report",
      len(r.warnings) > 0 and any("arithmetic check" in w.lower() for w in r.warnings),
      f"warnings: {r.warnings}")

# V2: Positive rebate is flagged as a charge
def v2(i):
    i["line_items"]["volume_discount"]["billed_amount"] = 174.00
    i["total_billed_amount"] = 5350.35
r = audit(invoice_edit=v2)
has_rebate_disc = any("Rebate" in d.category for d in r.discrepancies)
all_positive = all(d.overcharge > 0 for d in r.discrepancies)
check("V2  Positive rebate is flagged as a charge (not abs-ed into credit)",
      has_rebate_disc and all_positive,
      f"discrepancies: {[(d.category, float(d.overcharge)) for d in r.discrepancies]}")

# V3: Invoice total mismatch generates warning
def v3(i):
    i["total_billed_amount"] = 9999.99
r = audit(invoice_edit=v3)
check("V3  Invoice total != sum of lines generates a warning",
      any("does not match" in w.lower() or "variance" in w.lower() for w in r.warnings),
      f"warnings: {r.warnings}")

# V4: Detention rate*hours != amount generates warning
def v4(i):
    i["line_items"]["detention"] = {"billed_hours": 4.0, "billed_hourly_rate": 75.0, "billed_amount": 500.0}
    i["total_billed_amount"] = 5376.35
r = audit(invoice_edit=v4)
check("V4  Detention rate*hours != amount generates warning",
      any("Detention arithmetic" in w for w in r.warnings),
      f"warnings: {r.warnings}")

# V5: Reconciliation on Bad Header Total (billed == allowed + leakage without phantom payable)
def v5(i):
    i["total_billed_amount"] = 6000.00  # lines sum to 5176.35, difference of 823.65
r_v5 = audit(invoice_edit=v5)
v5_reconciles = reconciles(r_v5)
check("V5  Bad header total still reconciles: billed == allowed + leakage without inflating payable",
      v5_reconciles and r_v5.total_billed == Decimal("6000.00") and r_v5.total_allowed == Decimal("3823.60"),
      f"billed {r_v5.total_billed} vs allowed {r_v5.total_allowed} + leakage {r_v5.total_leakage}")

# V5b: Strict validation raises InvoiceValidationError on bad header total
try:
    audit(invoice_edit=v5, strict=True)
    check("V5b Strict validation raises InvoiceValidationError on header mismatch", False, "did not raise")
except InvoiceValidationError:
    check("V5b Strict validation raises InvoiceValidationError on header mismatch", True)
except Exception as e:
    check("V5b Strict validation raises InvoiceValidationError on header mismatch", False, f"raised {type(e).__name__}")

# V6: Header shortfall does not produce negative payable or double count
def v6(i):
    i["line_items"]["linehaul"]["billed_amount"] = 90000.00
    i["total_billed_amount"] = 5176.35  # stated total is far less than lines!
r_v6 = audit(invoice_edit=v6)
check("V6  Header shortfall does not produce negative payable: total_allowed >= 0",
      r_v6.total_allowed >= Decimal("0.00") and reconciles(r_v6),
      f"total_allowed: {r_v6.total_allowed}")

check("V6b Header shortfall does not produce >100% leakage: total_leakage <= total_billed",
      r_v6.total_leakage <= r_v6.total_billed and r_v6.leakage_percent <= Decimal("100.00"),
      f"total_leakage: {r_v6.total_leakage}, total_billed: {r_v6.total_billed}, pct: {r_v6.leakage_percent}")


# ============================================================================
# ITEM 6: Rebate Rules
# ============================================================================

# R1: Volume of 51 qualifies (> 50)
r = audit(invoice_edit=lambda i: i.update(monthly_volume_shipments_to_date=51))
check("R1  Volume of 51 qualifies for rebate (> 50)",
      any("Rebate" in d.category for d in r.discrepancies),
      "no rebate discrepancy found")

# R2: Volume of 49 does not qualify
r = audit(invoice_edit=lambda i: i.update(monthly_volume_shipments_to_date=49))
check("R2  Volume of 49 does not qualify for rebate",
      not any("Rebate" in d.category for d in r.discrepancies),
      "engine demanded rebate at 49 loads")

# R3: External rebate_base is used for calculation
def r3(i):
    i["monthly_volume_shipments_to_date"] = 62
    i["rebate_base"] = 50000.00
r = audit(invoice_edit=r3)
rebate_disc = [d for d in r.discrepancies if "Rebate" in d.category]
check("R3  External rebate_base is used for calculation",
      len(rebate_disc) > 0 and rebate_disc[0].overcharge == Decimal("2500.00"),
      f"got {rebate_disc[0].overcharge if rebate_disc else None}")

# R4: rebate_base_assumption is set in report
check("R4  rebate_base_assumption is set in report",
      len(r.rebate_base_assumption) > 0,
      "assumption string is empty")


# ============================================================================
# ITEM 7: Buyer-Side Telemetry
# ============================================================================

# B1: Buyer actual_miles overrides vendor miles
def b1(i):
    i["line_items"]["linehaul"]["actual_miles"] = 1200
r = audit(invoice_edit=b1)
check("B1  Buyer actual_miles (1200) overrides vendor miles (1450)",
      any("buyer verified miles" in w.lower() or "buyer telemetry" in w.lower() for w in r.warnings),
      f"warnings: {r.warnings}")

# B2: Missing actual_miles generates unverified telemetry warning
r = audit()
check("B2  Missing actual_miles generates unverified telemetry warning",
      any("cannot be independently verified" in w.lower() for w in r.warnings),
      f"warnings: {r.warnings}")

# B3: Buyer shipment_count overrides vendor count
def b3(i):
    i["shipment_count"] = 40  # vendor says 62, buyer says 40
r = audit(invoice_edit=b3)
check("B3  Buyer shipment_count (40) overrides vendor count (62)",
      any("buyer records show 40" in w for w in r.warnings) and not any("Rebate" in d.category for d in r.discrepancies),
      f"warnings: {r.warnings}")

# B4: Detention free hours applied per stop
def b4(i):
    i["line_items"]["detention"] = {
        "billed_hours": 4.0, "billed_hourly_rate": 75.0, "billed_amount": 300.0,
        "num_stops": 2,
    }
r = audit(invoice_edit=b4)
det_disc = [d for d in r.discrepancies if "Detention" in d.category]
check("B4  Detention free hours applied per stop (2 stops x 2hr = 4hr free)",
      len(det_disc) > 0 and det_disc[0].overcharge == Decimal("300.00"),
      f"got {det_disc[0].overcharge if det_disc else None}")


# ============================================================================
# ITEM 8: Dispute Letter Safety & Section Separation (S1 - S7)
# ============================================================================

notice = ContractAuditor.generate_dispute_notice(audit())

check("S1  Dispute notice is marked as DRAFT", "DRAFT" in notice)
check("S2  No 'in full satisfaction' language in notice", "in full satisfaction" not in notice.lower())
check("S3  No rigid 'five (5) business days' deadline", "five (5) business days" not in notice)
check("S4  Notice has human approval block", "Reviewed by" in notice and "Approved by" in notice)

# S4b: Reviewer name dynamically populates in dispute notice human approval block
notice_signed = ContractAuditor.generate_dispute_notice(r, reviewer_name="Eleanor Vance (Lead Auditor)")
check(
    "S4b Reviewer name populates in approval block instead of blank underscores",
    "Eleanor Vance (Lead Auditor)" in notice_signed and "Reviewed by:  Eleanor Vance" in notice_signed,
    "Expected reviewer name in approval block"
)

from datetime import date
today_str = date.today().strftime("%B %d, %Y")
check("S5  Date uses today's actual date", today_str in notice)
check("S6  Notice explicitly requires review before sending", "internal review only" in notice.lower())

# S7: Explicit separation of Confirmed Findings vs Needs-Review / Unverified Telemetry
check(
    "S7a Notice contains explicit Section 1 (Confirmed) and Section 2 (Needs-Review) headers",
    "SECTION 1: CONFIRMED FINDINGS" in notice and "SECTION 2: NEEDS-REVIEW" in notice,
    "Missing explicit section separation headers in notice"
)

# When buyer telemetry is NOT verified, unverified items appear under Section 2
r_unverified = audit()
notice_unconf = ContractAuditor.generate_dispute_notice(r_unverified)
check(
    "S7b Unverified telemetry items appear under Section 2 (Needs-Review)",
    "SECTION 2: NEEDS-REVIEW / UNVERIFIED ITEMS" in notice_unconf and "[R1]" in notice_unconf,
    "Expected unverified telemetry items under Section 2"
)

# When buyer telemetry IS verified, findings move to Section 1 (Confirmed)
def add_telemetry(i):
    i["line_items"]["linehaul"]["actual_miles"] = 1450
    i["shipment_count"] = 62
r_verified = audit(invoice_edit=add_telemetry)
notice_verified = ContractAuditor.generate_dispute_notice(r_verified)
check(
    "S7c Verified buyer telemetry findings appear under Section 1 (Confirmed Findings)",
    "SECTION 1: CONFIRMED FINDINGS" in notice_verified and "[1] LINEHAUL FREIGHT RATE OVERCHARGE" in notice_verified,
    "Expected verified items under Section 1"
)


# ============================================================================
# ITEM 9: Batch Auditing, Duplicate Detection & Cumulative Rollup (BT1 - BT4)
# ============================================================================
from batch_auditor import BatchAuditor, BatchAuditSummary

BATCH_DIR = os.path.join(BASE_DIR, "sample_data", "batch")
b1_path = os.path.join(BATCH_DIR, "invoice_batch_1.json")
b2_path = os.path.join(BATCH_DIR, "invoice_batch_2.json")
b_dup_path = os.path.join(BATCH_DIR, "invoice_batch_duplicate.json")

# Pass confirmed contract
c_contract_conf = extract_contract_from_pdf(SAMPLE_CONTRACT_PDF)
c_contract_conf.human_confirmed = True
batch_engine = BatchAuditor(c_contract_conf)

batch_res = batch_engine.audit_batch([b1_path, b2_path, b_dup_path], reviewer_name="Audit Lead")

# BT1: Duplicate invoice detection
check(
    "BT1 Duplicate invoice detection flags duplicate invoice ID (INV-8801)",
    any(a.duplicate_type == "DUPLICATE_INVOICE_NUMBER" and a.invoice_number == "INV-8801" for a in batch_res.duplicate_alerts),
    f"alerts: {batch_res.duplicate_alerts}"
)

# BT2: Duplicate invoice is 100% disallowed
dup_report = next((r for r in batch_res.reports if r.invoice_number == "INV-8801" and r.total_allowed == Decimal("0.00")), None)
check(
    "BT2 Duplicate invoice is 100% disallowed ($0.00 allowed, 100% leakage)",
    dup_report is not None and dup_report.total_allowed == Decimal("0.00") and dup_report.total_leakage == dup_report.total_billed,
    f"dup_report: {dup_report}"
)

# BT3: Cumulative volume rollup triggers volume rebate threshold (>50 loads)
check(
    "BT3 Cumulative volume rollup across non-duplicate invoices (28+27=55) triggers rebate threshold",
    batch_res.cumulative_shipments == 55 and batch_res.volume_threshold_reached is True,
    f"shipments: {batch_res.cumulative_shipments}, threshold_reached: {batch_res.volume_threshold_reached}"
)

# BT4: Master batch ledger records and CSV export
csv_data = batch_res.to_csv()
check(
    "BT4 Master batch ledger and CSV export generate valid records",
    len(batch_res.ledger_records) == 3
    and "INV-8801" in csv_data
    and "INV-8802" in csv_data
    and "DUPLICATE" in csv_data,
    f"records: {len(batch_res.ledger_records)}, csv len: {len(csv_data)}"
)


# ============================================================================
# ITEM 10: SQLite Audit Trail & Historical Persistence (H1 - H8)
# ============================================================================
from audit_store import (
    init_db,
    save_audit_run,
    get_audit_history,
    get_audit_by_id,
    clear_history,
    export_history_to_csv,
)

temp_ext_db = tempfile.mktemp(suffix=".db")
try:
    init_db(temp_ext_db)
    
    # H1: Save single audit
    id1 = save_audit_run(
        reviewer_name="Eleanor Vance",
        audit_mode="single",
        total_billed=Decimal("5176.35"),
        total_allowed=Decimal("3823.60"),
        total_leakage=Decimal("1352.75"),
        leakage_percent=Decimal("26.13"),
        invoice_identifier="INV-9042",
        details={"invoice_number": "INV-9042", "vendor": "SwiftLine"},
        db_path=temp_ext_db,
    )
    check("H1 save_audit_run saves single audit run and returns UUID", isinstance(id1, str) and len(id1) > 10)

    # H2: Save batch audit
    id2 = save_audit_run(
        reviewer_name="Marcus Reed",
        audit_mode="batch",
        total_billed=Decimal("10250.00"),
        total_allowed=Decimal("7500.00"),
        total_leakage=Decimal("2750.00"),
        leakage_percent=Decimal("26.83"),
        invoice_identifier="Batch (3 Invoices)",
        details=batch_res.to_dict(),
        db_path=temp_ext_db,
    )
    check("H2 save_audit_run saves batch audit run with details blob", isinstance(id2, str) and id2 != id1)

    # H3: Retrieve sorted history
    hist = get_audit_history(db_path=temp_ext_db)
    check("H3 get_audit_history returns all audits sorted by timestamp DESC", len(hist) == 2 and hist[0]["audit_id"] == id2)

    # H4: Filter by mode
    s_only = get_audit_history(audit_mode="single", db_path=temp_ext_db)
    b_only = get_audit_history(audit_mode="batch", db_path=temp_ext_db)
    check("H4 get_audit_history filters accurately by audit_mode", len(s_only) == 1 and len(b_only) == 1 and s_only[0]["audit_id"] == id1)

    # H5: Filter by reviewer
    ev_runs = get_audit_history(reviewer_name="Eleanor", db_path=temp_ext_db)
    check("H5 get_audit_history filters by reviewer_name substring", len(ev_runs) == 1 and ev_runs[0]["reviewer_name"] == "Eleanor Vance")

    # H6: Get by ID & parse JSON details
    audit_rec = get_audit_by_id(id1, db_path=temp_ext_db)
    check("H6 get_audit_by_id retrieves parsed JSON details", audit_rec is not None and audit_rec["details"]["invoice_number"] == "INV-9042")

    # H7: Export history to CSV
    h_csv = export_history_to_csv(db_path=temp_ext_db)
    check("H7 export_history_to_csv produces valid CSV headers and rows", "Audit ID" in h_csv and "INV-9042" in h_csv and "5176.35" in h_csv)

    # H8: Clear history
    n_cleared = clear_history(db_path=temp_ext_db)
    check("H8 clear_history empties database table and returns rowcount", n_cleared == 2 and len(get_audit_history(db_path=temp_ext_db)) == 0)

finally:
    if os.path.exists(temp_ext_db):
        try:
            os.unlink(temp_ext_db)
        except OSError:
            pass


# ============================================================================
# ITEM 11: Real Client Contracts — Lane Rates & Accessorial Caps (P1 - P4)
# ============================================================================
contract_with_lanes_acc = copy.deepcopy(CONTRACT)
contract_with_lanes_acc["terms"]["base_rate_per_mile"] = 3.00
contract_with_lanes_acc["terms"]["lane_rates"] = {
    "Chicago-Dallas": 2.40,
    "Atlanta-Miami": 2.65,
}
contract_with_lanes_acc["terms"]["accessorial_caps"] = {
    "lumper_fee": 150.00,
    "liftgate": 75.00,
    "layover": 250.00,
    "tonu": 200.00,
}

auditor_lanes = ContractAuditor(contract_with_lanes_acc)

# P1: Lane rate overrides general base rate
inv_p1 = {
    "invoice_number": "INV-P1",
    "total_billed_amount": 2850.00,
    "line_items": {
        "linehaul": {
            "lane": "Chicago-Dallas",
            "billed_miles": 1000,
            "billed_rate_per_mile": 2.85,
            "billed_amount": 2850.00,
            "actual_miles": 1000,
        }
    }
}
rep_p1 = auditor_lanes.audit_invoice(inv_p1)
lh_disc_p1 = next((d for d in rep_p1.discrepancies if d.category == "Linehaul Freight Rate Overcharge"), None)
check(
    "P1 Lane rate lookup (Chicago-Dallas @ $2.40) overrides general rate ($3.00) and detects overcharge",
    lh_disc_p1 is not None and lh_disc_p1.allowed_amount == Decimal("2400.00") and lh_disc_p1.overcharge == Decimal("450.00"),
    f"disc: {lh_disc_p1}"
)

# P2: Missing or uncontracted lane gracefully falls back to base_rate_per_mile
inv_p2 = {
    "invoice_number": "INV-P2",
    "total_billed_amount": 2850.00,
    "line_items": {
        "linehaul": {
            "lane": "Denver-Phoenix",
            "billed_miles": 1000,
            "billed_rate_per_mile": 2.85,
            "billed_amount": 2850.00,
            "actual_miles": 1000,
        }
    }
}
rep_p2 = auditor_lanes.audit_invoice(inv_p2)
lh_disc_p2 = next((d for d in rep_p2.discrepancies if d.category == "Linehaul Freight Rate Overcharge"), None)
check(
    "P2 Uncontracted lane gracefully falls back to base_rate_per_mile ($3.00) with zero overcharge",
    lh_disc_p2 is None and rep_p2.total_allowed == Decimal("2850.00"),
    f"disc: {lh_disc_p2}, rep: {rep_p2}"
)

# P3: Accessorial cap breach (e.g. lumper billed $225 vs cap $150 -> $75 overcharge)
inv_p3 = {
    "invoice_number": "INV-P3",
    "total_billed_amount": 3225.00,
    "line_items": {
        "linehaul": {
            "billed_miles": 1000,
            "billed_rate_per_mile": 3.00,
            "billed_amount": 3000.00,
            "actual_miles": 1000,
        },
        "lumper": {
            "billed_amount": 225.00,
        }
    }
}
rep_p3 = auditor_lanes.audit_invoice(inv_p3)
acc_disc_p3 = next((d for d in rep_p3.discrepancies if "Accessorial Fee Cap Breach" in d.category), None)
check(
    "P3 Accessorial cap breach (lumper $225 vs cap $150) flags $75 overcharge and excludes from unreviewed",
    acc_disc_p3 is not None
    and acc_disc_p3.allowed_amount == Decimal("150.00")
    and acc_disc_p3.overcharge == Decimal("75.00")
    and len(rep_p3.unreviewed_lines) == 0
    and rep_p3.total_allowed == Decimal("3150.00")
    and rep_p3.total_leakage == Decimal("75.00"),
    f"acc_disc: {acc_disc_p3}, unreviewed: {rep_p3.unreviewed_lines}, rep: {rep_p3}"
)

# P4: Accessorial fee within contract cap
inv_p4 = {
    "invoice_number": "INV-P4",
    "total_billed_amount": 3075.00,
    "line_items": {
        "linehaul": {
            "billed_miles": 1000,
            "billed_rate_per_mile": 3.00,
            "billed_amount": 3000.00,
            "actual_miles": 1000,
        },
        "liftgate": {
            "billed_amount": 75.00,
        }
    }
}
rep_p4 = auditor_lanes.audit_invoice(inv_p4)
acc_disc_p4 = next((d for d in rep_p4.discrepancies if "Accessorial Fee Cap Breach" in d.category), None)
check(
    "P4 Accessorial fee within cap ($75 vs $75) allows in full without overcharge or unreviewed flag",
    acc_disc_p4 is None
    and len(rep_p4.unreviewed_lines) == 0
    and rep_p4.total_allowed == Decimal("3075.00")
    and rep_p4.total_leakage == Decimal("0.00"),
    f"acc_disc: {acc_disc_p4}, unreviewed: {rep_p4.unreviewed_lines}, rep: {rep_p4}"
)

# P5: Accessorial semantic alias resolution ('unloading_labor' -> 'lumper' cap $150.00)
inv_p5 = {
    "invoice_number": "INV-P5",
    "total_billed_amount": 3225.00,
    "line_items": {
        "linehaul": {
            "billed_miles": 1000,
            "billed_rate_per_mile": 3.00,
            "billed_amount": 3000.00,
            "actual_miles": 1000,
        },
        "unloading_labor": {
            "billed_amount": 225.00,
        }
    }
}
rep_p5 = auditor_lanes.audit_invoice(inv_p5)
acc_disc_p5 = next((d for d in rep_p5.discrepancies if "Accessorial Fee Cap Breach" in d.category), None)
check(
    "P5 Accessorial semantic alias ('unloading_labor') resolves to lumper cap ($150) and isolates $75 overcharge",
    acc_disc_p5 is not None
    and acc_disc_p5.allowed_amount == Decimal("150.00")
    and acc_disc_p5.overcharge == Decimal("75.00")
    and len(rep_p5.unreviewed_lines) == 0
    and rep_p5.total_allowed == Decimal("3150.00")
    and rep_p5.total_leakage == Decimal("75.00"),
    f"acc_disc: {acc_disc_p5}, unreviewed: {rep_p5.unreviewed_lines}, rep: {rep_p5}"
)

# P6: Accessorial semantic alias resolution ('tailgate' -> 'liftgate' cap $75.00)
inv_p6 = {
    "invoice_number": "INV-P6",
    "total_billed_amount": 3100.00,
    "line_items": {
        "linehaul": {
            "billed_miles": 1000,
            "billed_rate_per_mile": 3.00,
            "billed_amount": 3000.00,
            "actual_miles": 1000,
        },
        "tailgate": {
            "billed_amount": 100.00,
        }
    }
}
rep_p6 = auditor_lanes.audit_invoice(inv_p6)
acc_disc_p6 = next((d for d in rep_p6.discrepancies if "Accessorial Fee Cap Breach" in d.category), None)
check(
    "P6 Accessorial semantic alias ('tailgate') resolves to liftgate cap ($75) and isolates $25 overcharge",
    acc_disc_p6 is not None
    and acc_disc_p6.allowed_amount == Decimal("75.00")
    and acc_disc_p6.overcharge == Decimal("25.00")
    and len(rep_p6.unreviewed_lines) == 0
    and rep_p6.total_allowed == Decimal("3075.00")
    and rep_p6.total_leakage == Decimal("25.00"),
    f"acc_disc: {acc_disc_p6}, unreviewed: {rep_p6.unreviewed_lines}, rep: {rep_p6}"
)

# P7: Progressive tiered ocean container demurrage schedule (FMC Tariff ZIMU-136)
contract_ocean = copy.deepcopy(contract_with_lanes_acc)
contract_ocean["terms"]["demurrage_tiers"] = [
    {"from_unit": 5, "to_unit": 8, "rate": 245.00},
    {"from_unit": 9, "to_unit": 12, "rate": 290.00},
    {"from_unit": 13, "to_unit": None, "rate": 335.00},
]
auditor_ocean = ContractAuditor(contract_ocean)
# Invoice with 6 due days starting at Day 5: 4 days @ $245 ($980) + 2 days @ $290 ($580) = $1,560.00 allowed.
# Vendor billed inflated flat rate of $300/day = $1,800.00 -> Overcharge = $240.00
inv_ocean = {
    "invoice_number": "INV-P7-OCEAN",
    "total_billed_amount": 1800.00,
    "line_items": {
        "detention": {
            "billed_days": 6,
            "start_day": 5,
            "billed_amount": 1800.00,
        }
    }
}
rep_ocean = auditor_ocean.audit_invoice(inv_ocean)
dem_disc_ocean = next((d for d in rep_ocean.discrepancies if "Tiered Demurrage" in d.category), None)
check(
    "P7 Progressive tiered demurrage schedule (FMC Tariff ZIMU-136) calculates exact $1,560.00 allowance and $240.00 overcharge",
    dem_disc_ocean is not None
    and dem_disc_ocean.allowed_amount == Decimal("1560.00")
    and dem_disc_ocean.overcharge == Decimal("240.00")
    and rep_ocean.total_allowed == Decimal("1560.00")
    and rep_ocean.total_leakage == Decimal("240.00"),
    f"dem_disc: {dem_disc_ocean}, rep: {rep_ocean}"
)


# ============================================================================
# Summary
# ============================================================================
print(f"\nEXTENDED SUITE SUMMARY: {passed} passed, {failed} failed")
