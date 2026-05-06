"""Thin wrapper around supabase-py. Matches the dashboard's convention
(reads SUPABASE_URL / SUPABASE_KEY) so both apps hit the same project."""
from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from config import load_settings


@lru_cache(maxsize=1)
def get_client() -> Client | None:
    s = load_settings()
    if not s.supabase_url or not s.supabase_key:
        return None
    return create_client(s.supabase_url, s.supabase_key)
