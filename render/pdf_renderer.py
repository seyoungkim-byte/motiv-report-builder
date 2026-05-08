"""HTML → PDF via Playwright. Chosen over weasyprint/wkhtmltopdf for
Windows stability and CSS support.

Cloud note: Streamlit Cloud containers have no proper sandbox kernel and
crash with `TargetClosedError` on default Chromium launch. The flags
below are the standard "Chromium in Docker" recipe — `--no-sandbox`
disables the user-namespace sandbox the container can't provide, and
`--disable-dev-shm-usage` works around the 64MB /dev/shm limit by
using /tmp instead. Both are safe for our single-purpose PDF rendering.
"""
from __future__ import annotations

from pathlib import Path


_CHROMIUM_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--single-process",
]


def html_to_pdf(html_path: Path, pdf_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    url = html_path.resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(args=_CHROMIUM_LAUNCH_ARGS)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
                prefer_css_page_size=True,
            )
        finally:
            browser.close()
    return pdf_path
