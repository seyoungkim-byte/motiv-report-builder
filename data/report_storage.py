"""Persist last build per campaign so the user can revisit a campaign and
still find the previously generated case study without rebuilding.

Single-row-per-campaign table `campaign_report_builds` (DDL applied
2026-05-08 via Supabase migration `campaign_report_builds`).

Files (HTML / PDF / DOCX / TXT / hero image) are stored as base64-encoded
TEXT columns. Per-row payload is small enough (~1MB max) that PG handles
it comfortably; if average grows we can move binaries to Supabase Storage.
"""
from __future__ import annotations

import base64
from typing import Any

from .supabase_client import get_client


TABLE = "campaign_report_builds"


def _b64encode(data: bytes | None) -> str | None:
    if not data:
        return None
    return base64.b64encode(data).decode("ascii")


def _b64decode(s: str | None) -> bytes | None:
    if not s:
        return None
    try:
        return base64.b64decode(s.encode("ascii"))
    except Exception:
        return None


def save_build(
    *,
    campaign_no: str,
    user_email: str | None,
    headline: str,
    subhead: str,
    context_prose: str,
    narrative: dict,
    metrics_table: list[dict],
    header_meta: dict | None,
    hero_image: bytes | None,
    html: bytes,
    pdf: bytes,
    docx: bytes,
    txt: bytes,
) -> bool:
    """Upsert the latest build for a campaign. Returns True on success."""
    client = get_client()
    if not client:
        return False
    try:
        client.table(TABLE).upsert({
            "campaign_no": campaign_no,
            "built_by": user_email,
            "headline": headline or "",
            "subhead": subhead or "",
            "context_prose": context_prose or "",
            "narrative": narrative or {},
            "metrics_table": metrics_table or [],
            "header_meta": header_meta or {},
            "hero_image_b64": _b64encode(hero_image),
            "html_b64": _b64encode(html),
            "pdf_b64":  _b64encode(pdf),
            "docx_b64": _b64encode(docx),
            "txt_b64":  _b64encode(txt),
        }, on_conflict="campaign_no").execute()
        return True
    except Exception:
        return False


def load_build(campaign_no: str) -> dict[str, Any] | None:
    """Fetch the saved build for a campaign, or None if not found.

    Returns a dict with both source state (headline/subhead/prose/narrative/
    metrics) and rendered files (4 download blobs + optional hero image bytes).
    """
    client = get_client()
    if not client:
        return None
    try:
        res = (
            client.table(TABLE)
            .select("*")
            .eq("campaign_no", campaign_no)
            .limit(1)
            .execute()
        )
    except Exception:
        return None

    rows = res.data or []
    if not rows:
        return None
    row = rows[0]

    files = []
    for label, col, fname in [
        ("HTML", "html_b64", "case_study_web.html"),
        ("PDF",  "pdf_b64",  "case_study_print.pdf"),
        ("DOCX", "docx_b64", "press_release.docx"),
        ("TXT",  "txt_b64",  "press_release.txt"),
    ]:
        b = _b64decode(row.get(col))
        if b:
            files.append((label, b, fname))

    return {
        "campaign_no": row.get("campaign_no"),
        "built_at": row.get("built_at"),
        "built_by": row.get("built_by"),
        "headline": row.get("headline") or "",
        "subhead": row.get("subhead") or "",
        "context_prose": row.get("context_prose") or "",
        "narrative": row.get("narrative") or {},
        "metrics_table": row.get("metrics_table") or [],
        "header_meta": row.get("header_meta") or {},
        "hero_image": _b64decode(row.get("hero_image_b64")),
        "files": files,
    }
