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
import math
from typing import Any

from .supabase_client import get_client


def _clean_for_json(obj: Any) -> Any:
    """NaN / Infinity 를 None 으로 치환. dict / list 재귀 처리.
    pandas DataFrame.to_dict() 가 빈 셀을 float('nan') 로 채우는데, 그게
    그대로 supabase-py 로 가면 ValueError 'Out of range float values are
    not JSON compliant'. 모든 페이로드를 보내기 직전에 한 번 청소."""
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_for_json(x) for x in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


TABLE = "campaign_report_builds"


# 가장 최근 save_build / load_build 실패의 raw error message 를 보관.
# UI 측에서 사용자에게 노출용 — silent fail 디버깅 불가능 문제 해결.
_LAST_ERROR: str | None = None


def last_storage_error() -> str | None:
    return _LAST_ERROR


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
    extra_analysis: str,
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
    global _LAST_ERROR
    client = get_client()
    if not client:
        _LAST_ERROR = "Supabase 클라이언트 미초기화 (SUPABASE_URL / SUPABASE_KEY 확인)"
        return False
    try:
        payload = _clean_for_json({
            "campaign_no": campaign_no,
            "built_by": user_email,
            "headline": headline or "",
            "subhead": subhead or "",
            "context_prose": context_prose or "",
            "extra_analysis": extra_analysis or "",
            "narrative": narrative or {},
            "metrics_table": metrics_table or [],
            "header_meta": header_meta or {},
        })
        # binary payloads — base64 텍스트는 NaN 없음, 별도 추가.
        payload["hero_image_b64"] = _b64encode(hero_image)
        payload["html_b64"] = _b64encode(html)
        payload["pdf_b64"]  = _b64encode(pdf)
        payload["docx_b64"] = _b64encode(docx)
        payload["txt_b64"]  = _b64encode(txt)
        client.table(TABLE).upsert(payload, on_conflict="campaign_no").execute()
        _LAST_ERROR = None
        return True
    except Exception as e:
        _LAST_ERROR = f"{type(e).__name__}: {e}"
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
        "extra_analysis": row.get("extra_analysis") or "",
        "narrative": row.get("narrative") or {},
        "metrics_table": row.get("metrics_table") or [],
        "header_meta": row.get("header_meta") or {},
        "hero_image": _b64decode(row.get("hero_image_b64")),
        "files": files,
    }
