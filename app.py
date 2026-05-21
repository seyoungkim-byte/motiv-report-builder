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
)
from ai.narrative import (
    BULLET_SECTIONS,
    INSIGHTS_KEY,
    INSIGHTS_LABEL,
    TLDR_KEY,
    TLDR_LABEL,
)
from auth import logout, require_auth
from config import load_settings
from data import (
    CampaignData, CampaignRepository, MetricRow,
    load_build, save_build, last_storage_error,
    get_setting, set_setting,
    save_example, list_examples, delete_example,
    save_draft, load_draft, delete_draft,
)
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
_session_default("metrics_footnotes", "")
# Header meta — user-supplied fields not in DB (집행 상품 / 구매 측정 / 태그 / 누적 기간)
_session_default("hdr_media_products", "")
_session_default("hdr_measurement", "")
_session_default("hdr_tags_raw", "")            # 한 줄에 한 태그
_session_default("hdr_cumulative_period", "")
# 성과 지표 표 — 편집 모드 / 백업 (Streamlit data_editor 의 reactive 동기화
# 문제를 피하려 명시적 편집 모드 도입. 편집 중에는 session_state 사후 수정
# 안 함 → 저장 클릭 시에만 commit).
_session_default("metrics_edit_mode", False)
_session_default("metrics_df_backup", None)


# ─────────────────────────────────────── Sidebar: campaign picker
def _reset_campaign_state(data: CampaignData):
    """Clear all per-campaign UI state so the new selection starts fresh."""
    st.session_state.campaign = data
    st.session_state.last_no = data.campaign_no
    # _select 컬럼을 처음부터 박아둬 data_editor 의 내부 캐시 식별자와 어긋나지 않게.
    # (이전에는 매 render 마다 .assign(_select=False) 로 새 DF 를 만들어 editor 가
    # 한 rerun 늦게 반영되는 lag 발생.)
    _df = pd.DataFrame(
        [{"indicator": m.indicator, "value": m.value, "note": m.note} for m in data.metrics_table]
    )
    _df["_select"] = False
    # 기본 04 표 = 전체 노출 (사용자가 빼고 싶으면 체크 해제)
    _df["_table"]  = True
    # 강조 (coral) — 디폴트: 마지막 row 만 True. 사용자가 체크박스로 재선택 가능.
    _df["_highlight"] = [i == len(_df) - 1 for i in range(len(_df))]
    st.session_state.metrics_df = _df
    st.session_state.narrative = (
        {k: "" for k, _ in NARRATIVE_SECTIONS} | {INSIGHTS_KEY: [], TLDR_KEY: []}
    )
    for k, _ in NARRATIVE_SECTIONS:
        st.session_state[f"nar_{k}"] = ""
    st.session_state["nar_insights"] = ""
    st.session_state["nar_tldr"] = ""
    st.session_state.context_prose = ""
    st.session_state.extra_analysis = ""
    st.session_state.metrics_footnotes = ""
    st.session_state.headline = ""
    st.session_state.subhead = ""
    st.session_state.hero_path = None
    st.session_state.last_build = None
    # Header 메타도 캠페인 단위로 리셋
    st.session_state.hdr_media_products = ""
    st.session_state.hdr_measurement = ""
    st.session_state.hdr_tags_raw = ""
    st.session_state.hdr_cumulative_period = ""
    # 편집 모드/백업 리셋 — 새 캠페인 들어오면 자동 표시 모드로
    st.session_state.metrics_edit_mode = False
    st.session_state.metrics_df_backup = None
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
    st.session_state.metrics_footnotes = src.get("metrics_footnotes") or ""
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

    # metrics_table 도 saved build 에서 복원 — 사용자가 수기로 편집한 indicator/
    # value/note 와 _table / _highlight 체크 상태를 보존.
    saved_metrics = src.get("metrics_table") or []
    if saved_metrics:
        saved_df = pd.DataFrame(saved_metrics)
        # 컬럼 보장 + 옛 빌드 잔존 컬럼(_kpi) 제거
        if "_kpi" in saved_df.columns:
            saved_df = saved_df.drop(columns=["_kpi"])
        if "_select" not in saved_df.columns:
            saved_df["_select"] = False
        if "_table" not in saved_df.columns:
            saved_df["_table"] = True
        # highlight 컬럼은 옛 빌드(saved) → MetricRow.highlight 또는 _highlight 어느
        # 쪽이든 흡수. 둘 다 없으면 마지막 행 default.
        if "_highlight" not in saved_df.columns and "highlight" in saved_df.columns:
            saved_df["_highlight"] = saved_df["highlight"].fillna(False).astype(bool)
            saved_df = saved_df.drop(columns=["highlight"])
        if "_highlight" not in saved_df.columns:
            saved_df["_highlight"] = [i == len(saved_df) - 1 for i in range(len(saved_df))]
        st.session_state.metrics_df = saved_df

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
    st.text_area(
        "헤드라인",
        key="headline",
        height=80,
        help=(
            "예시: '크로스디바이스 광고로 고객 획득 비용 53% 절감한 성인영양식 캠페인 사례'. "
            "Enter 로 줄바꿈 가능 — 입력한 위치 그대로 PDF·HTML 에 반영됩니다."
        ),
    )
    st.text_area(
        "서브헤드 (선택)",
        key="subhead",
        height=68,
        help="Enter 로 줄바꿈 가능.",
    )

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

    # ── 임시 저장본(draft) 복원 알림 ──
    # JWT 만료 / Streamlit Cloud sleep 으로 작성 중인 내용이 날아가는 사고를 막기 위해
    # narrative_drafts 테이블에 사용자가 '💾 임시 저장' 으로 적재한 내용을 보존.
    # 캠페인 로드 시 draft 가 빌드보다 새로우면 banner 로 복원 제안.
    _draft = load_draft(campaign.campaign_no)
    _build_built_at = (st.session_state.get("last_build") or {}).get("built_at")
    if _draft and not st.session_state.get(f"_draft_handled_{campaign.campaign_no}"):
        _draft_at = _draft.get("updated_at") or ""
        _newer_than_build = (not _build_built_at) or (_draft_at > str(_build_built_at))
        if _newer_than_build:
            cont = st.container(border=True)
            cont.markdown(
                f"💾 **임시 저장본이 있습니다** — `{_draft_at[:19].replace('T',' ')}` 에 저장됨. "
                f"마지막 빌드보다 새로워서 작성 중인 내용일 가능성이 큽니다."
            )
            _c1, _c2, _c3 = cont.columns([0.30, 0.30, 0.40])
            if _c1.button("✅ 복원하기", key=f"draft_restore_{campaign.campaign_no}",
                          type="primary", width="stretch"):
                st.session_state.headline         = _draft.get("headline") or ""
                st.session_state.subhead          = _draft.get("subhead") or ""
                st.session_state.context_prose    = _draft.get("context_prose") or ""
                st.session_state.extra_analysis   = _draft.get("extra_analysis") or ""
                st.session_state.metrics_footnotes = _draft.get("metrics_footnotes") or ""
                _nar = _draft.get("narrative") or {}
                if isinstance(_nar, dict) and _nar:
                    st.session_state.narrative = _nar
                    for _k, _ in NARRATIVE_SECTIONS:
                        _v = _nar.get(_k, "")
                        if _k in BULLET_SECTIONS and isinstance(_v, list):
                            st.session_state[f"nar_{_k}"] = "\n".join(_v)
                        else:
                            st.session_state[f"nar_{_k}"] = _v if isinstance(_v, str) else ""
                    st.session_state["nar_insights"] = "\n".join(_nar.get(INSIGHTS_KEY, []))
                # 헤더 메타
                st.session_state.hdr_media_products     = _draft.get("hdr_media_products") or ""
                st.session_state.hdr_measurement        = _draft.get("hdr_measurement") or ""
                st.session_state.hdr_tags_raw           = _draft.get("hdr_tags_raw") or ""
                st.session_state.hdr_cumulative_period  = _draft.get("hdr_cumulative_period") or ""
                # metrics_table
                _mt = _draft.get("metrics_table") or []
                if isinstance(_mt, list) and _mt:
                    _df = pd.DataFrame(_mt)
                    if "_select" not in _df.columns: _df["_select"] = False
                    if "_table" not in _df.columns:  _df["_table"]  = True
                    if "_highlight" not in _df.columns:
                        _df["_highlight"] = [i == len(_df) - 1 for i in range(len(_df))]
                    st.session_state.metrics_df = _df
                st.session_state[f"_draft_handled_{campaign.campaign_no}"] = True
                st.rerun()
            if _c2.button("🗑️ 임시본 무시", key=f"draft_discard_{campaign.campaign_no}",
                          width="stretch"):
                delete_draft(campaign.campaign_no)
                st.session_state[f"_draft_handled_{campaign.campaign_no}"] = True
                st.rerun()
            _c3.caption("복원하면 현재 화면 내용이 임시본으로 덮어쓰여집니다.")

    # ── 톤 가이드 — 모든 캠페인 공통 (Supabase app_settings 저장) ──
    # 한 번 설정하면 마케팅 팀 누구든 같은 톤으로 생성. 캠페인 무관 영구.
    if "tone_guide" not in st.session_state:
        st.session_state["tone_guide"] = get_setting("tone_guide", "")
    with st.expander(
        "🎨 톤 가이드 (모든 캠페인 공통)" + (
            " — ✅ 설정됨" if st.session_state["tone_guide"].strip()
            else " — ⚪ 비어있음"
        ),
        expanded=False,
    ):
        st.caption(
            "여기 적은 톤·문체·표현 규칙은 모든 캠페인의 Claude 초안 생성에 자동 적용됩니다. "
            "회사 통일 톤이므로 한 번 설정 후 거의 안 건드려도 됩니다."
        )
        st.text_area(
            "톤 가이드",
            key="tone_guide",
            height=180,
            placeholder=(
                "예시:\n"
                "- 과장어 절대 금지 ('폭발적', '획기적', '압도적' 등)\n"
                "- 한 문장은 60자 이하로 끊어주세요\n"
                "- 모든 섹션 첫 문장은 명사로 시작 (예: '카테고리 최성수기에...')\n"
                "- 영문 약어는 한 번만 풀이 후 약어 사용 (예: CTV(Connected TV))\n"
                "- 수치는 본문에 한 번만 — 표·차트가 보여주는 건 본문에서 다시 안 읊음"
            ),
            label_visibility="collapsed",
        )
        col_a, col_b = st.columns([1, 4])
        with col_a:
            if st.button("💾 저장", key="tone_guide_save", help="Supabase에 저장 — 다른 사용자 세션에도 즉시 반영"):
                if set_setting("tone_guide", st.session_state["tone_guide"]):
                    st.success("저장 완료")
                else:
                    st.error("저장 실패 — Supabase 연결 확인")
        with col_b:
            if st.button("🔄 서버에서 다시 불러오기", key="tone_guide_reload"):
                st.session_state["tone_guide"] = get_setting("tone_guide", "")
                st.rerun()

        # ── 톤 레퍼런스 (few-shot 예시) ──
        st.markdown("---")
        _refs = list_examples(limit=50)
        st.markdown(
            f"**📚 톤 레퍼런스** ({len(_refs)}개 저장됨) — "
            "빌드 후 ‘🎯 톤 레퍼런스로 저장’ 으로 추가. 다음 초안 생성 때 자동 주입."
        )
        st.session_state.setdefault("tone_ref_count", 3)
        st.session_state["tone_ref_count"] = st.slider(
            "초안 생성 시 주입할 최근 레퍼런스 수",
            min_value=0, max_value=5,
            value=int(st.session_state.get("tone_ref_count", 3)),
            help="0 = 사용 안 함. 너무 많으면 토큰 비용 증가 + 톤이 옛 케이스로 쏠릴 수 있음.",
            key="tone_ref_count_slider",
        )
        if _refs:
            for _r in _refs:
                _cols = st.columns([0.72, 0.16, 0.12])
                _label_disp = _r.get("label") or "(라벨 없음)"
                _saved_at = (_r.get("saved_at") or "")[:10]
                _cols[0].caption(
                    f"#{_r.get('id')} · `{_r.get('campaign_no')}` · {_label_disp} · {_saved_at}"
                )
                if _cols[1].button("👁️", key=f"ref_view_{_r['id']}", help="요약 미리보기"):
                    st.session_state[f"_ref_preview_{_r['id']}"] = True
                if _cols[2].button("🗑️", key=f"ref_del_{_r['id']}", help="이 레퍼런스 삭제"):
                    if delete_example(int(_r["id"])):
                        st.rerun()
                if st.session_state.get(f"_ref_preview_{_r['id']}"):
                    with st.expander(f"미리보기 #{_r['id']}", expanded=True):
                        _nar = _r.get("narrative") or {}
                        if _nar.get("summary"):
                            st.markdown(f"**요약**\n\n{_nar['summary']}")
                        for _k, _t in [
                            ("overview", "01"), ("background", "02"),
                            ("strategy", "03"),
                        ]:
                            _v = _nar.get(_k)
                            if isinstance(_v, list) and _v:
                                st.markdown(f"**{_t}**\n\n" + "\n".join(_v))
                        if _nar.get("insights"):
                            st.markdown("**05 인사이트**\n\n" + "\n".join(_nar["insights"]))
                        if st.button("닫기", key=f"ref_close_{_r['id']}"):
                            st.session_state[f"_ref_preview_{_r['id']}"] = False
                            st.rerun()
        else:
            st.caption("아직 저장된 레퍼런스가 없습니다.")

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
                _ref_count = int(st.session_state.get("tone_ref_count", 3))
                _examples = list_examples(limit=_ref_count) if _ref_count > 0 else []
                result = generate_narrative(
                    campaign.to_prompt_dict(),
                    campaign_context_prose=st.session_state.context_prose,
                    extra_analysis=st.session_state.extra_analysis,
                    tone_guide=st.session_state.get("tone_guide", ""),
                    tone_examples=_examples,
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

    # TL;DR 은 v3 레이아웃에서 미사용 — UI 노출 안 함. 옛 빌드 호환은 setdefault 로.
    st.session_state.narrative[TLDR_KEY] = []

    st.caption(
        "💡 서식 규칙\n"
        "  • 기본: 줄 단위로 한 단락 (불릿 없음). 줄바꿈 = 단락 구분.\n"
        "  • 불릿이 필요한 줄만 맨 앞에 `- ` 접두사. 연속된 `- ` 줄은 자동으로 불릿 묶음.\n"
        "  • 핵심 phrase 는 `**텍스트**` 로 감싸면 하이라이터 마커로 강조. 섹션당 1~2개 권장."
    )

    def _parse_lines_with_spacers(text: str, cap: int | None = None) -> list[str]:
        """textarea 입력을 list[str] 로 파싱.
        - 줄 단위 항목, 빈 줄도 spacer 로 보존 (시각적 여백)
        - 시작/끝 빈 줄은 제거, 연속 빈 줄은 1개로 collapse
        - cap 지정 시 비-빈 항목 기준으로 cap"""
        lines = [ln.strip() for ln in (text or "").splitlines()]
        # 앞뒤 trim
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        # 연속 빈 줄 collapse
        out: list[str] = []
        prev_empty = False
        for ln in lines:
            if ln:
                out.append(ln)
                prev_empty = False
            elif not prev_empty:
                out.append("")
                prev_empty = True
        if cap is not None:
            # 비-빈 항목 기준 cap (spacer 는 count 에서 제외)
            kept: list[str] = []
            content_count = 0
            for ln in out:
                if ln:
                    if content_count >= cap:
                        break
                    kept.append(ln)
                    content_count += 1
                else:
                    kept.append(ln)
            # 끝 trailing spacer 다시 정리
            while kept and not kept[-1]:
                kept.pop()
            out = kept
        return out

    for key, label in NARRATIVE_SECTIONS:
        if key in BULLET_SECTIONS:
            st.text_area(
                f"{label} — 한 줄에 한 항목 (불릿 원하면 줄 앞에 '- ', 항목 사이 빈 줄 = 여백)",
                height=110,
                key=f"nar_{key}",
                help="기본은 단락. '- ' 로 시작한 줄만 ▪ 불릿. 빈 줄 = 시각적 여백.",
            )
            st.session_state.narrative[key] = _parse_lines_with_spacers(
                st.session_state[f"nar_{key}"], cap=6
            )
        else:
            st.text_area(label, height=120, key=f"nar_{key}")
            st.session_state.narrative[key] = st.session_state[f"nar_{key}"]

    st.text_area(
        INSIGHTS_LABEL + " — 한 줄에 한 항목 (항목 사이 빈 줄 = 여백)",
        height=140,
        key="nar_insights",
        help="레퍼런스 기준 3항목 권장. 항목 사이 빈 줄을 넣으면 PDF 에서도 한 줄 띄워집니다.",
    )
    st.session_state.narrative[INSIGHTS_KEY] = _parse_lines_with_spacers(
        st.session_state["nar_insights"], cap=5
    )

    # ── 💾 임시 저장 — 작성 중인 내용을 Supabase narrative_drafts 에 보관.
    # JWT 만료/세션 휘발에도 살아남고, 캠페인 재오픈 시 배너로 복원 제안. ──
    _sd_c1, _sd_c2 = st.columns([0.35, 0.65])
    if _sd_c1.button(
        "💾 작성 중인 내용 임시 저장",
        key=f"save_draft_btn_{campaign.campaign_no}",
        help="narrative + 헤더 메타 + 성과지표 표를 Supabase 에 즉시 저장. 빌드 안 해도 복원 가능.",
        width="stretch",
    ):
        _df = st.session_state.get("metrics_df")
        _mt_payload = (
            _df.drop(columns=["_select"], errors="ignore").to_dict(orient="records")
            if _df is not None else []
        )
        ok = save_draft(
            campaign_no=campaign.campaign_no,
            headline=st.session_state.get("headline", ""),
            subhead=st.session_state.get("subhead", ""),
            context_prose=st.session_state.get("context_prose", ""),
            extra_analysis=st.session_state.get("extra_analysis", ""),
            narrative=st.session_state.get("narrative") or {},
            metrics_table=_mt_payload,
            metrics_footnotes=st.session_state.get("metrics_footnotes", ""),
            hdr_media_products=st.session_state.get("hdr_media_products", ""),
            hdr_measurement=st.session_state.get("hdr_measurement", ""),
            hdr_tags_raw=st.session_state.get("hdr_tags_raw", ""),
            hdr_cumulative_period=st.session_state.get("hdr_cumulative_period", ""),
            updated_by=user_email,
        )
        if ok:
            _sd_c2.success("✅ 임시 저장됨 — 세션이 끊겨도 캠페인 재오픈 시 복원 안내")
        else:
            _sd_c2.error("저장 실패 — Supabase 연결 또는 narrative_drafts 테이블 확인")

with col_r:
    # ── 4. 성과 지표 — 표시 모드 / 편집 모드 토글 ──────────────
    # data_editor 의 reactive 동기화 문제(셀 입력 lag · 새 행 사라짐)를
    # 회피하기 위해 명시적 편집 모드 도입. 표시 모드에서는 st.dataframe
    # 으로 read-only 노출, 편집 모드에서만 data_editor 가 활성화되고
    # 저장 클릭 시 일괄 commit.
    _c_title, _c_btn = st.columns([0.7, 0.3])
    _c_title.subheader("4. 성과 지표 (04. 캠페인 성과)")

    _in_edit = st.session_state.get("metrics_edit_mode", False)
    if not _in_edit:
        _c_btn.button(
            "✏️ 편집 모드",
            type="primary",
            width="stretch",
            key="enter_edit",
        )
        if st.session_state.get("enter_edit"):
            st.session_state.metrics_df_backup = (
                st.session_state.metrics_df.copy()
                if st.session_state.metrics_df is not None else None
            )
            st.session_state.metrics_edit_mode = True
            if "metrics_editor" in st.session_state:
                del st.session_state["metrics_editor"]
            st.rerun()
        st.caption("표시 모드 — 편집하려면 우측 [✏️ 편집 모드] 클릭")
    else:
        _c_btn.markdown(
            "<div style='padding:6px 12px;background:#fff7e0;border:1px solid #f0c060;"
            "border-radius:4px;color:#6b4d00;font-weight:600;font-size:13px;"
            "text-align:center;'>🟡 편집 중</div>",
            unsafe_allow_html=True,
        )
        st.caption("편집 중 — 하단 [💾 저장] 또는 [❌ 취소] 클릭")
    # 컬럼 보장 (표시·편집 양쪽에서 사용)
    if st.session_state.metrics_df is None:
        st.session_state.metrics_df = pd.DataFrame(
            columns=["indicator", "value", "note", "_select", "_table", "_highlight"]
        )
    else:
        df_cur = st.session_state.metrics_df
        if "_select" not in df_cur.columns:
            df_cur["_select"] = False
        if "_table" not in df_cur.columns:
            df_cur["_table"] = True
        if "_highlight" not in df_cur.columns:
            df_cur["_highlight"] = [i == len(df_cur) - 1 for i in range(len(df_cur))]

    if st.session_state.metrics_df is None or len(st.session_state.metrics_df) == 0:
        st.info("좌측에서 캠페인을 로드하면 카탈로그 기반 성과 지표가 자동으로 채워집니다.")
    elif not _in_edit:
        # ── 표시 모드 (read-only) ──
        _display_df = st.session_state.metrics_df
        _cols = [c for c in ["_table", "_highlight", "indicator", "value", "note"] if c in _display_df.columns]
        st.dataframe(
            _display_df[_cols],
            column_config={
                "_table":     st.column_config.CheckboxColumn("표",   disabled=True, width="small"),
                "_highlight": st.column_config.CheckboxColumn("강조", disabled=True, width="small"),
                "indicator":  st.column_config.TextColumn("성과 지표"),
                "value":      st.column_config.TextColumn("성과"),
                "note":       st.column_config.TextColumn("비고"),
            },
            width="stretch",
            hide_index=True,
        )
    else:
        # ── 편집 모드 ──
        edited = st.data_editor(
            st.session_state.metrics_df,
            num_rows="dynamic",
            width="stretch",
            column_config={
                "_select":    st.column_config.CheckboxColumn("↕",  width="small",
                                 help="체크 후 아래 ▲▼ 로 행 이동"),
                "_table":     st.column_config.CheckboxColumn("표",  width="small",
                                 help="04 캠페인 성과 표 노출. 체크된 행 순서대로."),
                "_highlight": st.column_config.CheckboxColumn("강조", width="small",
                                 help="이 행의 수치를 coral 색으로 강조. 여러 개 가능."),
                "indicator":  st.column_config.TextColumn("성과 지표"),
                "value":      st.column_config.TextColumn("성과"),
                "note":       st.column_config.TextColumn("비고"),
            },
            column_order=["_select", "_table", "_highlight", "indicator", "value", "note"],
            key="metrics_editor",
        )

        # ── 행 이동 + 저장 / 취소 (편집 모드 전용) ──
        _bcols = st.columns([0.13, 0.13, 0.13, 0.30, 0.30])

        def _swap_in_edited(direction: int):
            """`edited` (사용자 입력 누적) 에서 _select 행을 1칸 이동.
            결과를 session_state.metrics_df 에 쓰고 editor 캐시 리셋 후 rerun."""
            df_now = edited
            if "_select" not in df_now.columns:
                return
            sel = list(df_now.index[df_now["_select"].fillna(False) == True])
            if not sel:
                return
            i = sel[0]
            j = i + direction
            if j < 0 or j >= len(df_now):
                return
            new_idx = df_now.index.tolist()
            new_idx[i], new_idx[j] = new_idx[j], new_idx[i]
            new_df = df_now.loc[new_idx].reset_index(drop=True)
            new_df["_select"] = False
            new_df.at[j, "_select"] = True
            st.session_state.metrics_df = new_df
            if "metrics_editor" in st.session_state:
                del st.session_state["metrics_editor"]
            st.rerun()

        if _bcols[0].button("▲ 위로", width="stretch", key="metric_up"):
            _swap_in_edited(-1)
        if _bcols[1].button("▼ 아래로", width="stretch", key="metric_down"):
            _swap_in_edited(+1)

        if _bcols[3].button("💾 저장", type="primary", width="stretch", key="save_edit"):
            st.session_state.metrics_df = edited.copy()
            st.session_state.metrics_edit_mode = False
            st.session_state.metrics_df_backup = None
            if "metrics_editor" in st.session_state:
                del st.session_state["metrics_editor"]
            st.rerun()
        if _bcols[4].button("❌ 취소", width="stretch", key="cancel_edit"):
            if st.session_state.get("metrics_df_backup") is not None:
                st.session_state.metrics_df = st.session_state.metrics_df_backup
            st.session_state.metrics_edit_mode = False
            st.session_state.metrics_df_backup = None
            if "metrics_editor" in st.session_state:
                del st.session_state["metrics_editor"]
            st.rerun()

        # 보조 액션 — 카탈로그에서 fresh 한 metrics 로 덮어쓰기.
        # 옛 빌드의 stale 라벨 정리하거나 신규 카탈로그 메트릭 도입 시 사용.
        # 편집 중에만 노출. 클릭 시 backup 은 보존 (취소로 되돌릴 수 있게).
        st.caption(
            "📚 카탈로그에서 새로 불러오기 — 기존 수기 편집이 모두 카탈로그 기본값으로 덮어집니다. "
            "옛 빌드의 stale 라벨 정리 또는 카탈로그 신규 메트릭 추가 시 사용."
        )
        if st.button("🔄 카탈로그에서 새로고침 (수기 편집 폐기)", key="refresh_catalog", width="stretch"):
            fresh_df = pd.DataFrame(
                [{"indicator": m.indicator, "value": m.value, "note": m.note} for m in campaign.metrics_table]
            )
            fresh_df["_select"] = False
            fresh_df["_table"]  = True
            fresh_df["_highlight"] = [i == len(fresh_df) - 1 for i in range(len(fresh_df))]
            st.session_state.metrics_df = fresh_df
            if "metrics_editor" in st.session_state:
                del st.session_state["metrics_editor"]
            st.rerun()

    # ── 표 주석 — 04 표 아래에 노출되는 자유 형식 footnote ──
    st.text_area(
        "📎 표 주석 (선택) — 표 아래에 작게 노출",
        key="metrics_footnotes",
        height=90,
        placeholder=(
            "예시:\n"
            "*1 : 시장 평균 대비 지수, 100 = 동일\n"
            "*2 : 광고 노출자 = CTV 광고 1회 이상 시청 가구"
        ),
        help=(
            "표에 담기 긴 추가 설명을 줄 단위로. 표 indicator/note 안에 `*1` 같은 마커를 "
            "직접 넣고, 여기에 `*1 : 의미` 식으로 매칭하면 됩니다. "
            "`**굵게**` 마크업도 동일하게 지원."
        ),
    )

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
        st.caption(
            "🖼️ **권장 사이즈** — 가로:세로 = **5:4 (가로형)**, "
            "**최소 1400 × 1120 px** (인쇄 품질 위해 더 큰 것도 OK). "
            "PDF 헤더 우측 절반을 풀폭으로 차지하므로 **여백 없는 풀블리드 사진** 을 권장합니다. "
            "센터 영역에 핵심 피사체가 오면 잘림 위험 적음."
        )
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
        fixed_mm    = 110  # 헤더띠+타이틀+메타+요약+푸터 합
        total_mm    = fixed_mm + body_grid_mm + insights_mm
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

    # 편집 모드 중이면 빌드 차단 — 미저장 변경이 산출물에 반영 안 되는 사고 방지
    _block_build = st.session_state.get("metrics_edit_mode", False)
    if _block_build:
        st.warning("✏️ 4번 성과 지표 영역이 편집 모드입니다. 먼저 [💾 저장] 또는 [❌ 취소] 후 빌드 가능합니다.")

    _auto_hero = st.checkbox(
        "🖼️ 히어로 이미지 자동 생성 (Gemini)",
        value=True,
        help="이미지가 아직 없으면 빌드 시 Gemini 가 자동 생성합니다. 끄면 placeholder 로 빌드.",
        key="auto_hero_on_build",
    )
    if st.button(_build_label, type="primary", width="stretch", disabled=_block_build):
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

        # Stale hero path 정리 — 옛 빌드 load 직후의 path 가 더 이상 디스크에 없거나
        # 0 byte 면 새로 생성해야 함. (Streamlit Cloud 는 컨테이너 재시작 시 output/
        # 디렉터리가 휘발됨.)
        _hp = st.session_state.get("hero_path")
        if _hp:
            try:
                _hp_path = Path(_hp)
                if not _hp_path.exists() or _hp_path.stat().st_size == 0:
                    st.session_state.hero_path = None
                    st.caption(
                        "ℹ️ 직전 빌드의 히어로 이미지 파일이 사라져 새로 생성합니다."
                    )
            except Exception:
                st.session_state.hero_path = None

        # Hero image auto-fallback — 사용자가 명시적으로 생성 안 했고 토글 ON 이면
        # Gemini 호출로 한 번 시도. 실패해도 빌드는 계속 (placeholder 로).
        if _auto_hero and not st.session_state.get("hero_path"):
            try:
                with st.spinner("히어로 이미지 자동 생성 중…"):
                    _brief = (
                        f"{campaign.channel or 'CTV/Mobile'} 광고 케이스스터디 히어로 이미지. "
                        f"업종: {campaign.industry or '광고 일반'}. "
                        "담백한 에디토리얼 톤, 라이프스타일 중심, 텍스트·로고·실제 브랜드 노출 없음."
                    )
                    _path = generate_hero_image(
                        _brief, filename=f"hero_{campaign.campaign_no}.png"
                    )
                    st.session_state.hero_path = str(_path)
            except Exception as e:
                st.warning(
                    "⚠️ **히어로 이미지 자동 생성 실패** — placeholder 로 빌드를 계속합니다."
                )
                with st.expander("🔍 원인 진단 가이드", expanded=False):
                    st.markdown(
                        "**가장 흔한 원인 3가지:**\n\n"
                        "1. **`GEMINI_API_KEY` Streamlit Secrets 에 없음**\n"
                        "   - Streamlit Cloud → 앱 Settings → Secrets → 추가:\n"
                        "     `GEMINI_API_KEY = \"...\"`\n\n"
                        "2. **`GEMINI_IMAGE_MODEL` 이 이미지 출력 안 되는 모델**\n"
                        "   - 권장: `GEMINI_IMAGE_MODEL = \"gemini-2.5-flash-image-preview\"` (default)\n"
                        "   - imagen-* 모델은 generate_content API 와 호환 X\n\n"
                        "3. **모델/리전 권한 또는 안전 필터**\n"
                        "   - 모델 페이지에서 활성화 여부 확인\n"
                        "   - 또는 캠페인 industry/channel 텍스트가 필터에 걸렸을 수 있음\n"
                    )
                    st.code(str(e), language="text")

        # 히어로 이미지 상태 한 줄 표시 — 빌드마다 placeholder 인지 실제 이미지인지 명확하게.
        _hp_final = st.session_state.get("hero_path")
        if _hp_final and Path(_hp_final).exists() and Path(_hp_final).stat().st_size > 0:
            st.caption(f"🖼️ 히어로 이미지: `{Path(_hp_final).name}` 사용")
        elif not _auto_hero:
            st.caption("🖼️ 히어로 이미지: 자동 생성 토글 OFF — placeholder 로 빌드")
        else:
            st.caption("🖼️ 히어로 이미지: placeholder 로 빌드 (생성 실패 또는 미설정)")

        # materialize the edited metrics back into the campaign payload.
        # '표' 체크박스(_table) 만 사용 — KPI 스트립은 제거됨.
        df = st.session_state.metrics_df
        if df is None:
            df = pd.DataFrame(columns=["indicator", "value", "note"])
        all_records = df.to_dict(orient="records")
        valid_records = [
            r for r in all_records
            if str(r.get("indicator", "")).strip() and str(r.get("value", "")).strip()
        ]
        def _to_row(r):
            return MetricRow(
                indicator=str(r.get("indicator", "")).strip(),
                value=str(r.get("value", "")).strip(),
                note=str(r.get("note", "")).strip(),
                highlight=bool(r.get("_highlight", False)),
            )
        # 04 표 = '표' 체크박스가 켜진 행만 (옛 빌드 호환: 컬럼 없으면 모두 포함)
        table_records = [r for r in valid_records if r.get("_table", True)]
        campaign.metrics_table = [_to_row(r) for r in table_records]

        # Header meta — DB 자동값 + 사용자 입력 합쳐서 한 dict 로
        header_tags = [
            t.strip()
            for t in (st.session_state.hdr_tags_raw or "").splitlines()
            if t.strip()
        ]
        # 집행 기간(월 단위) — '2026.01 – 02' 식 사용자 친화 표기. 일자 노출 금지.
        def _fmt_period_month(start: str | None, end: str | None) -> str:
            if not start: return ""
            s7 = start[:7].replace("-", ".") if len(start) >= 7 else ""
            if not end: return s7
            e7 = end[:7].replace("-", ".") if len(end) >= 7 else ""
            if not e7 or s7 == e7: return s7
            # 같은 해면 끝월만 짧게 ('2026.01 – 02'), 해 다르면 풀로
            if s7[:4] == e7[:4]:
                return f"{s7} – {e7[5:]}"
            return f"{s7} – {e7}"

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
            "period_month": _fmt_period_month(campaign.period_start, campaign.period_end),
        }

        context = {
            "headline": st.session_state.headline,
            "subhead": st.session_state.subhead,
            "campaign": asdict(campaign),
            "narrative": st.session_state.narrative,
            "header_meta": header_meta,
            "metrics_footnotes": (st.session_state.get("metrics_footnotes") or "").strip(),
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
            metrics_footnotes=st.session_state.get("metrics_footnotes", ""),
            header_meta=header_meta_to_save,
            hero_image=hero_bytes,
            html=html_bytes,
            pdf=pdf_bytes,
            docx=docx_bytes,
            txt=txt_bytes,
        )
        if ok:
            _warn = last_storage_error()
            if _warn:
                st.warning(f"완료 → {out_dir}  ·  ⚠️ {_warn}")
            else:
                st.success(f"완료 → {out_dir}  ·  Supabase 에 저장됨 (다음 접속 때 자동 복원)")
            # 빌드 성공 = draft 가 더 이상 필요 없음. 다음 캠페인 오픈 시 stale banner 안 뜨게.
            delete_draft(campaign.campaign_no)
            st.session_state[f"_draft_handled_{campaign.campaign_no}"] = True
        else:
            _err = last_storage_error() or "(원인 미상)"
            st.warning(
                f"완료 → {out_dir}  ·  ⚠️ Supabase 저장 실패 (다운로드는 이번 세션에서 가능)"
            )
            with st.expander("🔍 저장 실패 원인", expanded=True):
                st.code(_err, language="text")

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

        # ── 톤 레퍼런스로 저장 ──
        # 빌드 결과 narrative 를 톤 레퍼런스 풀에 등록. 다음 초안 생성 때 자동 주입.
        st.markdown("---")
        _ref_cols = st.columns([0.55, 0.30, 0.15])
        _ref_label = _ref_cols[0].text_input(
            "라벨 (선택, 검색·관리용)",
            key=f"ref_label_{campaign.campaign_no}",
            placeholder="예: 시즌 캠페인 표준 톤",
            label_visibility="collapsed",
        )
        _ref_cols[1].caption("← 이 빌드의 narrative 톤을 다음 초안 작성에 참고시키려면")
        if _ref_cols[2].button(
            "🎯 톤 레퍼런스",
            key=f"save_ref_{campaign.campaign_no}",
            help="이 빌드의 narrative 를 톤 레퍼런스로 Supabase 에 저장. 다음 [Claude로 섹션 초안 생성] 시 최근 N개가 자동 주입됨.",
            width="stretch",
        ):
            _new_id = save_example(
                campaign_no=campaign.campaign_no,
                label=_ref_label,
                narrative=st.session_state.narrative or {},
                saved_by=user_email,
            )
            if _new_id:
                st.success(f"✅ 톤 레퍼런스 저장됨 (#{_new_id}) — 다음 초안부터 톤 가이드와 함께 자동 주입")
            else:
                st.error("저장 실패 — Supabase 연결 또는 narrative_examples 테이블 확인")
