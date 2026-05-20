"""Global app-wide settings stored in Supabase `app_settings` (single-record per key).

Use this for prefs that should persist across sessions, devices, and users —
e.g. the narrative tone guide that the marketing team aligns on once and
reuses across every case study.
"""
from __future__ import annotations

from .supabase_client import get_client


def get_setting(key: str, default: str = "") -> str:
    """Return the value for `key`, or `default` if the row is missing or unreachable."""
    client = get_client()
    if not client:
        return default
    try:
        res = (
            client.table("app_settings")
            .select("value")
            .eq("key", key)
            .single()
            .execute()
        )
        return (res.data or {}).get("value", default) or default
    except Exception:
        return default


def set_setting(key: str, value: str) -> bool:
    """Upsert (key, value). Returns True on success."""
    client = get_client()
    if not client:
        return False
    try:
        client.table("app_settings").upsert(
            {"key": key, "value": value or ""},
            on_conflict="key",
        ).execute()
        return True
    except Exception:
        return False
