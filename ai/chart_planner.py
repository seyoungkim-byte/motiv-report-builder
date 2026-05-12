"""Claude-driven chart selection.

Given a campaign payload + narrative draft + raw prose, Claude picks
up to 3 chart templates from `viz.templates.TEMPLATE_NAMES`, fills in
the data, and assigns each a layout placement. The build pipeline then
calls `viz.render_chart(template, spec)` for each entry and inlines the
PNGs as data URIs.

Why a planner (vs. hard-coded charts):
  Every campaign has a different "story" — a brand lift campaign cares
  about index_lift; a full-funnel push wants funnel + freq_distribution;
  a B2B awareness piece may not need any chart at all. The planner lets
  Claude make this call after reading the narrative it just wrote, so
  charts and prose stay in sync.

Hard limits:
  - 0~3 charts. Empty list is a valid result (= "data doesn't support a
    visual, leave the metrics table in place").
  - Numbers must come from the supplied DB/prose. The planner is
    prompted to refuse fabrication.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import anthropic

from config import load_settings
from viz import TEMPLATE_NAMES


# Placements understood by the renderer. Two zones:
#   performance       → right column, replaces / augments 04 metrics table.
#                       Up to 2 charts stack here. Medium size.
#   inline_strategy   → small chart floated next to 03 strategy text.
#                       At most 1. Small size.
PLACEMENT_VALUES = ["performance", "inline_strategy"]


SYSTEM_PROMPT = """당신은 데이터 시각화 큐레이터입니다.
주어진 캠페인 데이터와 내러티브를 읽고, 1페이지 케이스스터디(A4)에 들어갈 차트 0~3개를 선택합니다.

[원칙]
1. 데이터가 차트를 지지해야 합니다. DB나 캠페인 컨텍스트에 명시된 숫자만 사용하세요. 추측·평균·가공 금지.
2. 숫자가 빈약하거나(2개 미만 카테고리, 비교 대상 없음 등) 내러티브가 정성적이면 0개 반환합니다. 억지로 채우지 말 것.
3. 차트는 인접 텍스트를 보강해야 합니다. 텍스트와 따로 노는 차트는 절대 금지 — 공공기관 실태조사·이마케터 리포트 톤.
4. 동일 메트릭을 두 차트로 중복 노출하지 마세요. 차트마다 다른 각도를 보여야 합니다.
5. 한국어 라벨, 경어체 캡션.

[배치]
- "performance" : 우측 컬럼의 04 영역. 최대 2개. 캠페인 대표 성과 차트.
- "inline_strategy" : 03(적용 전략) 텍스트 옆 작은 차트. 최대 1개. 전략 근거 시각화 (예: 타겟 세그먼트 분포).

[템플릿]
- bar_horizontal     : 카테고리별 값 비교 (전환률·CTR·VTR 등). 승자 1개 강조. 4~6 항목 권장.
                       data = {labels, values, value_format, highlight_idx}
- bar_vertical_pair  : 두 그룹 비교 (대조군 vs 광고노출, 전월 vs 당월).
                       data = {categories, series_a:{label,values}, series_b:{label,values}, value_format}
- donut              : 비중/구성비. 5 슬라이스 이하 (그 이상은 "기타"로 묶기).
                       data = {labels, values, highlight_idx, center_label}
- funnel             : 풀퍼널 단계 (노출→도달→클릭→전환 등). 3~5 단계.
                       data = {stages:[{name,value}...], value_format, show_drop}
- index_lift         : 단일 강조 — 대조군 100 vs 광고 노출그룹 index.
                       data = {label, index, baseline_label, note}
- freq_distribution  : 빈도 구간별 분포 (1회/2회/3-4회/5+).
                       data = {buckets:[{name,value,share}...], total_label}

[value_format 작성 규칙 — 중요]
**Python str.format 패턴만 사용**. Excel/한글 패턴(#,##0 / 0.0% / +0.0% 등) 금지.
좋은 예:
  "{:+,.1f}%"      → +152.5%
  "{:+.1f}p"       → +20.0p
  "{:,.0f}명"       → 565,772명
  "{:,.0f}원"       → 4,360원
  "{:.2f}%"        → 0.27%
나쁜 예:
  "+#,##0.0%"      → Excel 코드, Python 이 처리 못 함
  "+0.0%"          → Excel 코드, 잘못된 출력
  "{value}%"       → 이름 있는 placeholder 금지, positional 만 사용

[출력 스키마]
charts: list. 각 항목:
  template   : 위 6개 중 하나
  placement  : "performance" 또는 "inline_strategy"
  title      : 짧은 한국어 (≤ 18자)
  subtitle   : 단위·기간 등 (선택, 빈 문자열 가능)
  data       : 위 템플릿별 스키마 그대로
  caption    : 차트 아래 한 줄 (≤ 45자, 경어체)

[caption 작성 규칙 — 중요]
1. 내러티브 문장을 그대로 베끼지 마세요. 차트의 **숫자 1개를 명시**하고
   그 숫자가 의미하는 바를 1줄로 압축합니다.
2. 인접 텍스트(narrative.strategy / narrative.insights[N]) 의 주장을
   **시각적으로 입증한다**는 다리 역할이어야 합니다. 동어반복 금지.
3. 좋은 예: "광고 노출자 구매 성장률 +358.3%로 시즌 효과를 입증" (40자)
   나쁜 예: "구매 성장률이 358.3%였습니다" (단순 사실 나열, 의미 없음)
   나쁜 예: narrative.insights[0] 의 문장 첫 절을 그대로 따옴

[제약]
- performance ≤ 2, inline_strategy ≤ 1.
- 차트 총 0~3개.
- 데이터가 부족하면 빈 리스트 반환: {"charts": []}.
- 코드펜스·설명·머리말 금지. JSON 만.
"""


# We previously used Anthropic's strict json_schema mode for the chart
# planner, but it rejects every workaround we needed for the `data`
# field (which has 6 different shapes — one per chart template):
#   1) additionalProperties: false → forbids template-specific keys
#   2) untyped {} → "Empty schema not supported"
#   3) oneOf with per-template schemas → still requires every leaf to be
#      strict, blowing up the schema for optional fields
# Simpler path: drop json_schema, ask Claude for a JSON object via prompt,
# strip code fences, parse with json.loads, and rely on `_data_looks_valid`
# to discard malformed entries. Sonnet 4.6 follows the schema in the
# system prompt reliably without strict mode.
JSON_RETURN_HINT = (
    "\n[출력 형식] 다음 형태의 JSON 객체만 반환 (코드펜스·설명 금지):\n"
    "{ \"charts\": [ { \"template\": ..., \"placement\": ..., \"title\": ..., "
    "\"subtitle\": \"\", \"caption\": ..., \"data\": { 템플릿별 키 } }, ... ] }"
)


def _strip_codefence(text: str) -> str:
    """Tolerate `````json … `````, ```` ``` … ``` ````, or bare-JSON returns.
    Claude is told not to wrap in fences but occasionally does anyway."""
    s = text.strip()
    if s.startswith("```"):
        # drop opening fence (with optional language) and closing fence
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    s = load_settings()
    if not s.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=s.anthropic_api_key)


def plan_charts(
    campaign_payload: dict[str, Any],
    narrative: dict[str, Any],
    *,
    campaign_context_prose: str = "",
    debug: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Ask Claude to pick 0~3 charts. Returns list of validated specs.

    Each returned dict has shape:
      {template, placement, title, subtitle, caption, data}

    Invalid items (unknown template, malformed data) are filtered out
    silently — the build still succeeds with whatever survives.

    `debug` (optional): caller can pass an empty dict; we populate it with
      reason  (str) — "api_error" / "no_text" / "json_error" / "validated"
      detail (str) — error message or short summary
      raw    (str) — first 600 chars of Claude's text response when present
      picked (int) — number of items returned by Claude before validation
    """
    settings = load_settings()

    db_block = (
        "[DB 보조 데이터]\n"
        + json.dumps(campaign_payload, ensure_ascii=False, indent=2, sort_keys=True)
    )
    nar_block = (
        "[내러티브 초안]\n"
        + json.dumps(narrative, ensure_ascii=False, indent=2)
    )
    prose_clean = (campaign_context_prose or "").strip()
    prose_block = (
        "[캠페인 컨텍스트 — 1차 사실]\n" + (prose_clean or "(없음)")
    )

    user_text = (
        f"{db_block}\n\n{nar_block}\n\n{prose_block}\n\n"
        "[지시] 위 [원칙]에 따라 0~3개 차트를 선택해 JSON으로 반환하세요."
        f"{JSON_RETURN_HINT}"
    )

    try:
        response = _client().messages.create(
            model=settings.anthropic_text_model,
            max_tokens=2048,
            thinking={"type": "disabled"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        )
    except Exception as e:
        if debug is not None:
            debug.update(reason="api_error", detail=f"{type(e).__name__}: {e}", picked=0)
        return []

    text = next((b.text for b in response.content if b.type == "text"), "")
    if debug is not None:
        debug["raw"] = (text or "")[:600]
    if not text:
        if debug is not None:
            debug.update(
                reason="no_text",
                detail=f"stop_reason={response.stop_reason!r} types={[b.type for b in response.content]}",
                picked=0,
            )
        return []
    try:
        data = json.loads(_strip_codefence(text))
    except json.JSONDecodeError as e:
        if debug is not None:
            debug.update(reason="json_error", detail=str(e), picked=0)
        return []

    raw = data.get("charts") or []
    if not isinstance(raw, list):
        if debug is not None:
            debug.update(reason="charts_not_list", detail=f"got {type(raw).__name__}", picked=0)
        return []

    validated = _validate(raw)
    if debug is not None:
        debug.update(reason="validated", detail=f"claude={len(raw)} validated={len(validated)}", picked=len(raw))
    return validated


def _validate(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    perf_count = 0
    inline_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        tpl = item.get("template")
        plc = item.get("placement")
        if tpl not in TEMPLATE_NAMES:
            continue
        if plc not in PLACEMENT_VALUES:
            plc = "performance"
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        if not _data_looks_valid(tpl, data):
            continue
        # enforce placement quotas
        if plc == "performance":
            if perf_count >= 2:
                continue
            perf_count += 1
        else:
            if inline_count >= 1:
                continue
            inline_count += 1
        out.append({
            "template": tpl,
            "placement": plc,
            "title":    str(item.get("title") or ""),
            "subtitle": str(item.get("subtitle") or ""),
            "caption":  str(item.get("caption") or ""),
            "data":     data,
        })
        if len(out) >= 3:
            break
    return out


def _data_looks_valid(template: str, data: dict[str, Any]) -> bool:
    """Cheap structural check — bail on obviously malformed payloads
    so the renderer doesn't crash mid-build."""
    try:
        if template == "bar_horizontal":
            return (
                isinstance(data.get("labels"), list)
                and isinstance(data.get("values"), list)
                and len(data["labels"]) == len(data["values"]) >= 2
            )
        if template == "bar_vertical_pair":
            a = data.get("series_a") or {}
            b = data.get("series_b") or {}
            cats = data.get("categories") or []
            return (
                isinstance(cats, list) and len(cats) >= 1
                and isinstance(a.get("values"), list)
                and isinstance(b.get("values"), list)
                and len(a["values"]) == len(b["values"]) == len(cats)
            )
        if template == "donut":
            return (
                isinstance(data.get("labels"), list)
                and isinstance(data.get("values"), list)
                and len(data["labels"]) == len(data["values"]) >= 2
            )
        if template == "funnel":
            stages = data.get("stages") or []
            return (
                isinstance(stages, list) and len(stages) >= 2
                and all(isinstance(s, dict) and "name" in s and "value" in s for s in stages)
            )
        if template == "index_lift":
            return "index" in data
        if template == "freq_distribution":
            buckets = data.get("buckets") or []
            return (
                isinstance(buckets, list) and len(buckets) >= 2
                and all(isinstance(b, dict) and "name" in b for b in buckets)
            )
    except Exception:
        return False
    return False
