"""
AuditAI - Multimodal LLM Extraction Engine (v1.0)
Extracts structured contract terms and invoice line items from raw PDFs
using Gemini 2.5 Flash via the official `google-genai` SDK with Pydantic response schemas.

Features:
- Structured Pydantic schema validation.
- Verbatim quoted text, page number, and confidence extraction.
- Real-world logistics terminology normalization (e.g. Linehaul / Motor Carrier Charge,
  FSC / Diesel Index Fee, Detention / Facility Wait Time / Demurrage).
- Automatic offline fallback to deterministic pdfplumber + regex engine if
  GEMINI_API_KEY is not set, empty, or if an API exception occurs.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from audit_engine import (
    ExtractionResult,
    ExtractedField,
    extract_contract_from_pdf as extract_contract_fallback,
    extract_invoice_from_pdf as extract_invoice_fallback,
)

logger = logging.getLogger("AuditAI.ExtractorLLM")


# ---------------------------------------------------------------------------
# Pydantic Structured Output Schemas
# ---------------------------------------------------------------------------

class NumericExtractionField(BaseModel):
    value: float = Field(description="The numeric value extracted from the document.")
    quoted_clause: str = Field(description="Exact verbatim quote or sentence from the document supporting this value.")
    page_number: int = Field(default=1, description="1-based page number where the clause was found.")
    confidence: float = Field(default=0.95, description="Extraction confidence score between 0.0 and 1.0.")


class StringExtractionField(BaseModel):
    value: str = Field(description="The string text extracted from the document.")
    quoted_clause: str = Field(description="Exact verbatim quote or sentence from the document supporting this value.")
    page_number: int = Field(default=1, description="1-based page number where the clause was found.")
    confidence: float = Field(default=0.95, description="Extraction confidence score between 0.0 and 1.0.")


class ContractExtractionSchema(BaseModel):
    contract_id: StringExtractionField = Field(description="Contract identifier (e.g. MSA-2026-US-LOG).")
    vendor_name: StringExtractionField = Field(description="Full legal name of the vendor/carrier.")
    buyer_name: StringExtractionField = Field(description="Full legal name of the buyer/client.")
    base_rate_per_mile: NumericExtractionField = Field(description="Maximum standard linehaul rate per route mile cap in dollars.")
    max_fuel_surcharge_percent: NumericExtractionField = Field(description="Maximum contractually allowed fuel surcharge percentage cap.")
    free_detention_hours: NumericExtractionField = Field(description="Free detention hours allowed at loading/unloading.")
    detention_hourly_rate: NumericExtractionField = Field(description="Contractually billable rate per detention hour after free time.")
    volume_discount_threshold_shipments: NumericExtractionField = Field(description="Shipment volume threshold required to trigger rebate.")
    volume_discount_percent: NumericExtractionField = Field(description="Rebate percentage applied when volume threshold is exceeded.")


class InvoiceLinehaulItem(BaseModel):
    billed_miles: float = Field(description="Billed distance in miles.")
    billed_rate_per_mile: float = Field(description="Billed linehaul rate per mile in dollars.")
    billed_amount: float = Field(description="Total billed linehaul amount in dollars.")
    quoted_clause: str = Field(description="Exact verbatim line item text from invoice.")
    page_number: int = Field(default=1)
    confidence: float = Field(default=0.95)


class InvoiceFuelItem(BaseModel):
    billed_percent: float = Field(description="Billed fuel surcharge percentage.")
    billed_amount: float = Field(description="Total billed fuel surcharge amount in dollars.")
    quoted_clause: str = Field(description="Exact verbatim line item text from invoice.")
    page_number: int = Field(default=1)
    confidence: float = Field(default=0.95)


class InvoiceDetentionItem(BaseModel):
    billed_hours: float = Field(description="Billed detention or wait time hours.")
    billed_hourly_rate: float = Field(description="Billed detention hourly rate in dollars.")
    billed_amount: float = Field(description="Total billed detention amount in dollars.")
    quoted_clause: str = Field(description="Exact verbatim line item text from invoice.")
    page_number: int = Field(default=1)
    confidence: float = Field(default=0.95)


class InvoiceRebateItem(BaseModel):
    billed_amount: float = Field(description="Billed volume rebate line amount in dollars (0.0 if not given).")
    quoted_clause: str = Field(description="Exact verbatim line item text from invoice.")
    page_number: int = Field(default=1)
    confidence: float = Field(default=0.95)


class InvoiceExtractionSchema(BaseModel):
    invoice_number: StringExtractionField = Field(description="Invoice or freight bill number (e.g. INV-9042, FB-77401).")
    vendor_name: StringExtractionField = Field(description="Vendor or motor carrier name.")
    buyer_name: StringExtractionField = Field(description="Buyer or shipper/account name.")
    total_billed_amount: NumericExtractionField = Field(description="Total amount billed or due on the invoice.")
    monthly_volume_shipments_to_date: Optional[NumericExtractionField] = Field(
        default=None, description="Dispatched shipments or monthly volume count mentioned on invoice."
    )
    linehaul: Optional[InvoiceLinehaulItem] = Field(default=None, description="Linehaul or base motor carrier charge item.")
    fuel_surcharge: Optional[InvoiceFuelItem] = Field(default=None, description="Fuel surcharge or diesel index fee item.")
    detention: Optional[InvoiceDetentionItem] = Field(default=None, description="Detention, demurrage, or facility wait time item.")
    volume_discount: Optional[InvoiceRebateItem] = Field(default=None, description="Volume rebate line item.")


# ---------------------------------------------------------------------------
# Multimodal Extraction Prompts
# ---------------------------------------------------------------------------

CONTRACT_EXTRACTION_PROMPT = """
You are a senior forensic contract auditor. Analyze the attached contract document and extract every key pricing term, parameter, and exact clause quote.

Normalization rules:
- Base Freight / Linehaul rate cap: Extract maximum allowable rate per mile.
- Fuel Surcharge cap: Extract the maximum contractual percentage cap.
- Detention & Wait Time: Extract free hours allowed and the hourly rate billable thereafter.
- Volume Rebate / Tiered Discount: Extract the qualifying volume threshold and the discount percentage.

For every field, provide:
1. The exact numeric or string value.
2. The exact verbatim sentence or clause quoted from the contract.
3. The 1-based page number where the clause appears.
4. Your confidence score between 0.0 and 1.0.
"""

INVOICE_EXTRACTION_PROMPT = """
You are a senior forensic freight and procurement auditor. Analyze the attached invoice or freight bill document and extract all billed charges, line items, and metadata.

Terminology mappings:
- Invoice ID: Look for "Invoice Number", "Bill Number", "Freight Bill", "Invoice #", "Statement #".
- Carrier / Vendor: Look for "Vendor", "Motor Carrier", "Carrier", "Issuing Party".
- Buyer / Shipper: Look for "Buyer", "Shipper", "Account", "Billed To", "Customer".
- Base Freight: Look for "Linehaul", "Motor Carrier Charge", "Base Freight", "Freight Charge". Extract miles, rate/mile, and total billed.
- Fuel: Look for "Fuel Surcharge", "Diesel Index Fee", "FSC", "Fuel Adjustment". Extract percent and total billed.
- Detention: Look for "Detention", "Demurrage", "Facility Wait Time", "Unloading Delay". Extract hours, hourly rate, and total billed.
- Rebate: Look for "Volume Rebate", "Tier Rebate", "Volume Discount".
- Stated Total: Look for "Total Amount Billed", "Total Amount Due", "Invoice Total", "Net Due".
- Volume: Look for "Monthly Volume", "Dispatched Shipments Month-to-Date", "Shipments to Date".

For every field, provide:
1. The exact value.
2. The verbatim quote from the invoice text.
3. The page number.
4. Your confidence score between 0.0 and 1.0.
"""


# ---------------------------------------------------------------------------
# Converters: Schema -> ExtractionResult
# ---------------------------------------------------------------------------

def _contract_schema_to_extraction_result(data: ContractExtractionSchema, source_file: str) -> ExtractionResult:
    clauses = {
        "section_4_1": data.base_rate_per_mile.quoted_clause,
        "section_5_3": data.max_fuel_surcharge_percent.quoted_clause,
        "section_6_2": f"{data.free_detention_hours.quoted_clause} | {data.detention_hourly_rate.quoted_clause}",
        "section_8_4": f"{data.volume_discount_threshold_shipments.quoted_clause} | {data.volume_discount_percent.quoted_clause}",
    }
    fields = [
        ExtractedField("contract_id", data.contract_id.value, data.contract_id.quoted_clause, data.contract_id.page_number, data.contract_id.confidence),
        ExtractedField("vendor_name", data.vendor_name.value, data.vendor_name.quoted_clause, data.vendor_name.page_number, data.vendor_name.confidence),
        ExtractedField("buyer_name", data.buyer_name.value, data.buyer_name.quoted_clause, data.buyer_name.page_number, data.buyer_name.confidence),
        ExtractedField("base_rate_per_mile", float(data.base_rate_per_mile.value), data.base_rate_per_mile.quoted_clause, data.base_rate_per_mile.page_number, data.base_rate_per_mile.confidence),
        ExtractedField("max_fuel_surcharge_percent", float(data.max_fuel_surcharge_percent.value), data.max_fuel_surcharge_percent.quoted_clause, data.max_fuel_surcharge_percent.page_number, data.max_fuel_surcharge_percent.confidence),
        ExtractedField("free_detention_hours", float(data.free_detention_hours.value), data.free_detention_hours.quoted_clause, data.free_detention_hours.page_number, data.free_detention_hours.confidence),
        ExtractedField("detention_hourly_rate", float(data.detention_hourly_rate.value), data.detention_hourly_rate.quoted_clause, data.detention_hourly_rate.page_number, data.detention_hourly_rate.confidence),
        ExtractedField("volume_discount_threshold_shipments", int(data.volume_discount_threshold_shipments.value), data.volume_discount_threshold_shipments.quoted_clause, data.volume_discount_threshold_shipments.page_number, data.volume_discount_threshold_shipments.confidence),
        ExtractedField("volume_discount_percent", float(data.volume_discount_percent.value), data.volume_discount_percent.quoted_clause, data.volume_discount_percent.page_number, data.volume_discount_percent.confidence),
        ExtractedField("clauses", clauses, "Consolidated contract clauses", 1, 1.0),
    ]
    return ExtractionResult(
        source_file=source_file,
        document_type="contract",
        fields=fields,
        human_confirmed=False,
        extraction_method="gemini-2.5-flash-vision",
    )


def _invoice_schema_to_extraction_result(data: InvoiceExtractionSchema, source_file: str) -> ExtractionResult:
    line_items: Dict[str, Any] = {}
    fields: List[ExtractedField] = []

    fields.append(ExtractedField("invoice_number", data.invoice_number.value, data.invoice_number.quoted_clause, data.invoice_number.page_number, data.invoice_number.confidence))
    fields.append(ExtractedField("vendor_name", data.vendor_name.value, data.vendor_name.quoted_clause, data.vendor_name.page_number, data.vendor_name.confidence))
    fields.append(ExtractedField("buyer_name", data.buyer_name.value, data.buyer_name.quoted_clause, data.buyer_name.page_number, data.buyer_name.confidence))
    fields.append(ExtractedField("total_billed_amount", float(data.total_billed_amount.value), data.total_billed_amount.quoted_clause, data.total_billed_amount.page_number, data.total_billed_amount.confidence))

    if data.monthly_volume_shipments_to_date:
        fields.append(ExtractedField(
            "monthly_volume_shipments_to_date",
            int(data.monthly_volume_shipments_to_date.value),
            data.monthly_volume_shipments_to_date.quoted_clause,
            data.monthly_volume_shipments_to_date.page_number,
            data.monthly_volume_shipments_to_date.confidence,
        ))

    if data.linehaul:
        lh_dict = {
            "billed_miles": int(data.linehaul.billed_miles),
            "billed_rate_per_mile": float(data.linehaul.billed_rate_per_mile),
            "billed_amount": float(data.linehaul.billed_amount),
        }
        line_items["linehaul"] = lh_dict
        fields.append(ExtractedField("linehaul_line", lh_dict, data.linehaul.quoted_clause, data.linehaul.page_number, data.linehaul.confidence))

    if data.fuel_surcharge:
        fuel_dict = {
            "billed_percent": float(data.fuel_surcharge.billed_percent),
            "billed_amount": float(data.fuel_surcharge.billed_amount),
        }
        line_items["fuel_surcharge"] = fuel_dict
        fields.append(ExtractedField("fuel_surcharge_line", fuel_dict, data.fuel_surcharge.quoted_clause, data.fuel_surcharge.page_number, data.fuel_surcharge.confidence))

    if data.detention:
        det_dict = {
            "billed_hours": float(data.detention.billed_hours),
            "billed_hourly_rate": float(data.detention.billed_hourly_rate),
            "billed_amount": float(data.detention.billed_amount),
        }
        line_items["detention"] = det_dict
        fields.append(ExtractedField("detention_line", det_dict, data.detention.quoted_clause, data.detention.page_number, data.detention.confidence))

    if data.volume_discount:
        reb_dict = {
            "billed_amount": float(data.volume_discount.billed_amount),
        }
        line_items["volume_discount"] = reb_dict
        fields.append(ExtractedField("volume_discount_line", reb_dict, data.volume_discount.quoted_clause, data.volume_discount.page_number, data.volume_discount.confidence))

    fields.append(ExtractedField("line_items", line_items, "Extracted line items dictionary", 1, 1.0))

    return ExtractionResult(
        source_file=source_file,
        document_type="invoice",
        fields=fields,
        human_confirmed=False,
        extraction_method="gemini-2.5-flash-vision",
    )


# ---------------------------------------------------------------------------
# Public Extraction Functions (with Automatic Fallback)
# ---------------------------------------------------------------------------

GEMINI_DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")

def extract_contract(file_path: str, api_key: Optional[str] = None) -> ExtractionResult:
    """
    Extracts contract terms from PDF using Gemini Vision if GEMINI_API_KEY is available.
    Automatically falls back to deterministic pdfplumber + regex if API key is absent or call fails.
    """
    resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not resolved_key or not resolved_key.strip():
        logger.info("GEMINI_API_KEY not found. Using deterministic fallback parser for contract.")
        res = extract_contract_fallback(file_path)
        res.extraction_method = "pdfplumber-deterministic"
        return res

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=resolved_key.strip())

        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")

        response = client.models.generate_content(
            model=GEMINI_DEFAULT_MODEL,
            contents=[part, CONTRACT_EXTRACTION_PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ContractExtractionSchema,
                temperature=0.0,
            ),
        )

        schema_obj = ContractExtractionSchema.model_validate_json(response.text)
        return _contract_schema_to_extraction_result(schema_obj, file_path)

    except Exception as exc:
        logger.warning(f"Gemini LLM extraction failed ({exc}). Falling back to deterministic parser.")
        res = extract_contract_fallback(file_path)
        res.extraction_method = "pdfplumber-fallback"
        res.extraction_warning = f"AI extraction failed ({exc}). Reverted to offline parser."
        return res


def extract_invoice(file_path: str, api_key: Optional[str] = None) -> ExtractionResult:
    """
    Extracts invoice line items from PDF using Gemini Vision if GEMINI_API_KEY is available.
    Automatically falls back to deterministic pdfplumber + regex if API key is absent or call fails.
    """
    resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not resolved_key or not resolved_key.strip():
        logger.info("GEMINI_API_KEY not found. Using deterministic fallback parser for invoice.")
        res = extract_invoice_fallback(file_path)
        res.extraction_method = "pdfplumber-deterministic"
        return res

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=resolved_key.strip())

        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")

        response = client.models.generate_content(
            model=GEMINI_DEFAULT_MODEL,
            contents=[part, INVOICE_EXTRACTION_PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=InvoiceExtractionSchema,
                temperature=0.0,
            ),
        )

        schema_obj = InvoiceExtractionSchema.model_validate_json(response.text)
        return _invoice_schema_to_extraction_result(schema_obj, file_path)

    except Exception as exc:
        logger.warning(f"Gemini LLM extraction failed ({exc}). Falling back to deterministic parser.")
        res = extract_invoice_fallback(file_path)
        res.extraction_method = "pdfplumber-fallback"
        res.extraction_warning = f"AI extraction failed ({exc}). Reverted to offline parser."
        return res
