"""Streamlit entry for the Report Builder.

Flow mirrors the reference case study (성인영양식_2603_v2.pdf):
  1. Pick campaign_no  →  fetch CampaignData
  2. Edit metrics table (04. 캠페인 성과)
  3. Generate + edit narrative (요약 / 01 / 02 / 03 / 05 인사이트)
  4. Generate hero image (AI)
  5. Build 4 artifacts: HTML · PDF · DOCX · TXT
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from ai import NARRATIVE_SECTIONS, generate_hero_image, generate_narrative
from ai.narrative import INSIGHTS_KEY, INSIGHTS_LABEL
from auth import logout, require_auth
from config import load_settings
from data import CampaignData, CampaignRepository, MetricRow
from render import (
    html_to_pdf,
    render_press_docx,
    render_press_txt,
    render_print_html,
    render_web_html,
)


def _ensure_playwright_chromium():
    """Install Chromium on first run if missing (for Streamlit Cloud).

    Local dev: no-op when ~/.cache/ms-playwright is already populated.
    Cloud first-boot: downloads Chromium (~150MB, 30~60s), then cached.
    """
    import os
    import subprocess
    cache = os.path.expanduser("~/.cache/ms-playwright")
    try:
        if os.path.isdir(cache) and any(
            d.startswith("chromium") for d in os.listdir(cache)
        ):
            return
    except Exception:
        pass
    try:
        subprocess.run(
            ["playwright", "install", "chromium"],
            check=False,
            timeout=300,
            capture_output=True,
        )
    except Exception:
        pass  # PDF will fail but the rest of the app still works


_ensure_playwright_chromium()


st.set_page_config(page_title="Report Builder", page_icon="📄", layout="wide")

# Auth gate — blocks the rest of the script with a login screen until
# a user signs in with an @{ALLOWED_DOMAIN} Google account.
user_email = require_auth()

settings = load_settings()


def _session_default(key, value):
    if key not in st.session_state:
        st.session_state[key] = value


_session_default("campaign", None)
_session_default("narrative", {k: "" for k, _ in NARRATIVE_SECTIONS} | {INSIGHTS_KEY: []})
_session_default("metrics_df", None)
_session_default("hero_path", None)
_session_default("headline", "")
_session_default("subhead", "")
_session_default("context_prose", "")


# ─────────────────────────────────────── Sidebar: campaign picker
def _reset_campaign_state(data: CampaignData):
    """Clear all per-campaign UI state so the new selection starts fresh."""
    st.session_state.campaign = data
    st.session_state.last_no = data.campaign_no
    st.session_state.metrics_df = pd.DataFrame(
        [{"indicator": m.indicator, "value": m.value, "note": m.note} for m in data.metrics_table]
    )
    st.session_state.narrative = {k: "" for k, _ in NARRATIVE_SECTIONS} | {INSIGHTS_KEY: []}
    for k, _ in NARRATIVE_SECTIONS:
        st.session_state[f"nar_{k}"] = ""
    st.session_state["nar_insights"] = ""
    st.session_state.context_prose = ""
    st.session_state.headline = ""
    st.session_state.subhead = ""
    st.session_state.hero_path = None
    if "metrics_editor" in st.session_state:
        del st.session_state["metrics_editor"]


with st.sidebar:
    # ── Logged-in user info + logout
    st.markdown(f"**👤 {user_email}**")
    if st.button("🚪 로그아웃", width="stretch"):
        logout()
    st.markdown("---")

    st.header("1. 캠페인 선택")
    repo = CampaignRepository()
    if not repo.is_available():
        st.error("Supabase 연결 실패 — SUPABASE_URL/KEY 확인")
        st.stop()
    st.caption(f"DB: {settings.supabase_url.replace('https://', '').split('.')[0]}")

    keyword = st.text_input(
        "검색 (캠페인명)",
        placeholder="예: 하이트, 맥도날드, 어드레서블",
        help="브랜드/광고주명은 campaign_name 안에 들어있어 그 텍스트 기준으로 검색됩니다. 최대 50건, 최신순.",
    )

    selected_no: str | None = None
    if keyword and keyword.strip():
        try:
            matches = repo.search(keyword.strip(), limit=50)
        except Exception as e:
            st.error(f"검색 오류: {e}")
            matches = []

        if matches:
            options = [
                f"[{m['campaign_no']}] {(m.get('campaign_name') or '?')}"
                f"  ·  {m.get('start_date') or '?'}~{m.get('end_date') or '?'}"
                for m in matches
            ]
            choice = st.selectbox(
                f"검색 결과 ({len(matches)}건)",
                options=options,
                index=None,
                placeholder="여기서 선택",
                key="campaign_picker",
            )
            if choice:
                m = re.match(r"\[(\d+)\]", choice)
                if m:
                    selected_no = m.group(1)
        else:
            st.caption("검색 결과 없음")

    if st.button("불러오기", width="stretch", disabled=selected_no is None):
        data = repo.get(selected_no) if selected_no else None
        if not data:
            st.warning("해당 campaign_no를 찾지 못했습니다.")
        else:
            _reset_campaign_state(data)
            st.success(f"로드 완료: {data.campaign_name}")


# ─────────────────────────────────────── Main
st.title("Case Study Report Builder")
st.caption("GEO용 1페이지 케이스스터디 · HTML · PDF · DOCX · TXT 동시 산출")

campaign: CampaignData | None = st.session_state.campaign
if campaign is None:
    st.info("좌측에서 캠페인을 검색·선택하고 '불러오기'를 눌러주세요.")
    st.stop()


col_l, col_r = st.columns([0.55, 0.45])

with col_l:
    st.subheader("2. 헤드라인 & 서브헤드")
    st.session_state.headline = st.text_input(
        "헤드라인",
        st.session_state.headline
        or f"{campaign.advertiser}, {campaign.campaign_name} 캠페인 사례",
        help="예시: '크로스디바이스 광고로 고객 획득 비용 53% 절감한 성인영양식 캠페인 사례'",
    )
    st.session_state.subhead = st.text_input("서브헤드 (선택)", st.session_state.subhead)

    st.subheader("3. 캠페인 컨텍스트 & 내러티브")
    st.session_state.context_prose = st.text_area(
        "캠페인 컨텍스트 (자유 서술 — Claude가 1차 사실로 사용)",
        value=st.session_state.context_prose,
        height=180,
        help=(
            "예: 'OO 캠페인은 X를 목표로 Y 오디언스를 타게팅하여 Z 방식으로 운영했고, "
            "~ 같은 성과를 거두었다.' 여기 적은 내용이 DB 데이터보다 우선합니다. "
            "비워두면 DB만 사용합니다."
        ),
    )
    if st.button("Claude로 섹션 초안 생성", type="primary"):
        with st.spinner("Claude 호출 중..."):
            try:
                result = generate_narrative(
                    campaign.to_prompt_dict(),
                    campaign_context_prose=st.session_state.context_prose,
                )
                st.session_state.narrative = result
                # Push generated values into the widget-bound keys so the
                # textareas refresh on this rerun. Without this, Streamlit
                # keeps the stale (empty) value the textarea was first
                # registered with.
                for k, _ in NARRATIVE_SECTIONS:
                    st.session_state[f"nar_{k}"] = result.get(k, "")
                st.session_state["nar_insights"] = "\n".join(
                    result.get(INSIGHTS_KEY, [])
                )
                st.success("초안 생성 완료. 아래에서 수정하세요.")
            except Exception as e:
                st.error(f"생성 실패: {e}")

    with st.expander("🔍 디버그: 현재 narrative dict"):
        st.json(st.session_state.narrative)

    # Initialize widget keys from the narrative dict on first render only.
    # After init, the widgets own their state — button handler above
    # overwrites these keys when a new draft is generated.
    for k, _ in NARRATIVE_SECTIONS:
        if f"nar_{k}" not in st.session_state:
            st.session_state[f"nar_{k}"] = st.session_state.narrative.get(k, "")
    if "nar_insights" not in st.session_state:
        st.session_state["nar_insights"] = "\n".join(
            st.session_state.narrative.get(INSIGHTS_KEY, [])
        )

    for key, label in NARRATIVE_SECTIONS:
        st.text_area(label, height=120, key=f"nar_{key}")
        st.session_state.narrative[key] = st.session_state[f"nar_{key}"]

    st.text_area(
        INSIGHTS_LABEL + " — 한 줄에 한 항목",
        height=140,
        key="nar_insights",
        help="빈 줄은 무시. 레퍼런스 기준 3항목 권장.",
    )
    st.session_state.narrative[INSIGHTS_KEY] = [
        line.strip() for line in st.session_state["nar_insights"].splitlines() if line.strip()
    ]

with col_r:
    st.subheader("4. 성과 지표 (04. 캠페인 성과)")
    st.caption("레퍼런스 포맷: 성과 지표 · 성과 · 비고 (3열)")
    base_df = st.session_state.metrics_df if st.session_state.metrics_df is not None else pd.DataFrame(
        columns=["indicator", "value", "note"]
    )
    edited = st.data_editor(
        base_df,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "indicator": st.column_config.TextColumn("성과 지표"),
            "value": st.column_config.TextColumn("성과"),
            "note": st.column_config.TextColumn("비고"),
        },
        key="metrics_editor",
    )
    st.session_state.metrics_df = edited

    st.subheader("5. 히어로 이미지")
    tab_ai, tab_upload = st.tabs(["AI 생성 (Gemini)", "직접 업로드"])
    with tab_ai:
        brief = st.text_area(
            "이미지 브리프",
            value=(
                f"{campaign.channel or 'CTV/Mobile'} 광고 케이스스터디 히어로 이미지. "
                f"브랜드: {campaign.advertiser}. 업종: {campaign.industry or ''}. "
                "담백한 에디토리얼 톤, 제품·라이프스타일 중심, 텍스트 없음."
            ),
            height=100,
            key="hero_brief",
        )
        if st.button("Gemini로 생성", key="hero_gen"):
            with st.spinner("이미지 생성 중..."):
                try:
                    path = generate_hero_image(
                        brief, filename=f"hero_{campaign.campaign_no}.png"
                    )
                    st.session_state.hero_path = str(path)
                    st.success("완료")
                except Exception as e:
                    st.error(f"생성 실패: {e}")
    with tab_upload:
        uploaded = st.file_uploader(
            "PNG/JPG 파일", type=["png", "jpg", "jpeg"], key="hero_upload"
        )
        if uploaded is not None:
            ext = uploaded.name.rsplit(".", 1)[-1].lower()
            hero_dir: Path = settings.output_dir / "hero"
            hero_dir.mkdir(parents=True, exist_ok=True)
            saved = hero_dir / f"hero_{campaign.campaign_no}_uploaded.{ext}"
            saved.write_bytes(uploaded.getvalue())
            st.session_state.hero_path = str(saved)
            st.success(f"업로드 완료: {saved.name}")
    if st.session_state.hero_path:
        st.image(st.session_state.hero_path)

    st.divider()
    st.subheader("6. 산출물 생성")
    out_dir: Path = settings.output_dir / campaign.campaign_no
    out_dir.mkdir(parents=True, exist_ok=True)

    if st.button("4개 파일 한번에 빌드", type="primary", width="stretch"):
        # Guard: narrative must be filled in. Hitting build before generating
        # results in a report with section headers but no body text.
        has_narrative = any(
            (st.session_state.narrative.get(k, "") or "").strip()
            for k, _ in NARRATIVE_SECTIONS
        )
        if not has_narrative:
            st.warning(
                "⚠️ 내러티브가 비어있습니다. 좌측 '3. 캠페인 컨텍스트 & 내러티브' 섹션에서 "
                "**[Claude로 섹션 초안 생성]** 버튼을 먼저 눌러 내용을 채워주세요. "
                "(또는 각 textarea에 직접 입력)"
            )
            st.stop()
        # materialize the edited metrics back into the campaign payload
        df = st.session_state.metrics_df
        if df is None:
            df = pd.DataFrame(columns=["indicator", "value", "note"])
        campaign.metrics_table = [
            MetricRow(
                indicator=str(r.get("indicator", "")).strip(),
                value=str(r.get("value", "")).strip(),
                note=str(r.get("note", "")).strip(),
            )
            for r in df.to_dict(orient="records")
            if str(r.get("indicator", "")).strip() and str(r.get("value", "")).strip()
        ]

        context = {
            "headline": st.session_state.headline,
            "subhead": st.session_state.subhead,
            "campaign": asdict(campaign),
            "narrative": st.session_state.narrative,
            "hero_image_url": Path(st.session_state.hero_path).as_uri()
            if st.session_state.hero_path
            else None,
            "company": {
                "name": settings.company_name,
                "url": settings.company_url,
                "url_secondary": settings.company_url_secondary,
                "logo": settings.company_logo_url,
                "description": settings.company_description,
                "press_contact_name": settings.press_contact_name,
                "press_contact_email": settings.press_contact_email,
            },
            "year": _dt.date.today().year,
        }

        web_html = render_web_html(context, out_dir / "case_study_web.html")
        print_html = render_print_html(context, out_dir / "_print.html")
        pdf = html_to_pdf(print_html, out_dir / "case_study_print.pdf")
        docx = render_press_docx(context, out_dir / "press_release.docx")
        txt = render_press_txt(context, out_dir / "press_release.txt")

        st.success(f"완료 → {out_dir}")
        for label, p in [("HTML", web_html), ("PDF", pdf), ("DOCX", docx), ("TXT", txt)]:
            with open(p, "rb") as f:
                st.download_button(
                    f"{label} 다운로드",
                    data=f.read(),
                    file_name=p.name,
                    key=f"dl_{label}",
                )
