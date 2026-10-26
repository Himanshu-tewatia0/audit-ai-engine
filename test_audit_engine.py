"""
Stress tests for audit_engine.py

HOW TO RUN
  1. Put this file in the SAME folder as audit_engine.py
  2. Open a terminal in that folder and run:   python test_audit_engine.py

HOW TO READ THE RESULT
  PASS = the engine behaves correctly in that situation
  FAIL = the engine gives a wrong or dangerous answer (a bug to fix)
After you fix a bug, its line should turn from FAIL to PASS.
"""
import copy
import json
import os
import tempfile

from audit_engine import ContractAuditor

# ---- Sample data rebuilt from your terminal output (INV-9042) -------------
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
        "section_6_2": "Section 6.2: first 2.0 hours free; $50.00/hour after.",
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


def audit(contract_edit=None, invoice_edit=None):
    """Run the engine on a modified copy of the sample data."""
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
    report = ContractAuditor(cp).audit_invoice(ip)
    return report


def reconciles(r):
    """The letter's own numbers must add up: billed - payable = recoverable."""
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


# ---- 0. Baseline: reproduces your terminal output --------------------------
r = audit()
check("T0  Baseline invoice gives 1,352.75 leakage",
      r.total_leakage == 1352.75 and reconciles(r),
      f"got leakage {r.total_leakage}")

# ---- 1. Compliant invoice priced BELOW the caps -----------------------------
def t1(i):
    i["total_billed_amount"] = 3045.00
    i["line_items"] = {
        "linehaul": {"billed_miles": 1450, "billed_rate_per_mile": 2.00, "billed_amount": 2900.00},
        "fuel_surcharge": {"billed_percent": 10.0, "billed_amount": 290.00},
        "detention": {"billed_hours": 0.0, "billed_hourly_rate": 0.0, "billed_amount": 0.0},
        "volume_discount": {"billed_amount": -145.00},
    }
r = audit(invoice_edit=t1)
check("T1  A fully compliant invoice is NOT disputed",
      r.total_leakage == 0 and r.total_allowed <= r.total_billed,
      f"engine claims leakage {r.total_leakage:.2f} and offers to pay {r.total_allowed:.2f} "
      f"on an invoice of {r.total_billed:.2f}")

# ---- 2. Extra line item that has no rule ------------------------------------
def t2(i):
    i["line_items"]["lumper_fee"] = {"billed_amount": 100.00}
    i["total_billed_amount"] = 5276.35
r = audit(invoice_edit=t2)
check("T2  Unknown line items are not silently dropped from the payable amount",
      reconciles(r),
      f"billed {r.total_billed:.2f} - payable {r.total_allowed:.2f} - leakage {r.total_leakage:.2f} "
      f"= {r.total_billed - r.total_allowed - r.total_leakage:.2f} (the $100 line vanished)")

# ---- 3. Missing contract terms ----------------------------------------------
def t3(c):
    c["terms"].pop("base_rate_per_mile")
try:
    r = audit(contract_edit=t3)
    check("T3  Missing base rate stops the audit instead of guessing", False,
          f"engine silently reported {r.leakage_percent}% leakage and offers to pay {r.total_allowed:.2f}")
except Exception:
    check("T3  Missing base rate stops the audit instead of guessing", True)

def t3b(c):
    c["terms"].pop("free_detention_hours")
try:
    r = audit(contract_edit=t3b)
    check("T3b Missing detention terms stop the audit instead of using hidden defaults", False,
          "engine silently assumed 2.0 free hours / $50 per hour from its own code")
except Exception:
    check("T3b Missing detention terms stop the audit instead of using hidden defaults", True)

# ---- 4. Rebate threshold boundary -------------------------------------------
r = audit(invoice_edit=lambda i: i.update(monthly_volume_shipments_to_date=50))
check("T4  Volume of exactly 50 does NOT qualify (clause says 'exceeds 50')",
      not any("Rebate" in d.category for d in r.discrepancies),
      "engine demanded the rebate and wrote '(50) exceeds (50)' in the letter")

# ---- 5. Vendor CHARGED on the rebate line instead of crediting ----------------
def t5(i):
    i["line_items"]["volume_discount"]["billed_amount"] = 174.00
    i["total_billed_amount"] = 5350.35
r = audit(invoice_edit=t5)
check("T5  A positive amount on the rebate line is flagged",
      any("Rebate" in d.category for d in r.discrepancies),
      "engine used abs() and treated a $174 CHARGE as a $174 credit")

# ---- 6. Internally inconsistent invoice ---------------------------------------
def t6(i):
    i["line_items"]["linehaul"]["billed_amount"] = 3000.00
    i["total_billed_amount"] = 4043.85
r = audit(invoice_edit=t6)
check("T6  The letter never contains a negative 'overcharge'",
      all(d.overcharge > 0 for d in r.discrepancies),
      "engine listed overcharges: " + ", ".join(f"{d.overcharge:.2f}" for d in r.discrepancies))

# ---- 8. Fuel label --------------------------------------------------------------
def t8(i):
    i["line_items"]["fuel_surcharge"] = {"billed_percent": 12.0, "billed_amount": 495.90}
    i["total_billed_amount"] = 4928.40
r = audit(invoice_edit=t8)
check("T8  Fuel billed at exactly the 12% cap is not called a 'cap violation'",
      not any("Cap Violation" in d.category for d in r.discrepancies),
      "letter says 'Billed at 12.0% vs contract ceiling of 12.0%' under 'Cap Violation'")

# ---- 9. Sub-cent noise -------------------------------------------------------------
def t9(i):
    i["total_billed_amount"] = 3897.60
    i["monthly_volume_shipments_to_date"] = 10
    i["line_items"] = {
        "linehaul": {"billed_miles": 1450, "billed_rate_per_mile": 2.40, "billed_amount": 3480.004},
        "fuel_surcharge": {"billed_percent": 12.0, "billed_amount": 417.60},
        "detention": {"billed_hours": 0.0, "billed_hourly_rate": 0.0, "billed_amount": 0.0},
        "volume_discount": {"billed_amount": 0.0},
    }
r = audit(invoice_edit=t9)
check("T9  A $0.004 difference does not create a dispute item",
      len(r.discrepancies) == 0,
      f"engine created {len(r.discrepancies)} dispute item(s) worth $0.00")

# ---- 10. Invoice total that does not match its own lines -----------------------------
try:
    r = audit(invoice_edit=lambda i: i.update(total_billed_amount=6000.00))
    warned = bool(getattr(r, "warnings", []))
    check("T10 An invoice whose total does not equal its lines is rejected or warned", warned,
          "engine accepted a stated total of 6,000.00 although the lines add to 5,176.35")
except Exception:
    check("T10 An invoice whose total does not equal its lines is rejected or warned", True)

# ---- 11. Notice formatting ----------------------------------------------------------------
notice = ContractAuditor.generate_dispute_notice(audit())
check("T11 Money is formatted correctly (no '$-0.00' or '$-174.00')",
      "$-" not in notice,
      "notice prints '$-0.00' and '$-174.00' instead of '-$174.00'")

# ---- Known limitation (information only) -----------------------------------------------------
def t7(i):
    i["total_billed_amount"] = 4569.60
    i["monthly_volume_shipments_to_date"] = 10
    i["line_items"] = {
        "linehaul": {"billed_miles": 1700, "billed_rate_per_mile": 2.40, "billed_amount": 4080.00},
        "fuel_surcharge": {"billed_percent": 12.0, "billed_amount": 489.60},
        "detention": {"billed_hours": 0.0, "billed_hourly_rate": 0.0, "billed_amount": 0.0},
        "volume_discount": {"billed_amount": 0.0},
    }
r = audit(invoice_edit=t7)
print(f"[INFO] T7  Vendor pads miles 1,450 -> 1,700 at the correct rate: "
      f"engine flagged {len(r.discrepancies)} items. It trusts the vendor's own miles, "
      f"so it needs the buyer's shipment records to catch this.")

print(f"\nSUMMARY: {passed} passed, {failed} failed")
