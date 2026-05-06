"""HTML → PDF via Playwright. Chosen over weasyprint/wkhtmltopdf for
Windows stability and CSS support."""
from __future__ import annotations

from pathlib import Path


def html_to_pdf(html_path: Path, pdf_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    url = html_path.resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
            prefer_css_page_size=True,
        )
        browser.close()
    return pdf_path
