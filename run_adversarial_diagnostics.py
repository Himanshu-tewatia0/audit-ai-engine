import os
import time
import fitz
from audit_engine import extract_contract_from_pdf as regex_extract_contract
import extractor_llm
from google import genai
from google.genai import types
from extractor_llm import ContractExtractionSchema, CONTRACT_EXTRACTION_PROMPT, GEMINI_DEFAULT_MODEL

DIAG_DIR = os.path.join(os.path.dirname(__file__), "sample_data", "diagnostic")
os.makedirs(DIAG_DIR, exist_ok=True)

EXPECTED_TERMS = {
    "base_rate_per_mile": 2.40,
    "max_fuel_surcharge_percent": 12.0,
    "free_detention_hours": 2.0,
    "detention_hourly_rate": 50.0,
    "volume_discount_threshold_shipments": 50,
    "volume_discount_percent": 5.0,
}

def ensure_scanned_pdf():
    doc_b_path = os.path.join(DIAG_DIR, "contract_scanned_image.pdf")
    if not os.path.exists(doc_b_path):
        text_a = """MASTER TRANSPORTATION SERVICES AGREEMENT
Contract Reference: TSA-2026-88
Parties: Global Retail Group Inc. and SwiftLine National Freight Corp.

1. Base Transportation Rates:
For all dedicated truckload linehaul moves, Carrier shall bill Shipper at an agreed rate not to exceed two dollars and forty cents (USD $2.40) per mile based on agreed route distance.

2. Diesel Fuel Surcharge Mechanism:
Shipper shall reimburse Carrier for diesel fuel fluctuations; provided, however, that the total fuel surcharge shall be capped at twelve percent (12.0%) of the base freight charges.

3. Facility Waiting & Detention Time:
Carrier agrees that the first two (2.0) hours of detention at origin and destination facilities are provided free of charge. Time accrued beyond this allowance shall be invoiced at the rate of fifty dollars ($50.00) per billable hour.

4. Volume Incentive Program:
Should monthly traffic volume tendered by Shipper surpass fifty (50) dispatched loads in a billing cycle, Carrier shall credit a five percent (5.0%) volume rebate against total qualifying monthly base freight."""
        doc_tmp = fitz.open()
        page_tmp = doc_tmp.new_page()
        page_tmp.insert_text((50, 50), text_a, fontsize=11)
        pix = page_tmp.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        doc_tmp.close()

        doc_b = fitz.open()
        page_b = doc_b.new_page(width=pix.width, height=pix.height)
        page_b.insert_image(page_b.rect, stream=img_bytes)
        doc_b.save(doc_b_path)
        doc_b.close()
    return doc_b_path

def create_table_format_pdf():
    path = os.path.join(DIAG_DIR, "contract_table_format.pdf")
    doc = fitz.open()
    page = doc.new_page()

    header_text = """CARRIER RATE SCHEDULE & PRICING MATRIX
Contract ID: MSA-2026-GRID-01
Parties: Global Retail Group Inc. (Shipper) & SwiftLine National Freight Corp. (Carrier)
Effective Date: February 1, 2026

The following matrix governs all billable transportation charges:
"""
    page.insert_text((50, 50), header_text, fontsize=11)

    table_text = """+------------------------------------+----------------+--------------------------------------------------+
| Service Component / Description    | Contract Cap   | Standard Application Rules                       |
+------------------------------------+----------------+--------------------------------------------------+
| Standard Dry-Van Linehaul Rate     | $2.40 / mile   | Maximum allowed charge per audited route mile    |
| Fuel Surcharge Ceiling             | 12.0%          | Maximum percentage applied on linehaul base      |
| Facility Detention Grace Period    | 2.0 hours      | Free time allowed at origin & destination stops  |
| Billable Detention Hourly Rate     | $50.00 / hour  | Hourly rate assessed after 2.0 free grace hours  |
| Monthly Volume Threshold           | 50 loads       | Qualifying monthly dispatched loads required     |
| Volume Discount Rebate Percentage  | 5.0%           | Credit applied against qualifying monthly freight|
+------------------------------------+----------------+--------------------------------------------------+"""
    page.insert_text((50, 150), table_text, fontname="Courier", fontsize=8.5)
    doc.save(path)
    doc.close()
    return path

def create_decoy_pdf():
    path = os.path.join(DIAG_DIR, "contract_with_decoy.pdf")
    doc = fitz.open()
    page = doc.new_page()

    text = """MASTER LOGISTICS SERVICES AGREEMENT
Contract Number: MSA-2026-MULTI-MODAL
Shipper: Global Retail Group Inc.
Carrier: SwiftLine National Freight Corp.

Section 1. Standard Motor Carrier Linehaul Operations:
For all primary over-the-road dedicated dry-van truckload shipments, Carrier shall charge a linehaul rate not to exceed $2.40 per route mile.
[NOTE: For non-primary intermodal container rail drayage moves under 50 miles, a separate flat local drayage rate of $3.85 per mile applies.]

Section 2. Fuel Adjustment Formulas:
For standard truckload freight governed by Section 1, the diesel fuel surcharge billed to Shipper is subject to a strict ceiling of twelve percent (12.0%) of the base linehaul charge.
[DECOY CLAUSE: In contrast, temperature-controlled refrigerated shipments and hazardous cargo are subject to a separate peak fuel surcharge mechanism capped at 18.5% of gross freight.]

Section 3. Waiting & Loading Allowances:
Carrier provides two (2.0) hours of free waiting time at all shipper origin and consignee destination facilities. Any authorized detention exceeding this two-hour allowance shall be billed at $50.00 per hour.
[DECOY CLAUSE: Weekend layover and 34-hour driver restart delay fees are billed separately at a flat penalty rate of $90.00 per hour with zero free grace period.]

Section 4. Volume Incentive Terms:
If Shipper tenders in excess of fifty (50) qualifying dispatched truckload shipments in a given calendar month, Carrier shall apply a five percent (5.0%) volume rebate credit to the cumulative monthly linehaul spend.
[DECOY CLAUSE: A separate international ocean freight incentive rebate of 2.5% applies only if global quarterly volume exceeds 500 TEU maritime containers.]"""

    page.insert_text((50, 50), text, fontsize=10)
    doc.save(path)
    doc.close()
    return path

def evaluate_fields(res, expected):
    fields_dict = {f.field_name: f.value for f in res.fields}
    results = {}
    for key, exp_val in expected.items():
        extracted_val = fields_dict.get(key)
        if extracted_val is None:
            results[key] = {"status": "MISSED", "expected": exp_val, "extracted": None}
        elif extracted_val == exp_val or (isinstance(extracted_val, (int, float)) and abs(float(extracted_val) - float(exp_val)) < 0.001):
            results[key] = {"status": "CORRECT", "expected": exp_val, "extracted": extracted_val}
        else:
            results[key] = {"status": "WRONG", "expected": exp_val, "extracted": extracted_val}
    return results

def get_token_usage_and_timing(pdf_path, api_key):
    client = genai.Client(api_key=api_key)
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    
    t0 = time.perf_counter()
    response = client.models.generate_content(
        model=GEMINI_DEFAULT_MODEL,
        contents=[part, CONTRACT_EXTRACTION_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ContractExtractionSchema,
            temperature=0.0,
        ),
    )
    latency = time.perf_counter() - t0
    
    usage = getattr(response, "usage_metadata", None)
    prompt_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
    candidate_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0
    total_tokens = getattr(usage, "total_token_count", 0) if usage else 0

    return latency, prompt_tokens, candidate_tokens, total_tokens

if __name__ == "__main__":
    gemini_key = os.environ.get("GEMINI_API_KEY")

    doc_scanned = ensure_scanned_pdf()
    doc_table = create_table_format_pdf()
    doc_decoy = create_decoy_pdf()

    tests = [
        ("TASK 1: Scanned / Raster Image Contract (No Text Layer, 150 DPI)", doc_scanned),
        ("TASK 2A: Table / Grid Format Contract (Rate Matrix Layout)", doc_table),
        ("TASK 2B: Contract With Nearby Decoy Clauses (Distractor Rejection)", doc_decoy),
    ]

    print("=" * 75)
    print("AUDITAI - REAL GEMINI ADVERSARIAL EXTRACTION SUITE")
    print("=" * 75)
    print(f"GEMINI_API_KEY Available: {bool(gemini_key)}")
    print(f"Active Model: {GEMINI_DEFAULT_MODEL}")
    print("-" * 75)

    all_telemetry = []

    for title, pdf_path in tests:
        print(f"\n>>> {title}")
        print(f"File: {pdf_path}")
        
        # Measure latency & token usage
        latency, p_tokens, c_tokens, tot_tokens = get_token_usage_and_timing(pdf_path, gemini_key)
        all_telemetry.append((title, latency, p_tokens, c_tokens, tot_tokens))

        # Run extraction module
        res = extractor_llm.extract_contract(pdf_path)
        print(f"  Method invoked: {res.extraction_method}")
        eval_res = evaluate_fields(res, EXPECTED_TERMS)
        for field, r in eval_res.items():
            print(f"  • {field:35s}: [{r['status']:7s}] Expected: {r['expected']} | Extracted: {r['extracted']}")

        print(f"  Telemetry: Latency = {latency:.2f}s | Prompt Tokens = {p_tokens} | Output Tokens = {c_tokens} | Total = {tot_tokens}")

    print("\n" + "=" * 75)
    print("COST & RELIABILITY BENCHMARK REPORT (TASK 3)")
    print("=" * 75)
    
    # Gemini pricing (Flash-Lite / Flash tier standard pricing):
    # Prompt tokens: $0.075 per 1,000,000 tokens
    # Output tokens: $0.30 per 1,000,000 tokens
    total_cost_usd = 0.0
    for title, latency, p_tokens, c_tokens, tot_tokens in all_telemetry:
        cost = (p_tokens * 0.075 / 1_000_000) + (c_tokens * 0.30 / 1_000_000)
        total_cost_usd += cost
        print(f"• {title[:35]:35s} | Latency: {latency:.2f}s | Tokens: {tot_tokens:4d} | Est. Cost: ${cost:.6f}")

    avg_cost = total_cost_usd / len(all_telemetry)
    print(f"\nAverage Cost per Document: ${avg_cost:.6f} USD (~${avg_cost*1000:.3f} per 1,000 documents)")
    print("=" * 75)
