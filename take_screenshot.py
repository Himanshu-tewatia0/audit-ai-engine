import os
import time
from playwright.sync_api import sync_playwright

artifact_dir = r"C:\Users\Himanshu Tewatia\.gemini\antigravity\brain\ecb37382-419f-4bae-8e1e-cb2d70859857"
os.makedirs(artifact_dir, exist_ok=True)
screenshot_path = os.path.join(artifact_dir, "dashboard_gemini_vision_mode.png")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1450, "height": 980})
    print("Navigating to http://localhost:8501...", flush=True)
    page.goto("http://localhost:8501", wait_until="networkidle")
    time.sleep(2)

    # Click the preset button "Load Standard (INV-9042)"
    btn = page.get_by_role("button", name="Load Standard (INV-9042)")
    if btn.count() > 0:
        print("Clicking 'Load Standard (INV-9042)' button...", flush=True)
        btn.click()
        # Wait for Streamlit to process and re-run LLM extraction
        time.sleep(8)
        page.wait_for_load_state("networkidle")
    else:
        print("Button 'Load Standard (INV-9042)' not found directly, looking for buttons...", flush=True)

    # Capture the loaded state screenshot
    page.screenshot(path=screenshot_path, full_page=False)
    print(f"Screenshot successfully captured to: {screenshot_path}", flush=True)

    # Print relevant text from the page to verify
    text = page.inner_text("body")
    for line in text.split("\n"):
        if any(term in line for term in ["Extraction Mode", "Live Gemini API Key", "Document Intelligence"]):
            print("  PAGE VERIFICATION ->", line)

    browser.close()
