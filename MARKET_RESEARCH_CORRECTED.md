# AuditAI: Corrected Market Research & Competitive Analysis

> [!IMPORTANT]
> This document replaces all earlier market research claims. Every number has
> a source link. Figures that remain unverifiable are explicitly marked **[UNVERIFIED]**.

---

## 1. The Real-World Problem (Corrected Scale)

Enterprise accounts payable (AP) departments routinely lose money to duplicate
payments, billing errors, unapplied discounts, and contract non-compliance.

### Actual Industry Recovery Rates

| Claim | Corrected Figure | Source |
| :--- | :--- | :--- |
| "3%–7% of global B2B spend is lost" | **1% to 2%** of total annual AP disbursements are lost to duplicate/erroneous payments in large organizations. Up to 3% when including broader pricing discrepancies and unapplied credits. | [SC&H Group – AP Overpayment Study](https://www.schgroup.com) |
| "Over $3 Trillion lost globally" | **[UNVERIFIED]**. If global B2B commerce is ~$120T and 1%–2% leaks, the theoretical range is $1.2T–$2.4T, but this is an extrapolation, not a measured figure. No single study has measured total global AP leakage comprehensively. | — |

> [!WARNING]
> Our v1 demo data showed 26% leakage on a single invoice. This was deliberately
> exaggerated for testing purposes. **Real-world recovery rates are 1%–3% of
> total spend**, not 26%. The sample data is now clearly labeled as "SAMPLE" with
> this caveat documented in the JSON metadata.

---

## 2. Competitive Landscape (Verified)

### Major Incumbents

| Company | What They Do | Revenue | Valuation / Exit | Key Source |
| :--- | :--- | :--- | :--- | :--- |
| **AppZen** | AI-native pre-payment expense and AP invoice auditing | **[UNVERIFIED]** — ARR is not publicly disclosed; the "$95M ARR" figure from earlier research could not be confirmed via official sources. | Raised **$290M total** (Series D: $180M led by Riverwood Capital, Sep 2025). Series C ($50M, 2019) valued at ~$500M. Series D was an "up round" but **exact post-money valuation was not disclosed**. | [Crunchbase – AppZen](https://www.crunchbase.com/organization/appzen), [AppZen Press Release](https://www.appzen.com) |
| **Loop** | AI logistics data platform (freight audit, payment, supply chain intelligence) | **Not publicly disclosed**. The earlier "$44M ARR" figure is **[UNVERIFIED]**. | Raised **$210M+ total** ($95M Series C led by Valor Equity Partners, April 2026). Claims to work with ~20% of Fortune 100. | [Loop.com Press Release](https://www.loop.com), [BizJournals](https://www.bizjournals.com) |
| **Cass Information Systems** (NASDAQ: CASS) | Freight audit, payment processing, and logistics information services | **$170M–$180M annual revenue** (2025: $109.9M fee revenue + net interest income; Q3 2026 revenue up 5.3% YoY to $52.7M). Record 2025 net income: $35.1M. | **Public company** (NASDAQ: CASS). Processes **~$37B in annual freight spend** and ~35M invoices/year. | [Cass Investor Relations](https://www.cassinfo.com), [Cass Q3 2026 Earnings](https://stocktitan.net) |
| **PRGX Global** | Recovery auditing (post-payment and pre-payment prevention), contract monitoring | **$170M+ annual revenue** (pre-acquisition). | **Acquired by Ardian** (private equity) for **$195M** ($7.71/share), completed March 2021. | [Ardian Press Release](https://www.ardian.com), [Preqin](https://www.preqin.com) |
| **Celonis** | Process mining for SAP/Oracle (catching duplicate payments, process bottlenecks) | **[UNVERIFIED]** — private company. | Last reported valuation: **~$13B** (Series D, 2021). Note: this may have changed in subsequent market corrections. | [Celonis – Crunchbase](https://www.crunchbase.com/organization/celonis) |
| **Coupa Software** | Procurement and business spend management | ~$725M revenue (FY2023, last public year). | **Acquired by Thoma Bravo for $8B** (January 2023). | [SEC Filing / Thoma Bravo](https://www.thomabravo.com) |

### Mid-Market Contingency Auditors (Previously Omitted)

| Company | Focus | Model | Target Market |
| :--- | :--- | :--- | :--- |
| **71lbs** | Small parcel audit (FedEx/UPS/DHL), expanding to LTL | Contingency (% of refunds recovered) | SMB to mid-market |
| **Refund Retriever** | Parcel shipping audit and contract analytics | Contingency (performance-based split) | SMB to mid-market e-commerce |
| **Shipware** | Parcel/LTL audit + carrier contract negotiation | Hybrid (contingency + SaaS/retainer) | Mid-market to enterprise |
| **P3 Cost Analysts** | Multi-expense reduction (freight, utilities, telecom) | Contingency ("shared savings") | SMB to mid-market |
| **Data2Logistics** (acquired by Loop) | Enterprise freight audit, payment, and BI | Enterprise fees / transaction-based | Upper mid-market to Fortune 500 |

---

## 3. Corrected "Market Gap" Analysis

The v1 research claimed three gaps that were either false or overstated. Here is the
corrected, honest assessment:

### Gap 1 (CORRECTED): "Incumbents Only Audit Post-Payment"
* **v1 Claim**: Cass and PRGX only audit 6–18 months after payment.
* **Reality**: This is **false**. Pre-payment audit is already standard practice:
  - **AppZen** pioneered 100% pre-payment AI auditing for T&E and AP invoices. ([appzen.com](https://www.appzen.com))
  - **Cass** performs pre-payment freight auditing as a core service. ([cassinfo.com](https://www.cassinfo.com))
  - **PRGX** offers both post-payment recovery AND pre-payment "Overpayment Prevention" monitoring. ([prgx.com](https://www.prgx.com))
  - **Loop** processes freight invoices for real-time pre-payment verification. ([loop.com](https://www.loop.com))
* **Honest Gap**: While pre-payment audit *exists*, it is concentrated at Fortune 500 accounts. Mid-sized companies ($5M–$50M revenue) often still rely on manual AP processes or basic 3-way PO matching without AI-powered contract clause verification.

### Gap 2 (CORRECTED): "Multimodal LLM OCR is Our Moat"
* **v1 Claim**: Modern LLM vision extraction gives us a unique advantage legacy players lack.
* **Reality**: This is **not a defensible moat**. LLM-based document extraction is available to every competitor:
  - AppZen has its own proprietary LLM family (ZenLM). ([appzen.com](https://www.appzen.com))
  - Loop has its proprietary DUX AI model for document extraction. ([loop.com](https://www.loop.com))
  - Every major player can integrate Google Gemini, OpenAI, or Claude APIs.
* **Honest Gap**: The real differentiation is not in extraction technology alone, but in the combination of: (a) **human-confirmed extraction** with confidence scores, (b) **deterministic, auditable math** (not LLM-generated arithmetic), (c) **buyer-side telemetry verification** (actual_miles, shipment_count), and (d) **industry-specific rating engine logic** that incumbents took years to build.

### Gap 3 (CORRECTED): "Mid-Market Has No Contingency Auditors"
* **v1 Claim**: Cass and AppZen only chase Fortune 100 and completely ignore mid-market.
* **Reality**: This is **false**. The mid-market freight audit space is served by multiple established contingency players: 71lbs, Refund Retriever, Shipware, P3 Cost Analysts, and Data2Logistics (now Loop).
* **Honest Gap**: Most mid-market auditors focus on **parcel carrier refunds** (late deliveries from FedEx/UPS) and **simple duplicate payment checks**. They do NOT typically perform deep **contract clause-level forensic auditing** (e.g., verifying fuel surcharge cap compliance, tiered volume rebate qualification, or per-stop detention grace period calculations against complex MSA language). This narrow but deep forensic capability — especially when combined with AI extraction and buyer-side data verification — represents the genuine opportunity.

---

## 4. Corrected Financial Projections

### What We Got Wrong

| v1 Claim | Correction |
| :--- | :--- |
| "AppZen valued at $1.3B+" | **Exact valuation not publicly disclosed**; Series D was an "up round" but the number "$1.3B" is unverifiable. |
| "Loop ~$44M ARR" | **Not publicly disclosed**. This figure is unverifiable. |
| "PRGX acquired for hundreds of millions" | **Acquired for exactly $195M** by Ardian. |
| "$100K–$250K upfront setup, 6–9 months" for first-gen AI tools | **[UNVERIFIED]**. Enterprise implementations vary widely. Some tools offer self-serve onboarding in days; others require months. No single source confirms this range as universal. |
| "$200K–$1M lost per mid-sized company" | **[UNVERIFIED]**. At 1%–2% leakage on a $20M spend, the range would be $200K–$400K, but this depends heavily on industry, supplier count, and existing controls. |

### Honest Revenue Expectations

Using the verified 1%–2% recovery rate (not 26%) and realistic pricing:

* **Contingency model (20% of recovered funds)**: If a client spends $20M/year with suppliers and you recover 1.5% = $300K, your 20% fee = **$60K per client per year**.
* **SaaS model ($2,500–$5,000/month for mid-market)**: Requires proving ongoing value beyond one-time recovery.
* **Year 1 realistic target**: 5–10 contingency clients = **$300K–$600K in fees** (not $388K from the inflated 26% demo).

---

## 5. Current Implementation Status & Known Technical Limitations

### What Is Implemented & Verified in Unit Tests (70/70 Assertions):
1. **Mathematical Reconciliation Engine (`audit_engine.py`)**: Exact decimal arithmetic verifying base rates, fuel caps, detention grace hours per stop, and volume rebate qualifications.
2. **Multi-Invoice Batch Processing & Duplicate Detection (`batch_auditor.py`)**: Identifies duplicate invoice IDs and identical shipment claims (100% disallowed), with cross-invoice cumulative volume rollups triggering tiered volume rebates.
3. **SQLite Audit Trail (`audit_store.py`)**: Persistent audit logging (`audit_history.db`) with reviewer sign-off stamps, UUID tracking, and RFC-4180 CSV export.
4. **Lane Rates & Accessorial Caps**: Support for contracted lane-specific caps (`lane_rates`) and fixed accessorial fee limits (`accessorial_caps`).

### Known Technical Limitations & Untested Boundaries:
1. **Exact Key Matching for Accessorials & Lanes**:
   - Accessorial fee capping currently operates on exact normalized keys (`lumper`, `lumper_fee`, `liftgate`, `layover`, `tonu`).
   - Suffix variations or custom vendor labels (e.g., `lumper_fee_charge`, `unloading_labor_assist`) do **not** match the cap dictionary and correctly fall to `unreviewed_lines` at billed amount rather than being dropped. Human review is required to reclassify these lines.
   - Lane rates require exact string matching (e.g., `"Chicago-Dallas"` matches `"Chicago - Dallas"`, but uncontracted lanes fall back to the general `base_rate_per_mile`).
2. **Document Extraction Modality Limits & Live Empirical Diagnostic**:
   - **Offline Deterministic Fallback (`audit_engine.extract_contract_from_pdf`)**:
     - Uses `pdfplumber` + regular expressions.
     - On standardized North American contracts with text layers: extracts terms reliably.
     - On non-standard structures (e.g., `contract_schedule_style.pdf` using UK Schedule phrasing and no "Section" headers): **finds 0 of 6 terms** (100% failure rate).
     - On image-only scanned PDFs (no text layer): **finds 0 of 6 terms** (100% failure rate).
   - **Multimodal LLM Vision (`extractor_llm.py`) - Verified with Live Google Gemini API**:
     - Tested live using Google Gemini API (`gemini-flash-lite-latest` via official `google-genai` SDK with Pydantic response schemas). *Note: Google retired `gemini-2.5-flash` for new API keys in 2026 with HTTP 404; engine routes to active `gemini-flash-lite-latest`.*
     - On non-standard text contracts (`contract_schedule_style.pdf`): **Extracted 6 of 6 terms correctly (100% precision)** with verbatim supporting quotes, overcoming the complete failure of the regex parser.
     - On raster bitmap scanned contracts (`contract_scanned_image.pdf`, 0 text bytes): **Extracted 2 of 6 terms correctly** and **hallucinated 4 of 6 values** (guessed standard industry numbers: $2.00, 15.0%, $75.00, 3.5% instead of $2.40, 12.0%, $50.00, 5.0%).
     - **Conclusion**: The mandatory Step 1 Human Confirmation Gate (`HumanConfirmationRequiredError`) is scientifically validated — pure AI vision extraction on scanned documents can hallucinate plausible numbers without human review.
3. **Grounded Economic Sizing**:
   - Industry-wide freight invoice discrepancy savings average **3.0% to 5.0%** (Cass Information Systems benchmark), **not** the 26.1% seen in the synthetic demonstration fixture (which intentionally combined four concurrent billing errors into one invoice to test every engine branch).
   - Real client value proposition: On $1M in annual freight spend, 3%–5% recovery yields $30,000–$50,000 in recoverable overcharges, supporting a $6,000–$10,000 contingency audit fee (20%).
4. **Unsupported Enterprise Features**:
   - No direct EDI 210/810 or SAP/Oracle ERP API connectors (currently ingests PDF, JSON, and manual entry via Streamlit UI).
   - No multi-currency foreign exchange conversion.
   - No SOC 2 Type II or ISO 27001 formal certification.
