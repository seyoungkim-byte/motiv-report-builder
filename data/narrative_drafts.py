"""In-progress draft per campaign — survives JWT refresh / container restart.

The user hits '💾 임시 저장' while authoring; we upsert every editable
field here. On campaign reload, if a draft exists and is newer than the
saved build (or no build exists), the app offers a restore banner.
A successful build clears the draft so we never restore stale text.
"""
from __future__ import annotations

import math
from typing import Any

from .supabase_client import get_client


TABLE = "narrative_drafts"


def _clean_for_json(obj: Any) -> Any:
    """NaN / Infinity → None. Same shape as report_storage; duplicated here so
    drafts don't depend on the other module's internals."""
    if isinstance(obj, dict):
        return {k: _clean_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_for_json(x) for x in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def save_draft(
    *,
    campaign_no: str,
    headline: str,
    subhead: str,
    context_prose: str,
    extra_analysis: str,
    narrative: dict,
    metrics_table: list[dict],
    hdr_media_products: str,
    hdr_measurement: str,
    hdr_tags_raw: str,
    hdr_cumulative_period: str,
    updated_by: str | None,
) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        payload = _clean_for_json({
            "campaign_no": campaign_no,
            "headline": headline or "",
            "subhead": subhead or "",
            "context_prose": context_prose or "",
            "extra_analysis": extra_analysis or "",
            "narrative": narrative or {},
            "metrics_table": metrics_table or [],
            "hdr_media_products": hdr_media_products or "",
            "hdr_measurement": hdr_measurement or "",
            "hdr_tags_raw": hdr_tags_raw or "",
            "hdr_cumulative_period": hdr_cumulative_period or "",
            "updated_by": updated_by,
        })
        client.table(TABLE).upsert(payload, on_conflict="campaign_no").execute()
        return True
    except Exception:
        return False


def load_draft(campaign_no: str) -> dict[str, Any] | None:
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
    return rows[0] if rows else None


def delete_draft(campaign_no: str) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        client.table(TABLE).delete().eq("campaign_no", campaign_no).execute()
        return True
    except Exception:
        return False
