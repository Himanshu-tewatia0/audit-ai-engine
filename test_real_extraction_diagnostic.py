"""
Diagnostic Test Script for Real-World Document Extraction
Evaluates extraction performance on:
  a) Naturally worded contract (lawyer-drafted, no 'Section 4.1' boilerplate)
  b) Scanned / raster image document (image-based PDF with visual artifacts and no text layer)
Reports extraction results field-by-field.
"""

import os
import fitz  # PyMuPDF
from audit_engine import extract_contract_from_pdf as regex_extract_contract
import extractor_llm

def create_diagnostic_documents():
    diag_dir = os.path.join(os.path.dirname(__file__), "sample_data", "diagnostic")
    os.makedirs(diag_dir, exist_ok=True)

    # 1. Document (a): Naturally worded contract
    doc_a_path = os.path.join(diag_dir, "contract_naturally_worded.pdf")
    doc_a = fitz.open()
    page_a = doc_a.new_page()
    text_a = """MASTER TRANSPORTATION SERVICES AGREEMENT
Contract Reference: TSA-2026-88
Effective: January 15, 2026
Parties: Global Retail Group Inc. (Shipper) and SwiftLine National Freight Corp. (Carrier)

1. Base Transportation Rates:
For all dedicated truckload linehaul moves, Carrier shall bill Shipper at an agreed rate not to exceed two dollars and forty cents (USD $2.40) per mile based on agreed route distance.

2. Diesel Fuel Surcharge Mechanism:
Shipper shall reimburse Carrier for diesel fuel fluctuations; provided, however, that the total fuel surcharge shall be capped at twelve percent (12.0%) of the base freight charges.

3. Facility Waiting & Detention Time:
Carrier agrees that the first two (2.0) hours of detention at origin and destination facilities are provided free of charge. Time accrued beyond this allowance shall be invoiced at the rate of fifty dollars ($50.00) per billable hour.

4. Volume Incentive Program:
Should monthly traffic volume tendered by Shipper surpass fifty (50) dispatched loads in a billing cycle, Carrier shall credit a five percent (5.0%) volume rebate against total qualifying monthly base freight.
"""
    page_a.insert_text((50, 50), text_a, fontsize=11)
    doc_a.save(doc_a_path)
    doc_a.close()

    # 2. Document (b): Scanned image-only PDF (rendered to bitmap image, so no selectable text layer)
    doc_b_path = os.path.join(diag_dir, "contract_scanned_image.pdf")
    doc_tmp = fitz.open()
    page_tmp = doc_tmp.new_page()
    page_tmp.insert_text((50, 50), text_a, fontsize=11)
    pix = page_tmp.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    doc_tmp.close()

    # Create new PDF containing only the raster image
    doc_b = fitz.open()
    page_b = doc_b.new_page(width=pix.width, height=pix.height)
    page_b.insert_image(page_b.rect, stream=img_bytes)
    doc_b.save(doc_b_path)
    doc_b.close()

    return doc_a_path, doc_b_path

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
    doc_a_path, doc_b_path = create_diagnostic_documents()
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
    print("EXTRACTION DIAGNOSTIC RUN REPORT")
    print("=" * 70)
    print(f"GEMINI_API_KEY Available: {bool(gemini_key)}")
    if not gemini_key:
        print("NOTICE: No GEMINI_API_KEY detected in environment.")
    print("-" * 70)

    # Test Document (a): Naturally Worded Contract
    print("\n--- TEST DOCUMENT (A): Naturally Worded Contract (PDF with text layer) ---")
    print(f"File: {doc_a_path}")

    print("\n[A.1] Deterministic Fallback Parser (audit_engine.extract_contract_from_pdf):")
    res_a_regex = regex_extract_contract(doc_a_path)
    eval_a_regex = evaluate_fields(res_a_regex, expected_terms)
    for field, r in eval_a_regex.items():
        print(f"  • {field:35s}: [{r['status']:7s}] Expected: {r['expected']} | Extracted: {r['extracted']}")

    print("\n[A.2] Multimodal LLM Module (extractor_llm.extract_contract):")
    res_a_llm = extractor_llm.extract_contract(doc_a_path)
    print(f"  Method invoked: {res_a_llm.extraction_method}")
    eval_a_llm = evaluate_fields(res_a_llm, expected_terms)
    for field, r in eval_a_llm.items():
        print(f"  • {field:35s}: [{r['status']:7s}] Expected: {r['expected']} | Extracted: {r['extracted']}")

    # Test Document (b): Scanned Image-Only Contract
    print("\n--- TEST DOCUMENT (B): Scanned / Raster Image Contract (Image-only, no text layer) ---")
    print(f"File: {doc_b_path}")

    print("\n[B.1] Deterministic Fallback Parser (audit_engine.extract_contract_from_pdf):")
    res_b_regex = regex_extract_contract(doc_b_path)
    eval_b_regex = evaluate_fields(res_b_regex, expected_terms)
    for field, r in eval_b_regex.items():
        print(f"  • {field:35s}: [{r['status']:7s}] Expected: {r['expected']} | Extracted: {r['extracted']}")

    print("\n[B.2] Multimodal LLM Module (extractor_llm.extract_contract):")
    res_b_llm = extractor_llm.extract_contract(doc_b_path)
    print(f"  Method invoked: {res_b_llm.extraction_method}")
    eval_b_llm = evaluate_fields(res_b_llm, expected_terms)
    for field, r in eval_b_llm.items():
        print(f"  • {field:35s}: [{r['status']:7s}] Expected: {r['expected']} | Extracted: {r['extracted']}")
    print("\n" + "=" * 70)
