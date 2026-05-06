"""Central config loader. Reads Streamlit secrets first, falls back to env / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"


DEFAULT_COMPANY_DESCRIPTION = (
    "모티브인텔리전스는 CTV 광고와 크로스디바이스 타겟팅 기술을 기반으로, "
    "CTV·모바일 광고 집행부터 전환 및 구매 성과 분석까지 지원하는 애드테크 기업입니다."
)


def _secret(key: str, default: str = "") -> str:
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.getenv(key, default)


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_key: str
    anthropic_api_key: str
    anthropic_text_model: str
    gemini_api_key: str
    gemini_image_model: str
    output_dir: Path
    company_name: str
    company_url: str
    company_url_secondary: str
    company_logo_url: str
    company_description: str
    press_contact_name: str
    press_contact_email: str
    app_url: str
    allowed_domain: str


def load_settings() -> Settings:
    output_dir = Path(_secret("OUTPUT_DIR", str(ROOT / "output"))).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        supabase_url=_secret("SUPABASE_URL"),
        supabase_key=_secret("SUPABASE_KEY"),
        anthropic_api_key=_secret("ANTHROPIC_API_KEY"),
        anthropic_text_model=_secret("ANTHROPIC_TEXT_MODEL", "claude-sonnet-4-6"),
        gemini_api_key=_secret("GEMINI_API_KEY"),
        gemini_image_model=_secret("GEMINI_IMAGE_MODEL", "imagen-3.0-generate-002"),
        output_dir=output_dir,
        company_name=_secret("COMPANY_NAME", "Motiv Intelligence"),
        company_url=_secret("COMPANY_URL", "https://www.motiv-i.com"),
        company_url_secondary=_secret("COMPANY_URL_SECONDARY", "https://www.crosstarget.co.kr"),
        company_logo_url=_secret("COMPANY_LOGO_URL"),
        company_description=_secret("COMPANY_DESCRIPTION", DEFAULT_COMPANY_DESCRIPTION),
        press_contact_name=_secret("PRESS_CONTACT_NAME"),
        press_contact_email=_secret("PRESS_CONTACT_EMAIL", "crosstarget@motiv-i.com"),
        app_url=_secret("APP_URL", "http://localhost:8501"),
        allowed_domain=_secret("ALLOWED_DOMAIN", "motiv-i.com"),
    )
