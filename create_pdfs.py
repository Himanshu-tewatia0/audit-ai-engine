import os
import fitz  # PyMuPDF

def create_sample_pdfs():
    sample_dir = os.path.join(os.path.dirname(__file__), "sample_data")
    os.makedirs(sample_dir, exist_ok=True)

    # 1. Create sample_contract.pdf
    contract_pdf_path = os.path.join(sample_dir, "sample_contract.pdf")
    doc_c = fitz.open()
    page_c = doc_c.new_page()
    contract_text = """MASTER SERVICE AGREEMENT: FREIGHT & LOGISTICS
Contract ID: MSA-2026-US-LOG
Effective Date: 2026-01-01
Parties: Global Retail Group Inc. (Buyer) and SwiftLine National Freight Corp. (Vendor)

Section 4.1 (Base Freight): Standard linehaul rates shall strictly not exceed $2.40 per route mile for Class-A dry van trailers.
Section 5.3 (Fuel Adjustment): Fuel surcharge is calculated strictly against base linehaul and is capped at a maximum of 12.0% regardless of national index fluctuations.
Section 6.2 (Detention & Wait Time): The initial 2.0 hours at origin or destination are free of charge. Excess hours are billable at $50.00/hour.
Section 8.4 (Tiered Volume Rebate): A 5.0% rebate applies across all qualifying monthly billing if aggregate shipment volume exceeds 50 loads.
Payment Terms: 2% early payment discount if paid within 10 days.
"""
    page_c.insert_text((50, 50), contract_text, fontsize=11)
    doc_c.save(contract_pdf_path)
    doc_c.close()
    print(f"Created {contract_pdf_path}")

    # 2. Create sample_invoice.pdf
    invoice_pdf_path = os.path.join(sample_dir, "sample_invoice.pdf")
    doc_i = fitz.open()
    page_i = doc_i.new_page()
    invoice_text = """FREIGHT INVOICE
Invoice Number: INV-9042
Invoice Date: 2026-10-18
Vendor: SwiftLine National Freight Corp.
Buyer: Global Retail Group Inc.
Contract Reference: MSA-2026-US-LOG
Monthly Volume to Date: 62 shipments

LINE ITEMS:
1. Linehaul: Route Chicago to Dallas, 1450 miles @ $2.85/mile = $4132.50
2. Fuel Surcharge: National Fuel Index Adjustment 18.0% = $743.85
3. Detention: Unloading delay 4.0 hours @ $75.00/hour = $300.00
4. Volume Rebate: $0.00

Total Amount Billed: $5176.35
"""
    page_i.insert_text((50, 50), invoice_text, fontsize=11)
    doc_i.save(invoice_pdf_path)
    doc_i.close()
    print(f"Created {invoice_pdf_path}")

    # 3. Create carrier_bill_alt_layout.pdf (Alternative non-standard layout & terminology)
    alt_invoice_path = os.path.join(sample_dir, "carrier_bill_alt_layout.pdf")
    doc_alt = fitz.open()
    page_alt = doc_alt.new_page()
    alt_text = """FREIGHT CARRIER STATEMENT & FREIGHT BILL
Bill Number: FB-77401
Billing Date: 2026-10-20
Motor Carrier: SwiftLine National Freight Corp.
Shipper / Account: Global Retail Group Inc.
Master Agreement: MSA-2026-US-LOG
Total Dispatched Shipments Month-to-Date: 62

FREIGHT CHARGES BREAKDOWN:
Item 101 - Motor Carrier Charge (1450 miles @ $2.85/mile): $4,132.50
Item 204 - Diesel Index Fee (FSC Rate: 18.0%): $743.85
Item 310 - Facility Wait Time (Demurrage 4.0 hrs @ $75.00/hr): $300.00
Item 405 - Volume Tier Rebate: $0.00

Total Amount Due: $5,176.35
"""
    page_alt.insert_text((50, 50), alt_text, fontsize=11)
    doc_alt.save(alt_invoice_path)
    doc_alt.close()
    print(f"Created {alt_invoice_path}")

    # 4. Create sample batch JSON fixtures
    create_sample_batch_files()

def create_sample_batch_files():
    sample_dir = os.path.join(os.path.dirname(__file__), "sample_data")
    batch_dir = os.path.join(sample_dir, "batch")
    os.makedirs(batch_dir, exist_ok=True)

    import json

    b1 = {
        "invoice_number": "INV-8801",
        "vendor_name": "SwiftLine National Freight Corp.",
        "buyer_name": "Global Retail Group Inc.",
        "invoice_date": "2026-10-05",
        "monthly_volume_shipments_to_date": 28,
        "total_billed_amount": 4189.80,
        "line_items": {
            "linehaul": {
                "billed_miles": 1200,
                "billed_rate_per_mile": 2.80,
                "billed_amount": 3360.00
            },
            "fuel_surcharge": {
                "billed_percent": 18.0,
                "billed_amount": 604.80
            },
            "detention": {
                "billed_hours": 3.0,
                "billed_hourly_rate": 75.0,
                "billed_amount": 225.00
            },
            "volume_discount": {
                "billed_amount": 0.0
            }
        }
    }

    b2 = {
        "invoice_number": "INV-8802",
        "vendor_name": "SwiftLine National Freight Corp.",
        "buyer_name": "Global Retail Group Inc.",
        "invoice_date": "2026-10-12",
        "monthly_volume_shipments_to_date": 27,
        "total_billed_amount": 5176.35,
        "line_items": {
            "linehaul": {
                "billed_miles": 1450,
                "billed_rate_per_mile": 2.85,
                "billed_amount": 4132.50
            },
            "fuel_surcharge": {
                "billed_percent": 18.0,
                "billed_amount": 743.85
            },
            "detention": {
                "billed_hours": 4.0,
                "billed_hourly_rate": 75.0,
                "billed_amount": 300.00
            },
            "volume_discount": {
                "billed_amount": 0.0
            }
        }
    }

    b_dup = dict(b1)

    p1 = os.path.join(batch_dir, "invoice_batch_1.json")
    p2 = os.path.join(batch_dir, "invoice_batch_2.json")
    p_dup = os.path.join(batch_dir, "invoice_batch_duplicate.json")

    with open(p1, "w", encoding="utf-8") as f:
        json.dump(b1, f, indent=2)
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(b2, f, indent=2)
    with open(p_dup, "w", encoding="utf-8") as f:
        json.dump(b_dup, f, indent=2)

    print(f"Created batch fixtures in {batch_dir}")

if __name__ == "__main__":
    create_sample_pdfs()
