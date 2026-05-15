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

from ai import (
    NARRATIVE_SECTIONS,
    generate_hero_image,
    generate_narrative,
    plan_charts,
    plan_chart_candidates,
)
from ai.narrative import (
    BULLET_SECTIONS,
    INSIGHTS_KEY,
    INSIGHTS_LABEL,
    TLDR_KEY,
    TLDR_LABEL,
)
from viz import render_chart, TEMPLATE_NAMES
from auth import logout, require_auth
from config import load_settings
from data import CampaignData, CampaignRepository, MetricRow, load_build, save_build
from render import (
    html_to_pdf,
    render_press_docx,
    render_press_txt,
    render_print_html,
    render_web_html,
)


_CHROMIUM_INSTALLED = False


def _find_chromium_binary() -> str | None:
    """Look for the actual Chromium executable, not just the cache dir.

    `playwright install` can leave a partially-populated chromium-* dir
    even when the binary failed to download — the dir-only check is too
    optimistic and skips a needed reinstall.
    """
    import os
    cache = os.path.expanduser("~/.cache/ms-playwright")
    if not os.path.isdir(cache):
        return None
    try:
        candidates = sorted(
            d for d in os.listdir(cache) if d.startswith("chromium")
        )
    except OSError:
        return None
    for d in candidates:
        for tail in ("chrome-linux/chrome", "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
                     "chrome-win/chrome.exe"):
            p = os.path.join(cache, d, tail)
            if os.path.isfile(p):
                return p
    return None


def _ensure_playwright_chromium():
    """Install Chromium on first run if the actual binary is missing.

    Local dev: no-op when chrome-linux/chrome already exists.
    Cloud first-boot or rebuild: downloads Chromium (~150MB, 30~60s).
    Idempotent within a process via _CHROMIUM_INSTALLED flag.
    """
    global _CHROMIUM_INSTALLED
    if _CHROMIUM_INSTALLED:
        return
    if _find_chromium_binary():
        _CHROMIUM_INSTALLED = True
        return
    import subprocess
    try:
        subprocess.run(
            ["playwright", "install", "chromium"],
            check=False,
            timeout=600,
            capture_output=True,
        )
    except Exception:
        pass
    if _find_chromium_binary():
        _CHROMIUM_INSTALLED = True


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
_session_default(
    "narrative",
    {k: "" for k, _ in NARRATIVE_SECTIONS} | {INSIGHTS_KEY: [], TLDR_KEY: []},
)
_session_default("metrics_df", None)
_session_default("hero_path", None)
_session_default("headline", "")
_session_default("subhead", "")
_session_default("context_prose", "")
_session_default("extra_analysis", "")
# Header meta — user-supplied fields not in DB (집행 상품 / 구매 측정 / 태그 / 누적 기간)
_session_default("hdr_media_products", "")
_session_default("hdr_measurement", "")
_session_default("hdr_tags_raw", "")            # 한 줄에 한 태그
_session_default("hdr_cumulative_period", "")


# ─────────────────────────────────────── Sidebar: campaign picker
def _reset_campaign_state(data: CampaignData):
    """Clear all per-campaign UI state so the new selection starts fresh."""
    st.session_state.campaign = data
    st.session_state.last_no = data.campaign_no
    st.session_state.metrics_df = pd.DataFrame(
        [{"indicator": m.indicator, "value": m.value, "note": m.note} for m in data.metrics_table]
    )
    st.session_state.narrative = (
        {k: "" for k, _ in NARRATIVE_SECTIONS} | {INSIGHTS_KEY: [], TLDR_KEY: []}
    )
    for k, _ in NARRATIVE_SECTIONS:
        st.session_state[f"nar_{k}"] = ""
    st.session_state["nar_insights"] = ""
    st.session_state["nar_tldr"] = ""
    st.session_state.context_prose = ""
    st.session_state.extra_analysis = ""
    st.session_state.headline = ""
    st.session_state.subhead = ""
    st.session_state.hero_path = None
    st.session_state.last_build = None
    # 차트 후보 / 선택도 캠페인마다 리셋 — 옛 후보가 새 캠페인에 섞이면 사고
    st.session_state.chart_candidates = []
    st.session_state.chart_selected = set()
    st.session_state.chart_instruction = ""
    # Header 메타도 캠페인 단위로 리셋
    st.session_state.hdr_media_products = ""
    st.session_state.hdr_measurement = ""
    st.session_state.hdr_tags_raw = ""
    st.session_state.hdr_cumulative_period = ""
    if "metrics_editor" in st.session_state:
        del st.session_state["metrics_editor"]

    # 같은 캠페인의 이전 빌드가 Supabase 에 있으면 모두 복원 (소스 + 산출물).
    saved = load_build(data.campaign_no)
    if not saved:
        return

    src = saved
    st.session_state.headline = src.get("headline") or st.session_state.headline
    st.session_state.subhead = src.get("subhead") or ""
    st.session_state.context_prose = src.get("context_prose") or ""
    st.session_state.extra_analysis = src.get("extra_analysis") or ""
    nar = src.get("narrative") or {}
    if nar:
        # Backfill missing keys so older builds don't blow up the widgets.
        # Older builds had overview/background/strategy as strings; the new
        # schema is list[str]. The textarea always wants a string so we
        # join-on-newline for bullet sections at load time.
        nar.setdefault(TLDR_KEY, [])
        nar.setdefault(INSIGHTS_KEY, [])
        st.session_state.narrative = nar
        for k, _ in NARRATIVE_SECTIONS:
            v = nar.get(k, "")
            if k in BULLET_SECTIONS and isinstance(v, list):
                st.session_state[f"nar_{k}"] = "\n".join(v)
            else:
                st.session_state[f"nar_{k}"] = v if isinstance(v, str) else ""
        st.session_state["nar_insights"] = "\n".join(nar.get(INSIGHTS_KEY, []))
        st.session_state["nar_tldr"] = "\n".join(nar.get(TLDR_KEY, []))

    # metrics_table 은 saved build 에서 복원하지 않음 — 카탈로그가 캐논이라
    # 옛 라벨/값이 그대로 살아남으면 정합성 깨짐. 위에서 이미 fresh
    # catalog 기반으로 metrics_df 가 채워졌음.

    # Header 메타 복원 (옛 빌드는 header_meta 없을 수 있음 — setdefault 처리)
    hm = src.get("header_meta") or {}
    if isinstance(hm, dict):
        st.session_state.hdr_media_products    = hm.get("media_products", "")
        st.session_state.hdr_measurement       = hm.get("measurement_source", "")
        st.session_state.hdr_cumulative_period = hm.get("cumulative_period", "")
        tags = hm.get("tags") or []
        if isinstance(tags, list):
            st.session_state.hdr_tags_raw = "\n".join(str(t) for t in tags if t)

    # 히어로 이미지 — 디스크에 다시 써서 기존 path-기반 UI 가 그대로 동작
    hero_bytes = src.get("hero_image")
    if hero_bytes:
        hero_dir: Path = settings.output_dir / "hero"
        hero_dir.mkdir(parents=True, exist_ok=True)
        hero_path = hero_dir / f"hero_{data.campaign_no}_restored.png"
        try:
            hero_path.write_bytes(hero_bytes)
            st.session_state.hero_path = str(hero_path)
        except Exception:
            pass

    # 4 산출물 다운로드 블롭
    if src.get("files"):
        st.session_state.last_build = {
            "campaign_no": data.campaign_no,
            "out_dir": "(saved)",
            "files": src["files"],
            "built_at": src.get("built_at"),
        }


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
    # value= 와 session_state 재할당 패턴은 위젯 내부 상태와 충돌하기 쉬움
    # (입력값이 한 rerun 늦거나 revert 되는 현상). key= 만으로 묶어 Streamlit
    # 이 상태를 직접 소유하게 함. 기본값은 _reset_campaign_state 에서 설정.
    if not st.session_state.get("headline"):
        # 자동 헤드라인은 항상 마스킹 라벨 사용 — 실제 광고주/캠페인명이
        # 리포트 산출물에 그대로 노출되지 않도록 한다. 사용자가 수동으로
        # 헤드라인을 채우면 그 값이 우선 (수동 입력은 검열 대상 아님).
        st.session_state["headline"] = (
            f"{campaign.masked_advertiser} 캠페인 사례"
        )
    st.text_input(
        "헤드라인",
        key="headline",
        help="예시: '크로스디바이스 광고로 고객 획득 비용 53% 절감한 성인영양식 캠페인 사례'",
    )
    st.text_input("서브헤드 (선택)", key="subhead")

    # ── 2-B. 캠페인 운영 개요 (헤더 메타) ──────────────────
    # DB 에 있는 값(광고주·기간·번호) 은 자동 prefill, DB 에 없는 값(상품·측정·태그)
    # 은 사용자가 직접 입력. reference 디자인의 헤더 meta 행을 1단 노트 톤에 흡수.
    with st.expander("📋 캠페인 운영 개요 (헤더 메타 정보)", expanded=False):
        st.caption(
            f"자동 채움 — 광고주: **{campaign.masked_advertiser}**  ·  "
            f"기간: **{campaign.period_start or '?'} ~ {campaign.period_end or '?'}**  ·  "
            f"No. **{campaign.campaign_no}**"
        )
        st.text_input(
            "집행 상품 (선택)",
            key="hdr_media_products",
            placeholder="예: CrossTarget TV (CTV) + CrossTarget DA (모바일)",
            help="reference 의 '집행 상품' 자리. 비워두면 헤더에 표시 안 됨.",
        )
        st.text_input(
            "구매·전환 측정 데이터 (선택)",
            key="hdr_measurement",
            placeholder="예: 롯데멤버스 DMP 실결제 데이터",
            help="데이터 소스 명시 — GEO 신뢰 신호 + 사람이 보기에 권위 ↑",
        )
        st.text_input(
            "전체 누적 기간 (선택)",
            key="hdr_cumulative_period",
            placeholder="예: 전체 누적 2025.11 ~",
            help="이번 보고 기간 외 누적 집행 시작일이 있을 때만 입력.",
        )
        st.text_area(
            "채널·특징 태그 (선택, 한 줄에 한 태그)",
            key="hdr_tags_raw",
            height=90,
            placeholder=(
                "CTV 광고\nDA 모바일 광고\nDMP 구매 데이터 측정\n경쟁사 고객 브랜드 전환"
            ),
            help="헤더 하단의 작은 칩으로 노출됩니다. 4~5개 권장.",
        )

    st.subheader("3. 캠페인 컨텍스트 & 내러티브")
    st.text_area(
        "캠페인 컨텍스트 (자유 서술 — Claude가 1차 사실로 사용)",
        key="context_prose",
        height=160,
        help=(
            "예: 'OO 캠페인은 X를 목표로 Y 오디언스를 타게팅하여 Z 방식으로 운영했고, "
            "~ 같은 성과를 거두었다.' 여기 적은 내용이 DB 데이터보다 우선합니다. "
            "비워두면 DB만 사용합니다."
        ),
    )

    # ── 추가 분석 데이터 — DB 외 보조 분석 자료 (시장 점유율/경쟁사 비교 등) ──
    with st.expander("📊 추가 분석 데이터 (선택) — 표·수치 수기 입력", expanded=False):
        st.caption(
            "DB 에 없는 분석 자료 (시장 점유율, 경쟁사 비교, 외부 벤치마크 등) 를 "
            "여기 붙여넣으면 **내러티브 + 차트 추천** 양쪽에 1차 사실로 반영됩니다. "
            "CSV / 표 / 줄글 자유. 실 브랜드명은 자동 익명화됩니다."
        )
        st.text_area(
            "추가 분석",
            key="extra_analysis",
            height=180,
            placeholder=(
                "예시:\n"
                "## 시장 점유율 변화 (구매 건수 기준, 1월 → 2월)\n"
                "브랜드,1월,2월\n"
                "마즈,38.6,24.7\n"
                "페레로,28.4,30.9\n"
                "허쉬,13.8,23.7\n"
                "린트,5.6,8.3\n"
                "→ 핵심: 린트 M/S +46.8% 증가율 2위 (페레로 +8.9% 대비 5배 이상)"
            ),
            label_visibility="collapsed",
        )

    if st.button("Claude로 섹션 초안 생성", type="primary"):
        with st.spinner("Claude 호출 중..."):
            try:
                result = generate_narrative(
                    campaign.to_prompt_dict(),
                    campaign_context_prose=st.session_state.context_prose,
                    extra_analysis=st.session_state.extra_analysis,
                )
                st.session_state.narrative = result
                # Push generated values into the widget-bound keys so the
                # textareas refresh on this rerun. Without this, Streamlit
                # keeps the stale (empty) value the textarea was first
                # registered with.
                for k, _ in NARRATIVE_SECTIONS:
                    val = result.get(k, "")
                    if k in BULLET_SECTIONS and isinstance(val, list):
                        st.session_state[f"nar_{k}"] = "\n".join(val)
                    else:
                        st.session_state[f"nar_{k}"] = val if isinstance(val, str) else ""
                st.session_state["nar_insights"] = "\n".join(
                    result.get(INSIGHTS_KEY, [])
                )
                st.session_state["nar_tldr"] = "\n".join(
                    result.get(TLDR_KEY, [])
                )
                st.success("초안 생성 완료. 아래에서 수정하세요.")
            except Exception as e:
                st.error(f"생성 실패: {e}")

    with st.expander("🔍 디버그: 현재 narrative dict"):
        st.json(st.session_state.narrative)

    # Initialize widget keys from the narrative dict on first render only.
    # After init, the widgets own their state — button handler above
    # overwrites these keys when a new draft is generated. Bullet sections
    # are joined to "one item per line" for the textarea.
    for k, _ in NARRATIVE_SECTIONS:
        if f"nar_{k}" not in st.session_state:
            val = st.session_state.narrative.get(k, "")
            if k in BULLET_SECTIONS and isinstance(val, list):
                st.session_state[f"nar_{k}"] = "\n".join(val)
            else:
                st.session_state[f"nar_{k}"] = val if isinstance(val, str) else ""
    if "nar_insights" not in st.session_state:
        st.session_state["nar_insights"] = "\n".join(
            st.session_state.narrative.get(INSIGHTS_KEY, [])
        )
    if "nar_tldr" not in st.session_state:
        st.session_state["nar_tldr"] = "\n".join(
            st.session_state.narrative.get(TLDR_KEY, [])
        )

    # TL;DR sits at the top — it's the eyebrow line under the headline,
    # shown as three side-by-side chips in print/web.
    st.text_area(
        TLDR_LABEL + " — 한 줄에 한 항목, 30~45자 단문 3개",
        height=90,
        key="nar_tldr",
        help="헤더 옆 3분할 박스에 들어갑니다. 핵심 사실 1개씩.",
    )
    st.session_state.narrative[TLDR_KEY] = [
        line.strip() for line in st.session_state["nar_tldr"].splitlines() if line.strip()
    ][:3]

    for key, label in NARRATIVE_SECTIONS:
        if key in BULLET_SECTIONS:
            st.text_area(
                f"{label} — 한 줄에 한 불릿 (35~65자 단문)",
                height=110,
                key=f"nar_{key}",
                help="만연체 금지. 단문 사실 1개씩.",
            )
            st.session_state.narrative[key] = [
                line.strip()
                for line in st.session_state[f"nar_{key}"].splitlines()
                if line.strip()
            ][:5]
        else:
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
    st.caption(
        "레퍼런스 포맷: 성과 지표 · 성과 · 비고 (3열). "
        "좌측 ☑ 체크 후 ▲▼ 로 행 이동."
    )
    base_df = st.session_state.metrics_df if st.session_state.metrics_df is not None else pd.DataFrame(
        columns=["indicator", "value", "note"]
    )
    # 행 이동용 선택 컬럼 보장
    if "_select" not in base_df.columns:
        base_df = base_df.assign(_select=False)
    # data_editor 에 들어가기 직전에 항상 첫 컬럼이 _select 가 되도록 정렬
    edited = st.data_editor(
        base_df,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "_select":   st.column_config.CheckboxColumn("↕", width="small",
                            help="체크 후 아래 ▲▼ 로 행 이동"),
            "indicator": st.column_config.TextColumn("성과 지표"),
            "value":     st.column_config.TextColumn("성과"),
            "note":      st.column_config.TextColumn("비고"),
        },
        column_order=["_select", "indicator", "value", "note"],
        key="metrics_editor",
    )
    st.session_state.metrics_df = edited

    # ── 행 이동 컨트롤 ──────────────────────
    def _shift_metric_row(direction: int):
        """direction=-1 (위) / +1 (아래). 체크된 첫 행만 이동."""
        df_now = st.session_state.metrics_df
        if df_now is None or df_now.empty or "_select" not in df_now.columns:
            return
        sel = df_now.index[df_now["_select"] == True].tolist()
        if not sel:
            return
        i = sel[0]
        j = i + direction
        if j < 0 or j >= len(df_now):
            return
        new_idx = df_now.index.tolist()
        new_idx[i], new_idx[j] = new_idx[j], new_idx[i]
        new_df = df_now.loc[new_idx].reset_index(drop=True)
        # 이동 후 체크박스는 새 위치의 행에 유지
        new_df["_select"] = False
        new_df.at[j, "_select"] = True
        st.session_state.metrics_df = new_df
        # data_editor 의 내부 상태 리셋해야 새 순서 반영됨
        if "metrics_editor" in st.session_state:
            del st.session_state["metrics_editor"]
        st.rerun()

    bcols = st.columns([0.18, 0.18, 0.64])
    if bcols[0].button("▲ 위로", width="stretch", key="metric_up"):
        _shift_metric_row(-1)
    if bcols[1].button("▼ 아래로", width="stretch", key="metric_down"):
        _shift_metric_row(+1)

    st.subheader("5. 히어로 이미지")
    tab_ai, tab_upload = st.tabs(["AI 생성 (Gemini)", "직접 업로드"])
    with tab_ai:
        brief = st.text_area(
            "이미지 브리프",
            value=(
                f"{campaign.channel or 'CTV/Mobile'} 광고 케이스스터디 히어로 이미지. "
                f"업종: {campaign.industry or '광고 일반'}. "
                "담백한 에디토리얼 톤, 라이프스타일 중심, 텍스트·로고·실제 브랜드 노출 없음."
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
    st.subheader("6. 차트 큐레이션 (선택)")
    st.caption(
        "비워두면 빌드 시 AI 가 자동으로 0~2개 선택. 직접 고르려면 아래에서 "
        "후보를 받아 체크하세요."
    )
    _instr = st.text_area(
        "Claude 에게 추가 지시 (선택)",
        key="chart_instruction",
        height=80,
        placeholder=(
            "예: '구매 성장률은 vertical_pair 로 보여줘'\n"
            "예: 'donut 은 빼고 funnel 위주로'\n"
            "예: 'index_lift 1개만 추천'"
        ),
        help="비워두면 일반 후보 추천. 입력하면 Claude 가 이 지시를 따릅니다.",
    )
    if st.button("🎨 차트 후보 받기 (4~5개)", key="chart_candidates_btn"):
        with st.spinner("후보 큐레이션 + 미리보기 렌더 중..."):
            dbg: dict = {}
            cands = plan_chart_candidates(
                campaign.to_prompt_dict(),
                st.session_state.narrative,
                campaign_context_prose=st.session_state.context_prose,
                extra_analysis=st.session_state.extra_analysis,
                user_instruction=_instr,
                debug=dbg,
            )
            previews: list[dict] = []
            for spec in cands:
                try:
                    spec["image_b64"] = render_chart(spec["template"], spec["data"])
                    previews.append(spec)
                except Exception as e:
                    st.warning(f"후보 '{spec.get('title')}' 렌더 실패: {e}")
            st.session_state.chart_candidates = previews
            # 기본 선택: 상위 2개 자동 체크 (사용자가 원하면 3개까지 수동 가능)
            st.session_state.chart_selected = set(range(min(2, len(previews))))
            if not previews:
                st.warning(
                    f"후보 0개 — 사유: {dbg.get('reason','?')} / {dbg.get('detail','')}"
                )
                with st.expander("🔍 원응답 (앞 800자)"):
                    st.code(dbg.get("raw", "(없음)"))

    # 후보 그리드 — 체크박스 + 프리뷰 + 메타
    cands = st.session_state.get("chart_candidates") or []
    if cands:
        st.caption(f"총 {len(cands)}개 후보. 0~2개 체크해서 빌드에 포함.")
        selected: set = st.session_state.get("chart_selected", set())
        # 최대 2개 강제
        for i, c in enumerate(cands):
            cols = st.columns([0.08, 0.92])
            checked_now = cols[0].checkbox(
                "", value=(i in selected), key=f"cand_{i}",
                label_visibility="collapsed",
            )
            if checked_now:
                selected.add(i)
            else:
                selected.discard(i)
            with cols[1]:
                st.markdown(
                    f"**{c.get('title','(제목 없음)')}**  "
                    f"_<span style='color:#7d8c4e'>{c.get('template','')}</span>_",
                    unsafe_allow_html=True,
                )
                if c.get("image_b64"):
                    import base64
                    st.image(base64.b64decode(c["image_b64"]), width=420)
                if c.get("caption"):
                    st.caption(c["caption"])
                st.markdown("---")
        # 3개 캡 강제 — 4개 이상 체크 시 나중 것 cut
        if len(selected) > 3:
            selected = set(sorted(selected)[:3])
            st.warning("⚠️ 최대 3개까지만 빌드에 포함됩니다. 나머지는 자동 해제됨.")
        st.session_state.chart_selected = selected
        if selected:
            note = (
                f"✅ {len(selected)}/3 개 선택됨"
                + (" (3개 시 가로 3열로 압축됨)" if len(selected) >= 3 else "")
            )
            st.success(note)
        else:
            st.info("체크된 후보가 없습니다 — 빌드 시 AI 자동 픽으로 폴백.")

    st.divider()
    st.subheader("7. 산출물 생성")
    out_dir: Path = settings.output_dir / campaign.campaign_no
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1페이지 충만도 사전 체크 ─────────────────────────
    # PDF 빌드 후 잘림을 막기 위해 빌드 직전에 길이 휴리스틱으로 위험 신호를
    # 노출. A4 가용 ~278mm 중 헤더/푸터/KPI/2단grid 가 ~190mm 차지하니
    # 본문 + 인사이트 + 차트가 들어갈 여유는 약 88mm. 거기 기준으로 추정.
    def _estimate_overflow() -> tuple[int, list[str], int]:
        """returns (risk_score, warnings, est_fill_percent 0~150+)"""
        nar = st.session_state.narrative or {}
        warns: list[str] = []
        score = 0

        def n(x):  # str length or sum of list-str lengths
            if isinstance(x, list):
                return sum(len(str(s)) for s in x)
            return len(str(x or ""))

        summary_chars = n(nar.get("summary"))
        if summary_chars > 200:
            warns.append(f"요약 {summary_chars}자 → 150자 이하 권장")
            score += 1

        ov = n(nar.get("overview"))
        if ov > 200:
            warns.append(f"01 캠페인 개요 {ov}자 → 불릿 더 짧게 (총 ≤170자)")
            score += 1
        bg = n(nar.get("background"))
        if bg > 200:
            warns.append(f"02 광고 집행 배경 {bg}자 → 불릿 더 짧게 (총 ≤170자)")
            score += 1
        st_chars = n(nar.get("strategy"))
        if st_chars > 250:
            warns.append(f"03 적용 전략 {st_chars}자 → 200자 이하 권장")
            score += 1

        ins_total = n(nar.get("insights"))
        ins_count = len(nar.get("insights") or [])
        if ins_total > 320:
            warns.append(f"인사이트 합계 {ins_total}자 → 270자 이하 권장 (각 ~90자)")
            score += 1
        if ins_count > 3:
            warns.append(f"인사이트 {ins_count}개 → 3개 권장")
            score += 1

        df_now = st.session_state.metrics_df
        n_rows = len(df_now) if df_now is not None else 0
        if n_rows > 7:
            warns.append(f"04 성과 지표 {n_rows}행 → 7행 이하 권장 (우측 표가 본문보다 너무 길어짐)")
            score += 1

        n_charts = len(st.session_state.get("chart_selected") or set())
        if n_charts == 0:
            # 자동 픽 fallback — 보통 2개. 충만도 추정에 2개 가정
            est_charts = 2
        else:
            est_charts = n_charts
        if est_charts >= 3 and (ins_total > 250 or n_rows > 6):
            warns.append(f"차트 3개 + 인사이트/성과표가 길어 잘림 위험")
            score += 1

        # 거친 fill % 계산 (모든 본문 + 차트 영역의 mm 합 ÷ 가용)
        # 라인당 ~5.5mm, 본문 width ~85mm 기준 한 줄 ~45자
        def lines(chars: int, w: int = 45) -> float:
            return max(1, chars / w + 0.4)
        body_left_mm = (
            lines(summary_chars) * 5 +
            lines(ov, 40) * 4.5 +
            lines(bg, 40) * 4.5 +
            lines(st_chars, 40) * 4.5
        )
        side_table_mm = 12 + n_rows * 7
        body_grid_mm = max(body_left_mm, side_table_mm) + 18  # 헤더들
        insights_mm = 12 + ins_count * 11 + (ins_total / 60) * 2
        charts_mm   = (32 if est_charts <= 2 else 28) + 8
        fixed_mm    = 110  # 헤더띠+타이틀+메타+태그+TL;DR+KPI+요약+푸터 합
        total_mm    = fixed_mm + body_grid_mm + insights_mm + charts_mm
        available_mm = 281
        fill = int(total_mm / available_mm * 100)
        return score, warns, fill

    _score, _warns, _fill = _estimate_overflow()
    if _fill >= 105 or _score >= 3:
        st.error(
            f"⚠️ 페이지 넘침 위험 — 예상 충만도 **{_fill}%** · 위험 신호 {_score}개"
        )
        for w in _warns:
            st.caption(f"  • {w}")
    elif _fill >= 95 or _score >= 1:
        st.warning(f"💡 1페이지 거의 채움 — 예상 충만도 **{_fill}%** · 권장 사항 {_score}건")
        for w in _warns:
            st.caption(f"  • {w}")
    else:
        st.success(f"✅ 1페이지 여유 — 예상 충만도 {_fill}%")

    # 같은 캠페인의 저장된 빌드가 이미 있으면 버튼은 '재생성' 으로 라벨 변경.
    _has_saved_build = bool(
        (lb := st.session_state.get("last_build"))
        and lb.get("campaign_no") == campaign.campaign_no
    )
    _build_label = "🔄 재생성 (이전 빌드 덮어쓰기)" if _has_saved_build else "📄 4개 파일 한번에 빌드"

    if st.button(_build_label, type="primary", width="stretch"):
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

        # Chart selection path — prefer user-curated candidates, fall back
        # to auto plan_charts() when the user hasn't run candidate mode.
        chart_set: list[dict] = []
        chart_debug: dict = {}
        user_cands = st.session_state.get("chart_candidates") or []
        user_picks = st.session_state.get("chart_selected") or set()

        if user_cands and user_picks:
            # User curated path — use only checked candidates (already pre-rendered)
            ordered = sorted(user_picks)[:2]
            for idx in ordered:
                if 0 <= idx < len(user_cands):
                    chart_set.append(user_cands[idx])
            chart_debug.update(
                reason="user_curated",
                detail=f"selected {len(chart_set)}/{len(user_cands)} candidates",
            )
        else:
            # Auto path — old behavior, AI silently picks up to 2
            try:
                with st.spinner("차트 큐레이션 중 (Claude)..."):
                    planned = plan_charts(
                        campaign.to_prompt_dict(),
                        st.session_state.narrative,
                        campaign_context_prose=st.session_state.context_prose,
                        extra_analysis=st.session_state.extra_analysis,
                        debug=chart_debug,
                    )
                for spec in planned:
                    try:
                        img_b64 = render_chart(spec["template"], spec["data"])
                        chart_set.append({**spec, "image_b64": img_b64})
                    except Exception as e:
                        st.warning(f"차트 '{spec.get('title')}' 렌더 실패: {e}")
            except Exception as e:
                st.warning(f"차트 큐레이션 단계 실패 (테이블로 폴백): {e}")

        # Visible status: who/what decided there were no charts.
        if chart_set:
            mode = "사용자 큐레이션" if chart_debug.get("reason") == "user_curated" else "AI 자동 픽"
            st.success(f"📈 차트 {len(chart_set)}개 생성 ({mode}) → 04 영역에 반영")
        else:
            reason = chart_debug.get("reason", "unknown")
            detail = chart_debug.get("detail", "")
            label = {
                "api_error":      "Anthropic API 호출 실패",
                "no_text":        "Claude 응답에 텍스트 없음",
                "json_error":     "Claude 응답 JSON 파싱 실패",
                "charts_not_list":"Claude 응답 스키마 불일치",
                "validated":      "Claude 가 0개 반환 (데이터 근거 부족 판단)",
                "unknown":        "사유 미상",
            }.get(reason, reason)
            st.info(
                f"📊 차트 0개 — {label}. 04 영역은 기존 성과 표로 렌더됩니다.\n\n"
                f"디테일: `{detail}`"
            )
            with st.expander("🔍 chart_planner 원응답 (앞 600자)"):
                st.code(chart_debug.get("raw", "(없음)"))

        # Header meta — DB 자동값 + 사용자 입력 합쳐서 한 dict 로
        header_tags = [
            t.strip()
            for t in (st.session_state.hdr_tags_raw or "").splitlines()
            if t.strip()
        ]
        header_meta = {
            # 사용자 입력
            "media_products":     (st.session_state.hdr_media_products or "").strip(),
            "measurement_source": (st.session_state.hdr_measurement or "").strip(),
            "cumulative_period":  (st.session_state.hdr_cumulative_period or "").strip(),
            "tags":               header_tags,
            # DB 자동
            "advertiser":   campaign.masked_advertiser,
            "campaign_no":  campaign.campaign_no,
            "period_start": campaign.period_start,
            "period_end":   campaign.period_end,
        }

        context = {
            "headline": st.session_state.headline,
            "subhead": st.session_state.subhead,
            "campaign": asdict(campaign),
            "narrative": st.session_state.narrative,
            "chart_set": chart_set,
            "header_meta": header_meta,
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

        # 1) 산출물을 메모리에 보관 (rerun 으로 디스크 휘발돼도 다운로드 가능)
        html_bytes = web_html.read_bytes()
        pdf_bytes = pdf.read_bytes()
        docx_bytes = docx.read_bytes()
        txt_bytes = txt.read_bytes()
        st.session_state.last_build = {
            "campaign_no": campaign.campaign_no,
            "out_dir": str(out_dir),
            "files": [
                ("HTML", html_bytes, web_html.name),
                ("PDF",  pdf_bytes,  pdf.name),
                ("DOCX", docx_bytes, docx.name),
                ("TXT",  txt_bytes,  txt.name),
            ],
        }

        # 2) Supabase 에 영속화 — 다음 세션·다른 사용자도 같은 캠페인 재방문 시
        #    바로 다운로드 가능하게.
        hero_bytes = None
        if st.session_state.hero_path:
            try:
                hero_bytes = Path(st.session_state.hero_path).read_bytes()
            except Exception:
                hero_bytes = None
        # 저장은 사용자 입력만 — DB 자동값은 매 빌드마다 재구성하므로 보존 불필요
        header_meta_to_save = {
            "media_products":     header_meta["media_products"],
            "measurement_source": header_meta["measurement_source"],
            "cumulative_period":  header_meta["cumulative_period"],
            "tags":               header_meta["tags"],
        }
        ok = save_build(
            campaign_no=campaign.campaign_no,
            user_email=user_email,
            headline=st.session_state.headline,
            subhead=st.session_state.subhead,
            context_prose=st.session_state.context_prose,
            extra_analysis=st.session_state.extra_analysis,
            narrative=st.session_state.narrative,
            metrics_table=df.drop(columns=["_select"], errors="ignore").to_dict(orient="records"),
            header_meta=header_meta_to_save,
            hero_image=hero_bytes,
            html=html_bytes,
            pdf=pdf_bytes,
            docx=docx_bytes,
            txt=txt_bytes,
        )
        if ok:
            st.success(f"완료 → {out_dir}  ·  Supabase 에 저장됨 (다음 접속 때 자동 복원)")
        else:
            st.warning(f"완료 → {out_dir}  ·  ⚠️ Supabase 저장 실패 (다운로드는 이번 세션에서 가능)")

    # 빌드 결과 다운로드 영역 — 버튼 핸들러 밖에 있어서 rerun 후에도 유지
    last = st.session_state.get("last_build")
    if last and last.get("campaign_no") == campaign.campaign_no:
        st.caption(f"📦 마지막 빌드: `{last['out_dir']}`")
        cols = st.columns(len(last["files"]))
        for i, (label, data, fname) in enumerate(last["files"]):
            with cols[i]:
                st.download_button(
                    f"{label} 다운로드",
                    data=data,
                    file_name=fname,
                    key=f"dl_{label}_{campaign.campaign_no}",
                    width="stretch",
                )
