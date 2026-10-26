import os
import fitz
from audit_engine import extract_contract_from_pdf as regex_extract_contract
import extractor_llm

def create_schedule_style_pdf():
    diag_dir = os.path.join(os.path.dirname(__file__), "sample_data", "diagnostic")
    os.makedirs(diag_dir, exist_ok=True)
    pdf_path = os.path.join(diag_dir, "contract_schedule_style.pdf")

    text = """SCHEDULE 3 — CARRIAGE CHARGES AND ALLOWANCES
This Schedule forms part of the Framework Carriage Agreement between
Global Retail Group Inc. and SwiftLine National Freight Corp.

Item (i) — Mileage Charge
The per-mile charge payable by the Customer for standard dry-van loads
shall not, under any circumstances, exceed USD 2.40.

Item (ii) — Fuel Recovery
Any fuel recovery surcharge invoiced hereunder is limited to a ceiling
of 12% calculated on the mileage charge.

Item (iii) — Waiting Time Allowance
No charge is payable for the first two hours of waiting time at a
collection or delivery point. Thereafter, the applicable rate is USD 50
per hour or part thereof.

Item (iv) — Rebate for Volume
Where the Customer tenders more than fifty (50) loads within a calendar
month, SwiftLine shall apply a rebate of 5% to the mileage charges for
that month."""

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), text, fontsize=11)
    doc.save(pdf_path)
    doc.close()
    return pdf_path

def evaluate_fields(res, expected):
    fields_dict = {f.field_name: f.value for f in res.fields}
    results = {}
    for key, exp_val in expected.items():
        extracted_val = fields_dict.get(key)
        if extracted_val is None:
            results[key] = {"status": "MISSED", "expected": exp_val, "extracted": None}
        elif extracted_val == exp_val or (isinstance(extracted_val, float) and abs(extracted_val - float(exp_val)) < 0.001):
            results[key] = {"status": "CORRECT", "expected": exp_val, "extracted": extracted_val}
        else:
            results[key] = {"status": "WRONG", "expected": exp_val, "extracted": extracted_val}
    return results

if __name__ == "__main__":
    pdf_path = create_schedule_style_pdf()
    gemini_key = os.environ.get("GEMINI_API_KEY")

    expected_terms = {
        "base_rate_per_mile": 2.40,
        "max_fuel_surcharge_percent": 12.0,
        "free_detention_hours": 2.0,
        "detention_hourly_rate": 50.0,
        "volume_discount_threshold_shipments": 50,
        "volume_discount_percent": 5.0,
    }

    print("=" * 70)
    print("EXTRACTION DIAGNOSTIC RUN REPORT: SCHEDULE STYLE CONTRACT")
    print("=" * 70)
    print(f"GEMINI_API_KEY Available: {bool(gemini_key)}")
    print(f"File: {pdf_path}")
    print("-" * 70)

    # 1. Deterministic Fallback Parser (regex)
    print("\n[1] Deterministic Fallback Parser (audit_engine.extract_contract_from_pdf):")
    res_regex = regex_extract_contract(pdf_path)
    print(f"  Method invoked: {res_regex.extraction_method}")
    eval_regex = evaluate_fields(res_regex, expected_terms)
    for field, r in eval_regex.items():
        print(f"  • {field:35s}: [{r['status']:7s}] Expected: {r['expected']} | Extracted: {r['extracted']}")

    # 2. Multimodal LLM Module (extractor_llm.extract_contract)
    print("\n[2] Multimodal LLM Module (extractor_llm.extract_contract):")
    res_llm = extractor_llm.extract_contract(pdf_path)
    print(f"  Method invoked: {res_llm.extraction_method}")
    eval_llm = evaluate_fields(res_llm, expected_terms)
    for field, r in eval_llm.items():
        print(f"  • {field:35s}: [{r['status']:7s}] Expected: {r['expected']} | Extracted: {r['extracted']}")

    print("\n  Verbatim Supporting Clauses Extracted by Gemini:")
    for f in res_llm.fields:
        if f.field_name in expected_terms:
            print(f"    • {f.field_name:35s}: \"{f.quoted_text}\" (Page {f.page_number}, Conf: {f.confidence})")

    print("\n" + "=" * 70)
