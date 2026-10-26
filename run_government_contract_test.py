import os
import json
from audit_engine import extract_contract_from_pdf as regex_extract_contract
import extractor_llm

pdf_path = os.path.join(
    os.path.dirname(__file__),
    "sample_data",
    "real_government_contract_iowa_ma005.pdf"
)

print("=" * 80)
print("REAL GOVERNMENT CONTRACT EXTRACTION EVALUATION")
print("Document: State of Iowa Master Agreement (MA 005 25356)")
print("File:", pdf_path)
print("=" * 80)

# 1. Deterministic Fallback Parser (Regex)
print("\n[1] DETERMINISTIC PARSER (audit_engine.extract_contract_from_pdf):")
res_regex = regex_extract_contract(pdf_path)
print(f"Extraction Method: {res_regex.extraction_method}")
print(f"Extraction Warning: {res_regex.extraction_warning}")
print(f"Human Confirmed: {res_regex.human_confirmed}")
print("\nExtracted Fields:")
for f in res_regex.fields:
    if f.field_name == "clauses":
        print(f"  - {f.field_name}:")
        for k, v in f.value.items():
            print(f"      {k}: {v}")
    else:
        print(f"  - {f.field_name:35s}: value={f.value!r:20s} | quote={f.quoted_text!r} | page={f.page_number} | conf={f.confidence}")

# 2. Multimodal LLM Module (Gemini)
print("\n" + "=" * 80)
print("[2] MULTIMODAL LLM MODULE (extractor_llm.extract_contract):")
res_llm = extractor_llm.extract_contract(pdf_path)
print(f"Extraction Method: {res_llm.extraction_method}")
print(f"Extraction Warning: {res_llm.extraction_warning}")
print(f"Human Confirmed: {res_llm.human_confirmed}")
print("\nExtracted Fields:")
for f in res_llm.fields:
    if f.field_name == "clauses":
        print(f"  - {f.field_name}:")
        for k, v in f.value.items():
            print(f"      {k}: {v}")
    else:
        print(f"  - {f.field_name:35s}: value={f.value!r:20s} | quote={f.quoted_text!r} | page={f.page_number} | conf={f.confidence}")

print("\n" + "=" * 80)
