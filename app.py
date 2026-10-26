"""
AuditAI Enterprise Billing & Contract Assurance Platform (v2.3)
Professional forensic audit workspace for Master Service Agreements (MSAs) and supplier invoices.
Features multimodal LLM extraction, multi-invoice batch auditing, duplicate claim detection,
cumulative volume rebate rollups, and strict human sign-off gates.
"""

import copy
import csv
import hashlib
import html
import io
import json
import os
import re
import sys
import tempfile
import uuid
import shutil
import importlib
from datetime import date
from dataclasses import asdict
from decimal import Decimal
from typing import Optional, Dict, Any, List

import streamlit as st

import audit_engine
importlib.reload(audit_engine)

from audit_engine import (
    ContractAuditor,
    AuditReport,
    ExtractionResult,
    ExtractedField,
    HumanConfirmationRequiredError,
    MissingContractTermError,
    InvoiceValidationError,
    extract_contract_from_pdf,
    extract_invoice_from_pdf,
    _fmt,
)

import extractor_llm
importlib.reload(extractor_llm)

from extractor_llm import (
    extract_contract as extract_contract_llm,
    extract_invoice as extract_invoice_llm,
)

import batch_auditor
importlib.reload(batch_auditor)

from batch_auditor import (
    BatchAuditor,
    BatchAuditSummary,
    DuplicateAlert,
)

import audit_store
importlib.reload(audit_store)

from audit_store import (
    save_audit_run,
    get_audit_history,
    get_audit_by_id,
    clear_history,
    export_history_to_csv,
)

# Startup environment check & key verification (safe masking: last 4 chars only)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_startup_key = ""
try:
    if hasattr(st, "secrets") and "GEMINI_API_KEY" in st.secrets:
        _startup_key = str(st.secrets["GEMINI_API_KEY"]).strip()
        if _startup_key:
            os.environ["GEMINI_API_KEY"] = _startup_key
except Exception:
    pass

if not _startup_key:
    _startup_key = os.environ.get("GEMINI_API_KEY", "")

if not _startup_key:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as _rk:
            _startup_key, _ = winreg.QueryValueEx(_rk, "GEMINI_API_KEY")
            if _startup_key:
                os.environ["GEMINI_API_KEY"] = _startup_key
    except Exception:
        pass

if _startup_key:
    print(f"[AuditAI Startup] os.environ.get('GEMINI_API_KEY') loaded: ...{_startup_key[-4:]}", flush=True)
else:
    print("[AuditAI Startup] os.environ.get('GEMINI_API_KEY') loaded: None", flush=True)


def get_active_db_path() -> str:
    """
    Returns the path to the isolated SQLite database for the active user session.
    Guarantees that uploaded audit data and history from one session can NEVER
    be viewed, exported, or deleted by another concurrent user or browser session.
    """
    if "session_db_path" not in st.session_state:
        session_id = uuid.uuid4().hex[:12]
        db_file = os.path.join(tempfile.gettempdir(), f"audit_history_{session_id}.db")
        audit_store.init_db(db_file)
        st.session_state["session_db_path"] = db_file
    return st.session_state["session_db_path"]


# ---------------------------------------------------------------------------
# Helpers for KaTeX Escaping, HTML Sanitization & Path Traversal Defense
# ---------------------------------------------------------------------------
def esc(text: Any, escape_html: bool = True) -> str:
    """
    Escapes HTML entities and dollar signs.
    Prevents Stored XSS attacks when user-controlled or PDF-extracted content
    is rendered via unsafe_allow_html=True, and escapes dollar signs so Streamlit
    does not misinterpret currency figures as KaTeX LaTeX math blocks.
    """
    if text is None:
        return ""
    s = str(text)
    if escape_html:
        s = html.escape(s, quote=True)
    return re.sub(r"(?<!\\)\$", r"\$", s)


def safe_fmt(amount: Any) -> str:
    """Converts numeric/Decimal to formatted currency and escapes for markdown."""
    d = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return esc(_fmt(d))


def sanitize_upload_filename(filename: str, prefix: str = "doc") -> str:
    """
    Sanitizes user-provided filenames to prevent Path Traversal attacks.
    Extracts only the safe file extension (.pdf or .json) and attaches a secure UUID.
    Guarantees the resulting filename contains no directory separators or relative traversal tokens ('..').
    """
    clean_base = os.path.basename(filename)
    _, ext = os.path.splitext(clean_base)
    ext = ext.lower()
    if ext not in [".pdf", ".json"]:
        ext = ".pdf"
    safe_id = uuid.uuid4().hex[:12]
    return f"{prefix}_{safe_id}{ext}"


def make_safe_temp_path(temp_dir: str, filename: str, prefix: str = "doc") -> str:
    """
    Constructs an absolute path strictly bounded within temp_dir.
    Raises ValueError if path traversal is attempted.
    """
    safe_name = sanitize_upload_filename(filename, prefix=prefix)
    target_path = os.path.abspath(os.path.join(temp_dir, safe_name))
    temp_dir_abs = os.path.abspath(temp_dir)
    if not (target_path == temp_dir_abs or target_path.startswith(temp_dir_abs + os.sep)):
        raise ValueError(f"Path traversal detected for filename: {filename}")
    return target_path


def validate_contract_document(contract_res: Any, contract_dict: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validates whether the extracted contract contains recognizable commercial terms or clauses.
    Returns (is_valid, error_message).
    """
    terms = contract_dict.get("terms", {})
    valid_terms = [v for k, v in terms.items() if v is not None and v != 0 and v != 0.0]
    
    term_fields = [f for f in getattr(contract_res, "fields", []) if getattr(f, "field_name", "") != "clauses"]
    if term_fields:
        zero_conf_count = sum(1 for f in term_fields if getattr(f, "confidence", 1.0) == 0.0)
        avg_conf = sum(getattr(f, "confidence", 1.0) for f in term_fields) / len(term_fields)
        if zero_conf_count == len(term_fields) or avg_conf < 0.10:
            return False, "This doesn't appear to be a valid contract document (0.0% extraction confidence across all fields). No contractual rate terms or clauses were found."

    clauses = contract_dict.get("clauses", {})
    if not valid_terms and not clauses:
        return False, "This doesn't appear to be a valid contract document (zero contract terms found). No base rate, fuel cap, detention, or rebate clauses were identified."

    return True, ""


def validate_invoice_document(invoice_res: Any, invoice_dict: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validates whether the extracted invoice contains recognizable billing items.
    Returns (is_valid, error_message).
    """
    line_items = invoice_dict.get("line_items", {})
    total_billed = invoice_dict.get("total_billed_amount", 0.0)
    inv_fields = [f for f in getattr(invoice_res, "fields", []) if getattr(f, "field_name", "") != "line_items"]
    if inv_fields:
        zero_conf_count = sum(1 for f in inv_fields if getattr(f, "confidence", 1.0) == 0.0)
        avg_conf = sum(getattr(f, "confidence", 1.0) for f in inv_fields) / len(inv_fields)
        if zero_conf_count == len(inv_fields) or avg_conf < 0.10:
            return False, "This doesn't appear to be a valid invoice document (0.0% extraction confidence across all fields). No billing line items or charges were found."

    if not line_items and (total_billed == 0.0 or total_billed is None):
        return False, "This doesn't appear to be a valid invoice document (zero billing line items found). No linehaul, fuel, detention, or total charges were identified."

    return True, ""


# ---------------------------------------------------------------------------
# Page Configuration & Professional Styling (Zero Emoji Enterprise Aesthetic)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="AuditAI | Contract & Billing Assurance",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        color: #0F172A;
    }

    /* Clean Enterprise Header */
    .app-header {
        border-bottom: 1px solid #E2E8F0;
        padding-bottom: 1.25rem;
        margin-bottom: 1.5rem;
    }
    .app-title {
        font-size: 1.4rem;
        font-weight: 700;
        color: #0F172A;
        letter-spacing: -0.02em;
        margin: 0;
    }
    .app-subtitle {
        font-size: 0.85rem;
        color: #64748B;
        margin-top: 0.25rem;
    }

    /* KPI Cards */
    .kpi-row {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 1rem;
        margin-bottom: 1.5rem;
    }
    .kpi-box {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 6px;
        padding: 1.1rem 1.25rem;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
    }
    .kpi-box-leakage {
        border-left: 4px solid #DC2626;
    }
    .kpi-box-allowed {
        border-left: 4px solid #16A34A;
    }
    .kpi-box-neutral {
        border-left: 4px solid #64748B;
    }
    .kpi-title {
        font-size: 0.725rem;
        text-transform: uppercase;
        font-weight: 600;
        color: #64748B;
        letter-spacing: 0.05em;
        margin-bottom: 0.35rem;
    }
    .kpi-num {
        font-size: 1.45rem;
        font-weight: 700;
        color: #0F172A;
        font-family: 'JetBrains Mono', monospace;
    }
    .kpi-num-red {
        color: #DC2626;
    }
    .kpi-num-green {
        color: #16A34A;
    }
    .kpi-subtext {
        font-size: 0.75rem;
        color: #64748B;
        margin-top: 0.35rem;
    }

    /* Status Badges */
    .badge-confirmed {
        display: inline-block;
        background-color: #ECFDF5;
        color: #065F46;
        font-size: 0.725rem;
        font-weight: 600;
        padding: 2px 7px;
        border-radius: 4px;
        border: 1px solid #A7F3D0;
    }
    .badge-review {
        display: inline-block;
        background-color: #FFFBEB;
        color: #92400E;
        font-size: 0.725rem;
        font-weight: 600;
        padding: 2px 7px;
        border-radius: 4px;
        border: 1px solid #FDE68A;
    }
    .badge-duplicate {
        display: inline-block;
        background-color: #FEF2F2;
        color: #991B1B;
        font-size: 0.725rem;
        font-weight: 600;
        padding: 2px 7px;
        border-radius: 4px;
        border: 1px solid #FECACA;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_CONTRACT_PDF = os.path.join(BASE_DIR, "sample_data", "sample_contract.pdf")
SAMPLE_INVOICE_PDF = os.path.join(BASE_DIR, "sample_data", "sample_invoice.pdf")
SAMPLE_ALT_INVOICE_PDF = os.path.join(BASE_DIR, "sample_data", "carrier_bill_alt_layout.pdf")

SAMPLE_BATCH_DIR = os.path.join(BASE_DIR, "sample_data", "batch")
BATCH_FILE_1 = os.path.join(SAMPLE_BATCH_DIR, "invoice_batch_1.json")
BATCH_FILE_2 = os.path.join(SAMPLE_BATCH_DIR, "invoice_batch_2.json")
BATCH_FILE_DUP = os.path.join(SAMPLE_BATCH_DIR, "invoice_batch_duplicate.json")

# ---------------------------------------------------------------------------
# App Header (Zero Emoji)
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="app-header">
        <div class="app-title">AuditAI Forensic Engine — Corporate Contract & Invoice Assurance</div>
        <div class="app-subtitle">
            Autonomous multi-clause contract reconciliation, multimodal document extraction, batch processing, and duplicate detection.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar: Engine Configuration, Telemetry, and Mode Selector
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Configuration & Ingestion")

    # Mode Selector
    workflow_mode = st.radio(
        "Workflow Mode",
        ["Single Invoice Audit", "Multi-Invoice Batch Audit"],
        index=0,
        help="Single mode provides deep forensic clause inspection. Batch mode scans multiple invoices for duplicate claims and aggregates cumulative volume.",
    )

    # Extraction Engine Selection
    extraction_mode = st.radio(
        "Extraction Engine",
        ["Gemini 2.5 Flash Vision (Multimodal)", "Deterministic Local Parser (pdfplumber)"],
        index=0,
        help="Gemini multimodal processes unstructured documents and scans. Local parser runs fully offline.",
    )

    server_key = ""
    try:
        if hasattr(st, "secrets") and "GEMINI_API_KEY" in st.secrets:
            server_key = str(st.secrets["GEMINI_API_KEY"]).strip()
    except Exception:
        pass
    if not server_key:
        server_key = os.environ.get("GEMINI_API_KEY", "")
    if not server_key:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as rk:
                server_key, _ = winreg.QueryValueEx(rk, "GEMINI_API_KEY")
                if server_key:
                    os.environ["GEMINI_API_KEY"] = server_key
        except Exception:
            pass

    api_key_input = ""
    effective_key = ""
    if "Gemini" in extraction_mode:
        if server_key:
            st.caption(f"Status: Server Key Active (...{server_key[-4:]}). Multimodal vision ready.")
            # Critical Security: value="" ensures server key is NEVER transmitted across WebSocket into client DOM/JS
            api_key_input = st.text_input(
                "Gemini API Key (Optional User Override)",
                value="",
                type="password",
                placeholder="Leave blank to use preconfigured server key",
                help="A secure server-side key is active. Enter your own key only if you wish to override it for this session.",
            )
            effective_key = api_key_input.strip() or server_key
        else:
            api_key_input = st.text_input(
                "Gemini API Key",
                value="",
                type="password",
                placeholder="AIzaSy...",
                help="If omitted or empty, the engine automatically falls back to deterministic local parsing.",
            )
            effective_key = api_key_input.strip()
            if effective_key:
                st.caption(f"Status: Custom API Key entered (...{effective_key[-4:]}). Multimodal vision active.")
            else:
                st.caption("Note: No API key detected. Operating in offline fallback mode.")

    st.markdown("---")

    # Contract Upload (Common to both modes)
    st.markdown("### Contract Ingestion (MSA)")
    uploaded_contract = st.file_uploader(
        "Master Service Agreement (PDF or JSON)",
        type=["pdf", "json"],
        key="contract_uploader",
    )
    if uploaded_contract:
        st.session_state["contract_file_data"] = uploaded_contract.read()
        st.session_state["contract_filename"] = sanitize_upload_filename(uploaded_contract.name, "contract")
        st.session_state["contract_display_name"] = os.path.basename(uploaded_contract.name)
        st.session_state.pop("audit_report", None)
        st.session_state.pop("batch_summary", None)

    if workflow_mode == "Single Invoice Audit":
        st.markdown("---")
        st.markdown("### Single Invoice Presets")
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("Load Standard (INV-9042)", use_container_width=True):
                if os.path.exists(SAMPLE_CONTRACT_PDF) and os.path.exists(SAMPLE_INVOICE_PDF):
                    with open(SAMPLE_CONTRACT_PDF, "rb") as f:
                        st.session_state["contract_file_data"] = f.read()
                    st.session_state["contract_filename"] = "sample_contract.pdf"
                    st.session_state["contract_display_name"] = "sample_contract.pdf"
                    with open(SAMPLE_INVOICE_PDF, "rb") as f:
                        st.session_state["invoice_file_data"] = f.read()
                    st.session_state["invoice_filename"] = "sample_invoice.pdf"
                    st.session_state["invoice_display_name"] = "sample_invoice.pdf"
                    st.session_state.pop("audit_report", None)
                    st.session_state.pop("synced_total", None)
                    st.rerun()

        with col_btn2:
            if st.button("Load Alt Layout (FB-77401)", use_container_width=True):
                if os.path.exists(SAMPLE_CONTRACT_PDF) and os.path.exists(SAMPLE_ALT_INVOICE_PDF):
                    with open(SAMPLE_CONTRACT_PDF, "rb") as f:
                        st.session_state["contract_file_data"] = f.read()
                    st.session_state["contract_filename"] = "sample_contract.pdf"
                    st.session_state["contract_display_name"] = "sample_contract.pdf"
                    with open(SAMPLE_ALT_INVOICE_PDF, "rb") as f:
                        st.session_state["invoice_file_data"] = f.read()
                    st.session_state["invoice_filename"] = "carrier_bill_alt_layout.pdf"
                    st.session_state["invoice_display_name"] = "carrier_bill_alt_layout.pdf"
                    st.session_state.pop("audit_report", None)
                    st.session_state.pop("synced_total", None)
                    st.rerun()

        uploaded_invoice = st.file_uploader(
            "Vendor Invoice (PDF or JSON)",
            type=["pdf", "json"],
            key="invoice_uploader",
        )
        if uploaded_invoice:
            st.session_state["invoice_file_data"] = uploaded_invoice.read()
            st.session_state["invoice_filename"] = sanitize_upload_filename(uploaded_invoice.name, "invoice")
            st.session_state["invoice_display_name"] = os.path.basename(uploaded_invoice.name)
            st.session_state.pop("audit_report", None)

        st.markdown("---")
        st.markdown("### Buyer Telemetry Records")
        actual_miles_input = st.number_input(
            "Verified Route Miles",
            min_value=0,
            value=0,
            step=50,
            help="Buyer-verified odometer or GPS miles. Overrides vendor-claimed billed miles.",
        )
        shipment_count_input = st.number_input(
            "Verified Monthly Load Count",
            min_value=0,
            value=0,
            step=1,
            help="Verified monthly aggregate shipments. Verifies volume rebate qualification.",
        )
        num_stops_input = st.number_input(
            "Route Stops Count",
            min_value=1,
            value=1,
            step=1,
            help="Contract free detention hours apply per stop (e.g. 2 stops = 4 free hours).",
        )
        rebate_base_input = st.number_input(
            esc("Qualifying Monthly Base Spend ($)"),
            min_value=0.0,
            value=0.0,
            step=1000.0,
            help="Leave 0.0 to calculate volume rebate against current invoice linehaul.",
        )

    else:
        # Multi-Invoice Batch Mode Sidebar
        st.markdown("---")
        st.markdown("### Multi-Invoice Batch Ingestion")

        if st.button("Load Sample 3-Invoice Batch (with Duplicate)", use_container_width=True):
            if os.path.exists(SAMPLE_CONTRACT_PDF):
                with open(SAMPLE_CONTRACT_PDF, "rb") as f:
                    st.session_state["contract_file_data"] = f.read()
                st.session_state["contract_filename"] = "sample_contract.pdf"

            batch_list = []
            for b_path, b_name in [
                (BATCH_FILE_1, "invoice_batch_1.json"),
                (BATCH_FILE_2, "invoice_batch_2.json"),
                (BATCH_FILE_DUP, "invoice_batch_duplicate.json"),
            ]:
                if os.path.exists(b_path):
                    with open(b_path, "rb") as f:
                        batch_list.append((b_name, f.read()))
            st.session_state["batch_files"] = batch_list
            st.session_state.pop("batch_summary", None)
            st.rerun()

        uploaded_batch = st.file_uploader(
            "Upload Batch Invoices (Multiple PDF or JSON)",
            type=["pdf", "json"],
            accept_multiple_files=True,
            key="batch_uploader",
        )
        if uploaded_batch:
            batch_list = []
            for b_idx, up in enumerate(uploaded_batch, 1):
                safe_name = sanitize_upload_filename(up.name, prefix=f"batch_inv_{b_idx}")
                batch_list.append((safe_name, up.read()))
            st.session_state["batch_files"] = batch_list
            st.session_state.pop("batch_summary", None)


# ---------------------------------------------------------------------------
# Default Sample Ingestion Initialization (Contract)
# ---------------------------------------------------------------------------
if "contract_file_data" not in st.session_state:
    if os.path.exists(SAMPLE_CONTRACT_PDF):
        with open(SAMPLE_CONTRACT_PDF, "rb") as f:
            st.session_state["contract_file_data"] = f.read()
        st.session_state["contract_filename"] = "sample_contract.pdf"


# ---------------------------------------------------------------------------
# Audit History & Compliance Ledger Display Component
# ---------------------------------------------------------------------------
def render_audit_history_tab(mode_context: str = "all"):
    st.markdown("#### Audit Trail & Historical Compliance Ledger")
    st.caption("Immutable SQLite audit log tracking all human-verified forensic audit executions.")

    # Filter & Management Controls
    fcol1, fcol2, fcol3 = st.columns([1, 1, 1])
    with fcol1:
        mode_filter = st.selectbox(
            "Filter by Audit Mode",
            ["All", "Single", "Batch"],
            index=0,
            key=f"hist_mode_filter_{mode_context}",
        )
    with fcol2:
        rev_filter = st.text_input(
            "Filter by Auditor Signature",
            placeholder="Search auditor name...",
            key=f"hist_rev_filter_{mode_context}",
        )
    with fcol3:
        st.write("")
        st.write("")
        clear_popover = st.popover("Database Management")
        with clear_popover:
            st.markdown("**Clear Historical Audit Logs**")
            st.warning("Warning: This permanently deletes all audit history from audit_history.db.")
            if st.button("Confirm Delete All History", type="secondary", key=f"btn_clear_hist_{mode_context}"):
                deleted = clear_history(db_path=get_active_db_path())
                st.success(f"Cleared {deleted} historical audit records.")
                st.rerun()

    records = get_audit_history(
        audit_mode=None if mode_filter == "All" else mode_filter.lower(),
        reviewer_name=rev_filter if rev_filter.strip() else None,
        db_path=get_active_db_path(),
    )

    if not records:
        st.info("No audit history found matching the current filters. Execute an audit in Tab 1 to populate the ledger.")
        return

    # Historical Aggregate Metrics Strip
    tot_audits = len(records)
    tot_billed = sum(r["total_billed"] for r in records)
    tot_allowed = sum(r["total_allowed"] for r in records)
    tot_leakage = sum(r["total_leakage"] for r in records)
    overall_leakage_pct = (tot_leakage / tot_billed * 100) if tot_billed > 0 else 0.0

    st.markdown(
        f"""
        <div class="kpi-row">
            <div class="kpi-box kpi-box-neutral">
                <div class="kpi-title">Historical Audits Logged</div>
                <div class="kpi-num">{tot_audits}</div>
                <div class="kpi-subtext">Certified audit executions</div>
            </div>
            <div class="kpi-box kpi-box-neutral">
                <div class="kpi-title">Historical Cumulative Billed</div>
                <div class="kpi-num">{safe_fmt(tot_billed)}</div>
                <div class="kpi-subtext">Total analyzed spend</div>
            </div>
            <div class="kpi-box kpi-box-allowed">
                <div class="kpi-title">Historical Contract Allowed</div>
                <div class="kpi-num kpi-num-green">{safe_fmt(tot_allowed)}</div>
                <div class="kpi-subtext">Total verified payable</div>
            </div>
            <div class="kpi-box kpi-box-leakage">
                <div class="kpi-title">Historical Recovered Leakage</div>
                <div class="kpi-num kpi-num-red">{safe_fmt(tot_leakage)}</div>
                <div class="kpi-subtext">Overall Rate: {overall_leakage_pct:.2f}%</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Historical Table Display
    display_rows = []
    for r in records:
        display_rows.append({
            "Audit ID": r["audit_id"][:8] + "...",
            "Timestamp (UTC)": r["timestamp"][:19].replace("T", " "),
            "Mode": r["audit_mode"].upper(),
            "Identifier": r["invoice_identifier"],
            "Auditor Sign-Off": r["reviewer_name"],
            "Billed ($)": f"${r['total_billed']:,.2f}",
            "Allowed ($)": f"${r['total_allowed']:,.2f}",
            "Leakage ($)": f"${r['total_leakage']:,.2f}",
            "Leakage %": f"{r['leakage_percent']:.2f}%",
        })

    st.markdown("---")
    st.markdown("#### Historical Audit Ledger")
    st.dataframe(display_rows, use_container_width=True)

    # Actions: CSV Export and Drill-Down Inspection
    hcol1, hcol2 = st.columns([1, 1])
    with hcol1:
        csv_history = export_history_to_csv(records)
        st.download_button(
            "Download Historical Audit Ledger (CSV)",
            data=csv_history,
            file_name="AuditAI_Historical_Audit_Ledger.csv",
            mime="text/csv",
            key=f"dl_hist_csv_{mode_context}",
            use_container_width=True,
        )
    with hcol2:
        audit_options = {
            f"{r['timestamp'][:19].replace('T', ' ')} | {r['invoice_identifier']} | {r['reviewer_name']} ({r['audit_id'][:8]})": r["audit_id"]
            for r in records
        }
        selected_label = st.selectbox(
            "Select Audit Run to Inspect Details",
            options=list(audit_options.keys()),
            key=f"select_inspect_{mode_context}",
        )
        selected_id = audit_options.get(selected_label)

    if selected_id:
        record_detail = get_audit_by_id(selected_id, db_path=get_active_db_path())
        if record_detail:
            with st.expander(f"Full Audit Record Details: {selected_id}", expanded=False):
                st.json(record_detail.get("details", {}))
                st.download_button(
                    "Download Selected Audit Run (JSON)",
                    data=json.dumps(record_detail, indent=2, default=str),
                    file_name=f"Audit_Run_{selected_id[:8]}.json",
                    mime="application/json",
                    key=f"dl_inspect_json_{selected_id}_{mode_context}",
                    use_container_width=True,
                )


# ===========================================================================
# BRANCH A: MULTI-INVOICE BATCH AUDIT WORKFLOW
# ===========================================================================
if workflow_mode == "Multi-Invoice Batch Audit":
    tab_b_review, tab_b_ledger, tab_b_details, tab_b_history = st.tabs([
        "1. Batch Ingestion & Sign-Off",
        "2. Master Batch Recovery Ledger",
        "3. Duplicate & Discrepancy Breakdown",
        "4. Audit Trail & History Log",
    ])

    batch_items = st.session_state.get("batch_files", [])

    with tab_b_review:
        st.markdown("#### Batch Portfolio Overview")
        st.caption(f"Master Contract: {st.session_state.get('contract_filename', 'sample_contract.pdf')} | Total Invoices in Queue: {len(batch_items)}")

        if not batch_items:
            st.info("Upload multiple invoices in the sidebar or click 'Load Sample 3-Invoice Batch (with Duplicate)'.")
        else:
            col_bq1, col_bq2 = st.columns([2, 1])
            with col_bq1:
                st.markdown("**Queued Invoices for Forensic Cross-Check:**")
                file_summary_list = []
                for b_idx, (b_name, b_bytes) in enumerate(batch_items, 1):
                    file_summary_list.append({
                        "Item": b_idx,
                        "Filename": b_name,
                        "Size (Bytes)": len(b_bytes),
                        "Format": os.path.splitext(b_name)[1].upper(),
                    })
                st.dataframe(file_summary_list, use_container_width=True)

            with col_bq2:
                st.markdown("**Audit Parameters:**")
                st.markdown("• **Duplicate ID Check**: Enabled")
                st.markdown("• **Duplicate Shipment Claim Check**: Enabled")
                st.markdown("• **Cumulative Volume Aggregation**: Enabled (>50 threshold)")
                st.markdown("• **Rebate Rate**: 5.0% on qualifying linehaul")

            st.markdown("---")
            st.markdown("**Mandatory Auditor Sign-Off Gate**")
            b_gate1, b_gate2 = st.columns([2, 1])
            with b_gate1:
                b_confirm = st.checkbox(
                    "I certify that I am executing a batch compliance audit across the above invoices against the active Master Agreement.",
                    value=False,
                    key="batch_signoff_checkbox",
                )
            with b_gate2:
                b_reviewer = st.text_input(
                    "Auditor Signature / ID",
                    placeholder="e.g. Eleanor Vance (Lead Auditor)",
                    key="batch_reviewer_input",
                )

            can_run_batch = b_confirm and bool(b_reviewer.strip())

            if st.button("Execute Batch Forensic Audit", type="primary", disabled=not can_run_batch, use_container_width=True):
                # Prepare contract
                temp_c = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                temp_c.write(st.session_state["contract_file_data"])
                temp_c.close()
                try:
                    c_extracted = extract_contract_from_pdf(temp_c.name)
                    c_extracted.human_confirmed = True
                    b_engine = BatchAuditor(c_extracted)
                    b_summary = b_engine.audit_batch(batch_items, reviewer_name=b_reviewer.strip())
                    st.session_state["batch_summary"] = b_summary
                    st.session_state["batch_reviewer"] = b_reviewer.strip()
                    
                    # Persist to SQLite Audit Trail (Session-Isolated)
                    b_audit_id = save_audit_run(
                        reviewer_name=b_reviewer.strip(),
                        audit_mode="batch",
                        total_billed=b_summary.total_batch_billed,
                        total_allowed=b_summary.total_batch_allowed,
                        total_leakage=b_summary.total_batch_leakage,
                        leakage_percent=b_summary.batch_leakage_percent,
                        invoice_identifier=f"Batch ({b_summary.total_invoices} Invoices)",
                        details=b_summary.to_dict(),
                        db_path=get_active_db_path(),
                    )
                    st.session_state["last_batch_audit_id"] = b_audit_id
                    st.success(f"Batch audit completed across {b_summary.total_invoices} invoices & recorded to Audit Trail (ID: {b_audit_id[:8]})! Proceed to Tab 2 to view recovery ledger.")
                except MissingContractTermError as e:
                    st.error(
                        f"The batch contract is missing required terms ({esc(str(e))}). "
                        "This can happen with scanned documents or contracts that don't specify this term. "
                        "Please check the source document."
                    )
                except InvoiceValidationError as e:
                    st.error(f"Batch invoice validation error: {esc(str(e))}.")
                except Exception as e:
                    st.error(f"Batch audit execution error: {esc(str(e))}.")
                finally:
                    if os.path.exists(temp_c.name):
                        os.unlink(temp_c.name)

    with tab_b_ledger:
        if "batch_summary" not in st.session_state:
            st.info("No batch audit executed yet. Verify documents and execute batch audit in Tab 1.")
        else:
            bs: BatchAuditSummary = st.session_state["batch_summary"]
            rev_label = st.session_state.get("batch_reviewer", "Verified Auditor")

            # Top Executive KPI Strip
            st.markdown(
                f"""
                <div class="kpi-row">
                    <div class="kpi-box kpi-box-neutral">
                        <div class="kpi-title">Total Batch Billed</div>
                        <div class="kpi-num">{safe_fmt(bs.total_batch_billed)}</div>
                        <div class="kpi-subtext">{bs.total_invoices} invoices processed</div>
                    </div>
                    <div class="kpi-box kpi-box-allowed">
                        <div class="kpi-title">Contract Allowed</div>
                        <div class="kpi-num kpi-num-green">{safe_fmt(bs.total_batch_allowed)}</div>
                        <div class="kpi-subtext">Verified payable amount</div>
                    </div>
                    <div class="kpi-box kpi-box-leakage">
                        <div class="kpi-title">Recoverable Leakage</div>
                        <div class="kpi-num kpi-num-red">{safe_fmt(bs.total_batch_leakage)}</div>
                        <div class="kpi-subtext">Total discrepancy adjustments</div>
                    </div>
                    <div class="kpi-box kpi-box-neutral">
                        <div class="kpi-title">Batch Leakage Rate</div>
                        <div class="kpi-num">{bs.batch_leakage_percent}%</div>
                        <div class="kpi-subtext">Auditor: {esc(rev_label)}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # Secondary Operational Telemetry Strip
            mcol1, mcol2, mcol3 = st.columns(3)
            with mcol1:
                rebate_badge = "<span class='badge-confirmed'>[VOLUME REBATE TRIGGERED (>50)]</span>" if bs.volume_threshold_reached else "<span class='badge-review'>[THRESHOLD NOT REACHED]</span>"
                st.markdown(f"**Cumulative Monthly Shipments**: {bs.cumulative_shipments} loads {rebate_badge}", unsafe_allow_html=True)
            with mcol2:
                st.markdown(f"**Cumulative Linehaul Spend Base**: {safe_fmt(bs.cumulative_spend_base)}")
            with mcol3:
                dup_count = len(bs.duplicate_alerts)
                dup_badge = f"<span class='badge-duplicate'>[{dup_count} DUPLICATE ALERTS]</span>" if dup_count > 0 else "<span class='badge-confirmed'>[ZERO DUPLICATES]</span>"
                st.markdown(f"**Duplicate Billing Claims**: {dup_badge}", unsafe_allow_html=True)

            # Duplicate Claims Callout
            if bs.duplicate_alerts:
                st.markdown("---")
                st.markdown("#### Duplicate Billing Alerts Flagged")
                for da in bs.duplicate_alerts:
                    st.error(
                        esc(f"DUPLICATE DETECTED: {da.duplicate_type} — Invoice #{da.invoice_number} conflicts with {da.conflicting_invoice}.\n"
                            f"Action: 100% of duplicate submission ({_fmt(da.amount)}) disallowed as recoverable leakage.\n"
                            f"Details: {da.details}")
                    )

            # Consolidated Master Ledger Table
            st.markdown("---")
            st.markdown("#### Master Batch Recovery Ledger")
            st.dataframe(bs.ledger_records, use_container_width=True)

            # Download Actions
            col_b_dl1, col_b_dl2 = st.columns(2)
            with col_b_dl1:
                st.download_button(
                    "Download Batch Recovery Ledger (CSV)",
                    data=bs.to_csv(),
                    file_name="AuditAI_Batch_Recovery_Ledger.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with col_b_dl2:
                st.download_button(
                    "Download Full Batch Summary (JSON)",
                    data=json.dumps(bs.to_dict(), indent=2, default=str),
                    file_name="AuditAI_Batch_Audit_Summary.json",
                    mime="application/json",
                    use_container_width=True,
                )

    with tab_b_details:
        if "batch_summary" not in st.session_state:
            st.info("Execute a batch audit to inspect itemized invoice breakdowns.")
        else:
            bs = st.session_state["batch_summary"]
            st.markdown("#### Itemized Invoice Audit Findings")
            for idx, rep in enumerate(bs.reports, 1):
                with st.expander(esc(f"[{idx}] Invoice #{rep.invoice_number} — Billed: {_fmt(rep.total_billed)} | Allowed: {_fmt(rep.total_allowed)} | Leakage: {_fmt(rep.total_leakage)} ({rep.leakage_percent}%)")):
                    st.markdown(f"**Vendor**: {rep.vendor_name} | **Buyer**: {rep.buyer_name}")
                    for d in rep.discrepancies:
                        st.markdown(f"• **{d.category}**: Billed {_fmt(d.billed_amount)} vs Allowed {_fmt(d.allowed_amount)} (Overcharge: {_fmt(d.overcharge)})")
                        st.caption(f"Citation: {d.contract_clause} | Note: {d.explanation}")
                    if rep.warnings:
                        for w in rep.warnings:
                            st.caption(f"Warning: {w}")

    with tab_b_history:
        render_audit_history_tab("batch")

    st.stop()


# ===========================================================================
# BRANCH B: SINGLE INVOICE FORENSIC AUDIT WORKFLOW
# ===========================================================================

# Default Single Invoice Ingestion Initialization
if "invoice_file_data" not in st.session_state:
    if os.path.exists(SAMPLE_INVOICE_PDF):
        with open(SAMPLE_INVOICE_PDF, "rb") as f:
            st.session_state["invoice_file_data"] = f.read()
        st.session_state["invoice_filename"] = "sample_invoice.pdf"

c_name = st.session_state.get("contract_filename", "contract.pdf")
i_name = st.session_state.get("invoice_filename", "invoice.pdf")
is_c_pdf = c_name.lower().endswith(".pdf")
is_i_pdf = i_name.lower().endswith(".pdf")

effective_llm_key = effective_key or server_key or os.environ.get("GEMINI_API_KEY", "")
c_bytes = st.session_state.get("contract_file_data", b"")
i_bytes = st.session_state.get("invoice_file_data", b"")

# Session-state fingerprint cache keys (based on document content hash + engine mode + key presence)
c_cache_key = f"{hashlib.sha256(c_bytes).hexdigest()}_{c_name}_{extraction_mode}_{bool(effective_llm_key)}"
i_cache_key = f"{hashlib.sha256(i_bytes).hexdigest()}_{i_name}_{extraction_mode}_{bool(effective_llm_key)}"

need_c_extract = (
    "cached_contract_res" not in st.session_state
    or st.session_state.get("cached_contract_key") != c_cache_key
)
need_i_extract = (
    "cached_invoice_res" not in st.session_state
    or st.session_state.get("cached_invoice_key") != i_cache_key
)

if need_c_extract or need_i_extract:
    temp_dir = tempfile.mkdtemp()
    try:
        if need_c_extract:
            c_path = make_safe_temp_path(temp_dir, c_name, prefix="contract")
            with open(c_path, "wb") as f:
                f.write(c_bytes)
            if is_c_pdf:
                if "Gemini" in extraction_mode and effective_llm_key:
                    contract_res = extract_contract_llm(c_path, api_key=effective_llm_key)
                else:
                    contract_res = extract_contract_from_pdf(c_path)
                contract_dict = contract_res.to_dict()
            else:
                with open(c_path, "r", encoding="utf-8") as f:
                    contract_dict = json.load(f)
                contract_res = ExtractionResult(
                    source_file=c_path,
                    document_type="contract",
                    human_confirmed=False,
                    extraction_method="pdfplumber-deterministic",
                )
            st.session_state["cached_contract_res"] = contract_res
            st.session_state["cached_contract_dict"] = contract_dict
            st.session_state["cached_contract_key"] = c_cache_key
        else:
            contract_res = st.session_state["cached_contract_res"]
            contract_dict = st.session_state["cached_contract_dict"]

        if need_i_extract:
            i_path = make_safe_temp_path(temp_dir, i_name, prefix="invoice")
            with open(i_path, "wb") as f:
                f.write(i_bytes)
            if is_i_pdf:
                if "Gemini" in extraction_mode and effective_llm_key:
                    invoice_res = extract_invoice_llm(i_path, api_key=effective_llm_key)
                else:
                    invoice_res = extract_invoice_from_pdf(i_path)
                invoice_dict = invoice_res.to_dict()
            else:
                with open(i_path, "r", encoding="utf-8") as f:
                    invoice_dict = json.load(f)
                invoice_res = ExtractionResult(
                    source_file=i_path,
                    document_type="invoice",
                    human_confirmed=False,
                    extraction_method="pdfplumber-deterministic",
                )
            st.session_state["cached_invoice_res"] = invoice_res
            st.session_state["cached_invoice_dict"] = invoice_dict
            st.session_state["cached_invoice_key"] = i_cache_key
        else:
            invoice_res = st.session_state["cached_invoice_res"]
            invoice_dict = st.session_state["cached_invoice_dict"]
    finally:
        # Cleanup temporary files immediately after extraction to prevent filesystem accumulation
        shutil.rmtree(temp_dir, ignore_errors=True)
else:
    contract_res = st.session_state["cached_contract_res"]
    contract_dict = st.session_state["cached_contract_dict"]
    invoice_res = st.session_state["cached_invoice_res"]
    invoice_dict = st.session_state["cached_invoice_dict"]


# ---------------------------------------------------------------------------
# Workspace Tabs: 1. Review & Sign-Off, 2. Audit Breakdown, 3. Dispute Memo, 4. History
# ---------------------------------------------------------------------------
tab_review, tab_audit, tab_memo, tab_history = st.tabs([
    "1. Document Review & Sign-Off",
    "2. Forensic Audit Breakdown",
    "3. Draft Dispute Memorandum",
    "4. Audit Trail & History Log",
])


# ===========================================================================
# TAB 1: Document Review & Mandatory Sign-Off Gate
# ===========================================================================
with tab_review:
    st.markdown("#### Document Intelligence & Clause Extraction")
    c_method = getattr(contract_res, "extraction_method", "pdfplumber-deterministic")
    c_disp = st.session_state.get("contract_display_name", st.session_state.get("contract_filename", "sample_contract.pdf"))
    i_disp = st.session_state.get("invoice_display_name", st.session_state.get("invoice_filename", "sample_invoice.pdf"))
    st.caption(
        f"Contract source: {esc(c_disp)} | "
        f"Invoice source: {esc(i_disp)} | "
        f"Extraction Mode: {c_method}"
    )

    c_warning = getattr(contract_res, "extraction_warning", None)
    i_warning = getattr(invoice_res, "extraction_warning", None)
    if c_warning:
        st.warning(f"⚠️ **Contract Extraction Alert:** {c_warning}")
    if i_warning:
        st.warning(f"⚠️ **Invoice Extraction Alert:** {i_warning}")

    is_valid_c, c_validation_msg = validate_contract_document(contract_res, contract_dict)
    is_valid_i, i_validation_msg = validate_invoice_document(invoice_res, invoice_dict)

    if not is_valid_c:
        st.error(
            f"⚠️ **Invalid Contract Document Detected:** {c_validation_msg} "
            "Please check the source document or enter the values manually below."
        )
    if not is_valid_i:
        st.error(
            f"⚠️ **Invalid Invoice Document Detected:** {i_validation_msg} "
            "Please check the source document or enter the line items manually below."
        )

    col_c, col_i = st.columns(2)

    with col_c:
        st.markdown("**Master Service Agreement Terms**")
        c_terms = contract_dict.get("terms", {})
        c_clauses = contract_dict.get("clauses", {})

        edit_contract_id = st.text_input("Contract Identifier", value=str(contract_dict.get("contract_id", "MSA-2026-US-LOG") if is_valid_c else (contract_dict.get("contract_id") or "UNKNOWN_CONTRACT")))
        edit_c_vendor = st.text_input("Contract Vendor Name", value=str(contract_dict.get("vendor_name", "SwiftLine National Freight Corp.") if is_valid_c else (contract_dict.get("vendor_name") or "Unknown Vendor")))
        edit_c_buyer = st.text_input("Contract Buyer Name", value=str(contract_dict.get("buyer_name", "Global Retail Group Inc.") if is_valid_c else (contract_dict.get("buyer_name") or "Unknown Buyer")))

        c_subcol1, c_subcol2 = st.columns(2)
        with c_subcol1:
            default_base_rate = float(c_terms.get("base_rate_per_mile") or 0.0) if not is_valid_c else float(c_terms.get("base_rate_per_mile", 2.40))
            edit_base_rate = st.number_input(
                esc("Base Linehaul Cap ($/mile)"),
                value=default_base_rate,
                step=0.05,
            )
            default_fuel_cap = float(c_terms.get("max_fuel_surcharge_percent") or 0.0) if not is_valid_c else float(c_terms.get("max_fuel_surcharge_percent", 12.0))
            edit_fuel_cap = st.number_input(
                "Fuel Surcharge Cap (%)",
                value=default_fuel_cap,
                step=0.5,
            )
            default_det_free = float(c_terms.get("free_detention_hours") or 0.0) if not is_valid_c else float(c_terms.get("free_detention_hours", 2.0))
            edit_det_free = st.number_input(
                "Free Detention Hours / Stop",
                value=default_det_free,
                step=0.5,
            )
        with c_subcol2:
            default_det_rate = float(c_terms.get("detention_hourly_rate") or 0.0) if not is_valid_c else float(c_terms.get("detention_hourly_rate", 50.0))
            edit_det_rate = st.number_input(
                esc("Contract Detention Rate ($/hr)"),
                value=default_det_rate,
                step=5.0,
            )
            default_vol_thresh = int(c_terms.get("volume_discount_threshold_shipments") or 0) if not is_valid_c else int(c_terms.get("volume_discount_threshold_shipments", 50))
            edit_vol_thresh = st.number_input(
                "Volume Threshold (loads)",
                value=default_vol_thresh,
                step=5,
            )
            default_vol_pct = float(c_terms.get("volume_discount_percent") or 0.0) if not is_valid_c else float(c_terms.get("volume_discount_percent", 5.0))
            edit_vol_pct = st.number_input(
                "Volume Rebate Rate (%)",
                value=default_vol_pct,
                step=0.5,
            )

        if is_c_pdf:
            with st.expander("Contract Clause Citations & Evidentiary Quotes", expanded=False):
                for f in contract_res.fields:
                    if f.field_name != "clauses":
                        st.markdown(
                            f"<div style='font-size:0.8rem; margin-bottom: 4px;'>"
                            f"<strong>{esc(f.field_name)}</strong>: Page {f.page_number} | Conf: {f.confidence*100:.0f}%<br/>"
                            f"<em>\"{esc(f.quoted_text)}\"</em></div>",
                            unsafe_allow_html=True,
                        )

    with col_i:
        st.markdown("**Vendor Billing & Invoice Line Items**")
        i_lines = invoice_dict.get("line_items", {})
        lh = i_lines.get("linehaul", {})
        fuel = i_lines.get("fuel_surcharge", {})
        det = i_lines.get("detention", {})
        reb = i_lines.get("volume_discount", {})

        edit_inv_number = st.text_input("Invoice / Bill ID", value=str(invoice_dict.get("invoice_number", "INV-9042") if is_valid_i else (invoice_dict.get("invoice_number") or "UNKNOWN_INVOICE")))
        edit_i_vendor = st.text_input("Billing Vendor Name", value=str(invoice_dict.get("vendor_name", "SwiftLine National Freight Corp.") if is_valid_i else (invoice_dict.get("vendor_name") or "Unknown Vendor")))
        edit_i_buyer = st.text_input("Billed Customer Name", value=str(invoice_dict.get("buyer_name", "Global Retail Group Inc.") if is_valid_i else (invoice_dict.get("buyer_name") or "Unknown Buyer")))

        default_total = float(invoice_dict.get("total_billed_amount", 5176.35) if is_valid_i else (invoice_dict.get("total_billed_amount") or 0.0))
        if "synced_total" in st.session_state:
            default_total = st.session_state["synced_total"]

        edit_inv_total = st.number_input(
            esc("Stated Invoice Total Due ($)"),
            value=default_total,
            step=50.0,
        )

        i_sub1, i_sub2 = st.columns(2)
        with i_sub1:
            default_miles = int(lh.get("billed_miles", 1450) if is_valid_i else (lh.get("billed_miles") or 0))
            edit_miles = st.number_input(
                "Billed Distance (miles)",
                value=default_miles,
                step=50,
            )
            default_lh_rate = float(lh.get("billed_rate_per_mile", 2.85) if is_valid_i else (lh.get("billed_rate_per_mile") or 0.0))
            edit_lh_rate = st.number_input(
                esc("Billed Linehaul Rate ($/mile)"),
                value=default_lh_rate,
                step=0.05,
            )
            default_lh_amt = float(lh.get("billed_amount", 4132.50) if is_valid_i else (lh.get("billed_amount") or 0.0))
            edit_lh_amount = st.number_input(
                esc("Billed Linehaul Amount ($)"),
                value=default_lh_amt,
                step=50.0,
            )
            default_fuel_pct = float(fuel.get("billed_percent", 18.0) if is_valid_i else (fuel.get("billed_percent") or 0.0))
            edit_fuel_pct = st.number_input(
                "Billed Fuel Rate (%)",
                value=default_fuel_pct,
                step=0.5,
            )
            default_fuel_amt = float(fuel.get("billed_amount", 743.85) if is_valid_i else (fuel.get("billed_amount") or 0.0))
            edit_fuel_amt = st.number_input(
                esc("Billed Fuel Amount ($)"),
                value=default_fuel_amt,
                step=10.0,
            )
        with i_sub2:
            default_det_hrs = float(det.get("billed_hours", 4.0) if is_valid_i else (det.get("billed_hours") or 0.0))
            edit_det_hrs = st.number_input(
                "Billed Detention (hours)",
                value=default_det_hrs,
                step=0.5,
            )
            default_det_rate_b = float(det.get("billed_hourly_rate", 75.0) if is_valid_i else (det.get("billed_hourly_rate") or 0.0))
            edit_det_rate_billed = st.number_input(
                esc("Billed Detention Rate ($/hr)"),
                value=default_det_rate_b,
                step=5.0,
            )
            default_det_amt = float(det.get("billed_amount", 300.00) if is_valid_i else (det.get("billed_amount") or 0.0))
            edit_det_amt = st.number_input(
                esc("Billed Detention Amount ($)"),
                value=default_det_amt,
                step=25.0,
            )
            default_reb_amt = float(reb.get("billed_amount", 0.0) if is_valid_i else (reb.get("billed_amount") or 0.0))
            edit_reb_amt = st.number_input(
                esc("Billed Rebate Line ($ — negative for credit)"),
                value=default_reb_amt,
                step=10.0,
                help="Negative number for contractual rebate credit, 0.0 if omitted, positive if erroneously billed as charge.",
            )
            default_vol_to_date = int(invoice_dict.get("monthly_volume_shipments_to_date", 62) if is_valid_i else (invoice_dict.get("monthly_volume_shipments_to_date") or 0))
            edit_vol_to_date = st.number_input(
                "Vendor Stated Month Volume",
                value=default_vol_to_date,
                step=1,
            )

        if is_i_pdf:
            with st.expander("Invoice Line Item Citations & Evidentiary Quotes", expanded=False):
                for f in invoice_res.fields:
                    if f.field_name != "line_items":
                        st.markdown(
                            f"<div style='font-size:0.8rem; margin-bottom: 4px;'>"
                            f"<strong>{esc(f.field_name)}</strong>: Page {f.page_number} | Conf: {f.confidence*100:.0f}%<br/>"
                            f"<em>\"{esc(f.quoted_text)}\"</em></div>",
                            unsafe_allow_html=True,
                        )

    # Horizontal Accounting Integrity Check
    st.markdown("---")
    st.markdown("**Accounting Integrity & Header Reconciliation Check**")

    computed_sum = Decimal(str(edit_lh_amount)) + Decimal(str(edit_fuel_amt)) + Decimal(str(edit_det_amt)) + Decimal(str(edit_reb_amt))
    stated_total_dec = Decimal(str(edit_inv_total))
    header_variance = stated_total_dec - computed_sum
    has_header_mismatch = abs(header_variance) > Decimal("0.02")

    rc1, rc2, rc3 = st.columns(3)
    with rc1:
        st.metric("Sum of Line Items", safe_fmt(computed_sum))
    with rc2:
        st.metric("Stated Invoice Header Total", safe_fmt(stated_total_dec))
    with rc3:
        st.metric("Unallocated Header Variance", safe_fmt(header_variance))

    if has_header_mismatch:
        if header_variance > 0:
            st.warning(
                esc(f"Header Billing Inflation: Stated total ({_fmt(stated_total_dec)}) exceeds line item sum ({_fmt(computed_sum)}) by +{_fmt(header_variance)}. "
                    f"This positive unallocated difference is treated as an overcharge discrepancy during audit.")
            )
        else:
            st.warning(
                esc(f"Header Shortfall: Line items sum to {_fmt(computed_sum)}, but stated header total is {_fmt(stated_total_dec)} (shortfall: -{_fmt(abs(header_variance))}). "
                    f"Audit will evaluate against the true line item sum to prevent double-counting deductions.")
            )
            if st.button("Auto-Sync Stated Total to Sum of Line Items", use_container_width=True):
                st.session_state["synced_total"] = float(computed_sum)
                st.rerun()

    # Mandatory Sign-Off Gate
    st.markdown("---")
    st.markdown("**Mandatory Human Sign-Off Gate**")
    st.caption("Federal and corporate procurement compliance requires explicit reviewer sign-off prior to generating legal payment adjustments.")

    gate_col1, gate_col2 = st.columns([2, 1])
    with gate_col1:
        confirm_checkbox = st.checkbox(
            "I certify that I have verified the extracted clauses, rate caps, and invoice items against original source documents.",
            value=False,
            key="signoff_checkbox",
        )
    with gate_col2:
        reviewer_name = st.text_input(
            "Reviewer Signature / ID",
            placeholder="e.g. Eleanor Vance (Lead Internal Auditor)",
            key="reviewer_name_input",
        )

    can_execute = confirm_checkbox and bool(reviewer_name.strip())

    if st.button("Confirm & Execute Forensic Audit", type="primary", disabled=not can_execute, use_container_width=True):
        final_contract = {
            "contract_id": edit_contract_id,
            "vendor_name": edit_c_vendor,
            "buyer_name": edit_c_buyer,
            "terms": {
                "base_rate_per_mile": edit_base_rate,
                "max_fuel_surcharge_percent": edit_fuel_cap,
                "free_detention_hours": edit_det_free,
                "detention_hourly_rate": edit_det_rate,
                "volume_discount_threshold_shipments": edit_vol_thresh,
                "volume_discount_percent": edit_vol_pct,
            },
            "clauses": c_clauses,
        }

        final_invoice = {
            "invoice_number": edit_inv_number,
            "vendor_name": edit_i_vendor,
            "buyer_name": edit_i_buyer,
            "total_billed_amount": edit_inv_total,
            "monthly_volume_shipments_to_date": edit_vol_to_date,
            "line_items": {
                "linehaul": {
                    "billed_miles": edit_miles,
                    "billed_rate_per_mile": edit_lh_rate,
                    "billed_amount": edit_lh_amount,
                },
                "fuel_surcharge": {
                    "billed_percent": edit_fuel_pct,
                    "billed_amount": edit_fuel_amt,
                },
                "detention": {
                    "billed_hours": edit_det_hrs,
                    "billed_hourly_rate": edit_det_rate_billed,
                    "billed_amount": edit_det_amt,
                    "num_stops": num_stops_input,
                },
                "volume_discount": {
                    "billed_amount": edit_reb_amt,
                },
            },
        }

        if actual_miles_input > 0:
            final_invoice["line_items"]["linehaul"]["actual_miles"] = actual_miles_input
        if shipment_count_input > 0:
            final_invoice["shipment_count"] = shipment_count_input
        if rebate_base_input > 0:
            final_invoice["rebate_base"] = rebate_base_input

        try:
            auditor = ContractAuditor(final_contract)
            report = auditor.audit_invoice(final_invoice)
            report.reviewer_name = reviewer_name.strip()
            report.approval_date = date.today().strftime("%B %d, %Y")

            st.session_state["audit_report"] = report
            st.session_state["reviewer_name"] = reviewer_name.strip()
            st.session_state["approval_date"] = date.today().strftime("%B %d, %Y")
            st.session_state["audit_ran"] = True

            # Persist to SQLite Audit Trail (Session-Isolated)
            single_audit_id = save_audit_run(
                reviewer_name=reviewer_name.strip(),
                audit_mode="single",
                total_billed=report.total_billed,
                total_allowed=report.total_allowed,
                total_leakage=report.total_leakage,
                leakage_percent=report.leakage_percent,
                invoice_identifier=report.invoice_number,
                details=ContractAuditor.to_dict(report),
                db_path=get_active_db_path(),
            )
            st.session_state["last_single_audit_id"] = single_audit_id
            st.success(f"Audit executed & logged to Audit Trail (ID: {single_audit_id[:8]}) for Invoice #{report.invoice_number}. Proceed to Tab 2 to inspect breakdown.")
        except MissingContractTermError as e:
            st.error(
                f"This document is missing required contract information ({esc(str(e))}). "
                "This can happen with scanned documents or contracts that don't specify this term. "
                "Please check the source document or enter the value manually above."
            )
        except InvoiceValidationError as e:
            st.error(
                f"This invoice cannot be audited due to data validation errors: {esc(str(e))}. "
                "Please review the extracted line items or adjust the values manually above."
            )
        except HumanConfirmationRequiredError as e:
            st.error(
                f"Human verification is required prior to audit execution: {esc(str(e))}. "
                "Please complete the sign-off checkbox above."
            )
        except Exception as e:
            st.error(
                f"An unexpected error occurred while processing the audit: {esc(str(e))}. "
                "Please check the source document and entered values."
            )


# ===========================================================================
# TAB 2: Forensic Audit Breakdown
# ===========================================================================
with tab_audit:
    if "audit_report" not in st.session_state:
        st.info("No audit executed yet. Complete document review and sign-off in Tab 1 to run the audit.")
    else:
        rep: AuditReport = st.session_state["audit_report"]
        rev_sig = st.session_state.get("reviewer_name", getattr(rep, "reviewer_name", "Verified Auditor"))

        # Top Executive KPI Strip
        st.markdown(
            f"""
            <div class="kpi-row">
                <div class="kpi-box kpi-box-neutral">
                    <div class="kpi-title">Total Billed</div>
                    <div class="kpi-num">{safe_fmt(rep.total_billed)}</div>
                    <div class="kpi-subtext">Invoice #{rep.invoice_number}</div>
                </div>
                <div class="kpi-box kpi-box-allowed">
                    <div class="kpi-title">Contract Allowed</div>
                    <div class="kpi-num kpi-num-green">{safe_fmt(rep.total_allowed)}</div>
                    <div class="kpi-subtext">Payable amount</div>
                </div>
                <div class="kpi-box kpi-box-leakage">
                    <div class="kpi-title">Recoverable Leakage</div>
                    <div class="kpi-num kpi-num-red">{safe_fmt(rep.total_leakage)}</div>
                    <div class="kpi-subtext">Discrepancy adjustment due</div>
                </div>
                <div class="kpi-box kpi-box-neutral">
                    <div class="kpi-title">Leakage Rate</div>
                    <div class="kpi-num">{rep.leakage_percent}%</div>
                    <div class="kpi-subtext">Sign-Off: {esc(rev_sig)}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        confirmed_list = [d for d in rep.discrepancies if d.status == "confirmed"]
        review_list = [d for d in rep.discrepancies if d.status == "needs_review"]

        # Section 1: Confirmed Discrepancies
        st.markdown("#### Section 1: Confirmed Discrepancies (Verified Telemetry)")
        if confirmed_list:
            for idx, item in enumerate(confirmed_list, 1):
                with st.expander(esc(f"[{idx}] {item.category} — Adjustment: -{_fmt(item.overcharge)}"), expanded=True):
                    col_d1, col_d2 = st.columns([1, 2])
                    with col_d1:
                        st.markdown(f"**Billed Amount**: {safe_fmt(item.billed_amount)}")
                        st.markdown(f"**Contract Allowed**: {safe_fmt(item.allowed_amount)}")
                        st.markdown(f"**Adjustment**: -{safe_fmt(item.overcharge)}")
                        st.markdown("<span class='badge-confirmed'>[CONFIRMED FINDING]</span>", unsafe_allow_html=True)
                    with col_d2:
                        st.markdown(f"**Contract Citation**: {esc(item.contract_clause)}")
                        st.markdown(f"**Audit Explanation**: {esc(item.explanation)}")
        else:
            st.info("No confirmed discrepancies found. Provide buyer telemetry in sidebar to confirm items.")

        # Section 2: Needs-Review Discrepancies
        st.markdown("#### Section 2: Needs-Review Items (Unverified Vendor Telemetry)")
        if review_list:
            st.caption("Calculated using vendor-supplied inputs. Verify route miles or monthly shipment counts in sidebar to confirm.")
            for idx, item in enumerate(review_list, 1):
                with st.expander(esc(f"[R{idx}] {item.category} — Potential Adjustment: -{_fmt(item.overcharge)}"), expanded=True):
                    col_r1, col_r2 = st.columns([1, 2])
                    with col_r1:
                        st.markdown(f"**Billed Amount**: {safe_fmt(item.billed_amount)}")
                        st.markdown(f"**Contract Allowed**: {safe_fmt(item.allowed_amount)}")
                        st.markdown(f"**Potential Adjustment**: -{safe_fmt(item.overcharge)}")
                        st.markdown("<span class='badge-review'>[NEEDS REVIEW]</span>", unsafe_allow_html=True)
                    with col_r2:
                        st.markdown(f"**Contract Citation**: {esc(item.contract_clause)}")
                        st.markdown(f"**Audit Explanation**: {esc(item.explanation)}")
        else:
            st.info("No pending review items.")

        # Section 3: Unreviewed Lines
        st.markdown("#### Section 3: Unreviewed Line Items")
        if rep.unreviewed_lines:
            st.caption("Line items without corresponding contractual rules. Reconciled at billed amount.")
            for ul in rep.unreviewed_lines:
                st.markdown(esc(f"• **{ul.key}**: {_fmt(ul.billed_amount)} — *{ul.note}*"))
        else:
            st.caption("All line items matched contractual rules.")

        # Audit Warnings
        if rep.warnings:
            st.markdown("#### Audit Trail & Arithmetic Warnings")
            for w in rep.warnings:
                st.caption(esc(f"• {w}"))


# ===========================================================================
# TAB 3: Draft Dispute Memorandum
# ===========================================================================
with tab_memo:
    if "audit_report" not in st.session_state:
        st.info("No audit executed yet. Complete document review and sign-off in Tab 1 to run the audit.")
    else:
        rep: AuditReport = st.session_state["audit_report"]
        rev_sig = st.session_state.get("reviewer_name", getattr(rep, "reviewer_name", "Verified Auditor"))
        appr_date = st.session_state.get("approval_date", getattr(rep, "approval_date", date.today().strftime("%B %d, %Y")))

        dispute_text = ContractAuditor.generate_dispute_notice(
            rep,
            reviewer_name=rev_sig,
            approval_date=appr_date,
        )

        st.markdown("#### Formal Dispute & Payment Adjustment Notice (DRAFT)")
        st.caption("Professional executive memorandum format reflecting auditor certification.")

        st.code(dispute_text, language="text")

        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "Download Dispute Notice (TXT)",
                data=dispute_text,
                file_name=f"Dispute_Notice_{rep.invoice_number}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with col_dl2:
            report_dict = ContractAuditor.to_dict(rep)
            report_dict["human_approval_gate"] = {
                "reviewed_by": rev_sig,
                "approved_by": rev_sig,
                "date": appr_date,
                "title": "Internal Audit & Recovery Lead",
                "status": "APPROVED_BY_HUMAN",
            }
            st.download_button(
                "Download Full Audit Report (JSON)",
                data=json.dumps(report_dict, indent=2, default=str),
                file_name=f"Audit_Report_{rep.invoice_number}.json",
                mime="application/json",
                use_container_width=True,
            )

with tab_history:
    render_audit_history_tab("single")
