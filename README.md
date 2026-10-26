# AuditAI: Enterprise Contract & Invoice Assurance Engine

AuditAI is an automated forensic reconciliation engine that audits transportation freight invoices against Master Service Agreements (MSAs), contracts, lane rate sheets, and accessorial fee schedules to identify billing discrepancies, duplicate submissions, and unapplied volume rebates.

## Grounded Market Context
In commercial freight audit (source: Cass Information Systems, US Bank Freight Payment Index), carrier billing discrepancy rates average **3.0% to 5.0%** of gross transportation spend. Discrepancies stem from:
* Incorrect rate caps applied against contracted origin-destination lanes (`lane_rates`)
* Unapplied volume discount tiers and omitted contractual rebates
* Uncapped accessorial charges (lumper fees, detention beyond grace hours, layover, TONU)
* Duplicate freight bill submissions and fuel surcharge base inflation

*Note: The included synthetic demonstration fixture produces a 26.1% discrepancy because it was constructed to exercise all four error-detection branches simultaneously in a single bill.*

## Architecture & Extraction Modality
* `audit_engine.py`: Core deterministic reconciliation engine (decimal math, lane rates, accessorial caps, and executive dispute memorandum generation).
* `batch_auditor.py`: Multi-invoice batch reconciliation, duplicate invoice detection (100% disallowed), and cross-invoice volume aggregation.
* `audit_store.py`: Local SQLite persistence layer (`audit_history.db`) for permanent audit trail logging with CSV export.
* `extractor_llm.py`: Multimodal document extraction module using `google-genai` SDK with Pydantic structured output schemas.
  * **Production Mode**: Uses Google Gemini API (`GEMINI_API_KEY`). On non-standard text contracts (e.g. `contract_schedule_style.pdf` with UK schedule phrasing), Gemini successfully extracted 6/6 contract terms with 100% precision. On 150 DPI raster image scans (`contract_scanned_image.pdf`), Gemini extracted 2/6 terms correctly and hallucinated 4/6 values, demonstrating the necessity of the human confirmation gate.
  * **Offline Fallback**: Uses deterministic `pdfplumber` + regex pattern matching. *Limitation: The fallback parser is tuned to standardized digital text PDF layouts; on non-standard phrasing or image-only scanned documents, it finds 0/6 terms.*
* `app.py`: Streamlit enterprise dashboard with mandatory human review & sign-off gate before audit execution.

## Known Limitations
* **Accessorial Key Matching**: Accessorial cap matching is currently key-based (`lumper`, `lumper_fee`, `liftgate`, etc.). Non-matching vendor descriptions (e.g. `lumper_fee_charge`, `unloading_labor`) correctly fall to `unreviewed_lines` at billed amount for human review.
* **Document Extraction & Human Gate**: Fully autonomous extraction across unstandardized contracts requires an active Gemini API key. For scanned raster images, AI vision can hallucinate figures; the engine therefore enforces a mandatory Step 1 human review & sign-off gate before any mathematical reconciliation is executed.

## Before running a real client document

The operator must manually confirm each item below prior to executing an audit:

1. **Is the document a digital PDF with a real text layer, not a scan?** (If scanned, expect wrong numbers on financial fields — do not trust them without manual verification against the original.)
2. **Has a human compared every extracted field against the source document by hand before it feeds into the audit math?**
3. **Is GEMINI_API_KEY set and confirmed live (not silently running on the regex fallback)?**
4. **Has any extraction_warning been checked and resolved before proceeding?**

