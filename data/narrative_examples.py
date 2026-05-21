"""User-curated narrative examples used as few-shot anchors in the prompt.

Whenever the user is happy with a build's narrative tone, they hit the
'🎯 톤 레퍼런스로 저장' button next to the download buttons. That writes
the full narrative dict here. On the next `generate_narrative()` call,
the top-N most recent rows are injected as `### 예시 1` ... `### 예시 N`
blocks into the system prompt, so Claude mimics the team's preferred
voice.
"""
from __future__ import annotations

from typing import Any

from .supabase_client import get_client


TABLE = "narrative_examples"


def save_example(
    *,
    campaign_no: str,
    label: str,
    narrative: dict[str, Any],
    saved_by: str | None,
) -> int | None:
    """Insert a new example row. Returns the new id, or None on failure."""
    client = get_client()
    if not client:
        return None
    try:
        res = (
            client.table(TABLE)
            .insert({
                "campaign_no": campaign_no,
                "label": (label or "").strip(),
                "narrative": narrative or {},
                "saved_by": saved_by,
            })
            .execute()
        )
        rows = res.data or []
        return rows[0]["id"] if rows else None
    except Exception:
        return None


def list_examples(limit: int | None = None) -> list[dict[str, Any]]:
    """Return saved examples newest-first."""
    client = get_client()
    if not client:
        return []
    try:
        q = client.table(TABLE).select("*").order("saved_at", desc=True)
        if limit and limit > 0:
            q = q.limit(limit)
        res = q.execute()
        return res.data or []
    except Exception:
        return []


def delete_example(row_id: int) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        client.table(TABLE).delete().eq("id", row_id).execute()
        return True
    except Exception:
        return False
