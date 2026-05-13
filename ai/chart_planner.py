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
세로 1단 레이아웃에서 차트는 본문 흐름 안에 [그림 1], [그림 2] 형태로 배치됩니다.
- "performance"      : 메인 성과 차트 (이번 캠페인의 핵심 숫자 1~2개 시각화). 최대 2개.
- "inline_strategy"  : 전략 근거 시각화 (타겟/세그먼트 등). 최대 1개. 보통 생략 가능.

총 0~2개 권장 (3개는 1페이지 압박). 데이터가 정말 충분히 다른 각도일 때만 2개.

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

[메트릭 정의 카탈로그 활용 — 가장 중요]
입력 payload 의 `extras.metric_catalog` 에는 이번 캠페인에 적용된
공식 메트릭 정의가 들어 있습니다. 각 메트릭마다:
  metric_id, display_name, tier1, tier3, description, formula, unit,
  primary_value, views (시장평균 / 광고노출자 / 상대지수 등 sub-views)

**활용 원칙**:
1. **차트 제목·캡션은 metric_catalog 의 display_name 을 그대로 사용**.
   "조회 기여도" 가 카탈로그에 있다면 "조회 비중" / "조회 전환" 같은
   임의 변형 금지.
2. **caption 작성 시 description + formula 를 인용**. 단순히 "X% 였습니다"
   가 아니라 "{formula 요약} → {primary_value}{unit}" 식으로 의미 명시.
3. **multi-view 메트릭은 bar_vertical_pair 1순위 후보**.
   views 안에 market/motiv/relative 가 다 있으면:
     - market.value vs motiv.value 로 bar_vertical_pair (절대 명수 대비)
     - relative.value 를 index_lift 로 (상대지수 vs 100 baseline)
     - 둘 다 가능하면 다른 메트릭에서 하나씩 사용해 다양성 확보.
4. **단, 자릿수 차이가 1000배 이상이면** bar_vertical_pair 가 안 보이니
   donut (점유율) 또는 index_lift 로 대체. metric.unit 이 "지수" 면
   index_lift, "%" 면 donut/bar_horizontal.

[원본 DB 컬럼 추가 활용 — 보조]
`extras.view_row` 에는 DB 의 모든 숫자 컬럼이 있고 `raw_pairs` 에
motiv vs total / pre vs curr 페어가 자동 정리돼 있습니다. 카탈로그에
명시 안 된 보조 비교 (예: 빈도 분포, 코호트 retention) 가 필요할 때 활용.

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
- 차트 총 0~2개 (3개는 1페이지에 안 들어감).
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


def _extract_raw_pairs(extras: dict[str, Any] | None) -> dict[str, Any]:
    """Surface DB-side raw comparison pairs so Claude doesn't have to dig
    through view_row to find them.

    Walks `extras.view_row` and groups columns into two categories:
      - motiv_X / total_X  → "motiv-vs-total"  (광고노출 vs 전체)
      - X_prev  / X_curr   → "pre-vs-curr"     (전월 vs 당월)
      - X_curr  / X_growth (existing %) → exposes the % alongside

    Returns a dict keyed by the shared suffix (e.g. "view_uv", "pur_count").
    Empty dict if view_row is missing or no pairs found.
    """
    if not isinstance(extras, dict):
        return {}
    view = extras.get("view_row") or {}
    if not isinstance(view, dict):
        return {}

    def _as_num(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    pairs: dict[str, Any] = {}

    # motiv_X / total_X 자동 매칭
    for key in view:
        if key.startswith("motiv_"):
            suffix = key[len("motiv_"):]
            counter = "total_" + suffix
            if counter in view:
                m = _as_num(view[key])
                t = _as_num(view[counter])
                if m is not None and t is not None and t > 0:
                    pairs.setdefault(suffix, {})
                    pairs[suffix]["motiv"] = m
                    pairs[suffix]["total"] = t
                    pairs[suffix]["share_pct"] = round(m / t * 100, 2)

    # X_prev / X_curr 자동 매칭 (DMP 가 노출하는 시계열 페어)
    for key in view:
        if key.endswith("_prev"):
            base = key[:-5]
            curr_key = base + "_curr"
            if curr_key in view:
                p = _as_num(view[key])
                c = _as_num(view[curr_key])
                if p is not None and c is not None:
                    pairs.setdefault(base, {})
                    pairs[base]["prev"] = p
                    pairs[base]["curr"] = c
                    if p > 0:
                        pairs[base]["growth_pct"] = round((c - p) / p * 100, 2)

    return pairs


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

    raw_pairs = _extract_raw_pairs(campaign_payload.get("extras"))
    catalog = (campaign_payload.get("extras") or {}).get("metric_catalog") or []
    catalog_block = (
        "[메트릭 카탈로그 — 공식 정의 + 현 값. 차트 제목·캡션 1순위 출처]\n"
        + json.dumps(catalog, ensure_ascii=False, indent=2)
        if catalog else
        "[메트릭 카탈로그]\n(비어있음 — metric_definitions 테이블 미연결 또는 미적용)"
    )
    db_block = (
        "[DB 보조 데이터 — 카탈로그에 없는 raw 컬럼 활용용]\n"
        + json.dumps(campaign_payload, ensure_ascii=False, indent=2, sort_keys=True)
    )
    pairs_block = (
        "[DB 원본 비교 페어 — 차트 보조 재료]\n"
        + json.dumps(raw_pairs, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n(motiv vs total 또는 prev vs curr 페어. 카탈로그에 없는 추가 비교 필요 시 활용.)"
        if raw_pairs else "[DB 원본 비교 페어]\n(추출된 페어 없음)"
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
        f"{catalog_block}\n\n{db_block}\n\n{pairs_block}\n\n{nar_block}\n\n{prose_block}\n\n"
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
        if len(out) >= 2:
            break       # hard cap — 1-col layout can't fit a 3rd chart
    return out


# ────────────────────────────────────────────────────────────
# Candidate mode — propose 4~5 charts for the user to pick from
# ────────────────────────────────────────────────────────────
CANDIDATE_PROMPT_SUFFIX = """
[모드 변경 — 후보 제시]
지금은 final 픽을 골라서 반환하지 마세요. 이번 캠페인 데이터로 만들 수
있는 **다양한 각도**의 차트 후보를 **4~5개** 제시합니다. 사용자가 UI
에서 직접 0~2개를 골라 빌드에 포함합니다.

선택 다양성 원칙:
- 같은 메트릭을 다른 템플릿으로 한 번 더 보여주는 것은 OK (예: 성장률을
  bar_horizontal vs bar_vertical_pair).
- 하지만 데이터가 빈약한 후보 (값 1개로 만든 차트, 모호한 비교) 는 금지.
- 각 후보 caption 은 그 차트가 캠페인 어떤 부분을 입증하는지 1줄로 명시.

placement quota 무시 — 모두 "performance" 로 두어도 됩니다.
"""


def _validate_candidates(items: list[Any]) -> list[dict[str, Any]]:
    """Like _validate() but skips placement quotas and caps at 5."""
    out: list[dict[str, Any]] = []
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
        out.append({
            "template": tpl,
            "placement": plc,
            "title":    str(item.get("title") or ""),
            "subtitle": str(item.get("subtitle") or ""),
            "caption":  str(item.get("caption") or ""),
            "data":     data,
        })
        if len(out) >= 5:
            break
    return out


def plan_chart_candidates(
    campaign_payload: dict[str, Any],
    narrative: dict[str, Any],
    *,
    campaign_context_prose: str = "",
    user_instruction: str = "",
    debug: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Propose up to 5 chart candidates for user selection in the UI.

    Differs from plan_charts():
      - Asks Claude for diverse candidates instead of final picks
      - No placement quotas
      - Includes optional `user_instruction` as a steering directive
        ("X 데이터로 도넛을 추가", "성장률 비교는 빼고 가치지수 위주" etc.)
    """
    settings = load_settings()

    raw_pairs   = _extract_raw_pairs(campaign_payload.get("extras"))
    catalog     = (campaign_payload.get("extras") or {}).get("metric_catalog") or []
    catalog_block = (
        "[메트릭 카탈로그 — 공식 정의 + 현 값. 차트 제목·캡션 1순위 출처]\n"
        + json.dumps(catalog, ensure_ascii=False, indent=2)
        if catalog else
        "[메트릭 카탈로그]\n(비어있음)"
    )
    db_block    = "[DB 보조 데이터]\n" + json.dumps(campaign_payload, ensure_ascii=False, indent=2, sort_keys=True)
    pairs_block = (
        "[DB 원본 비교 페어 — 차트 보조 재료]\n"
        + json.dumps(raw_pairs, ensure_ascii=False, indent=2, sort_keys=True)
        if raw_pairs else "[DB 원본 비교 페어]\n(추출 페어 없음)"
    )
    nar_block   = "[내러티브 초안]\n" + json.dumps(narrative, ensure_ascii=False, indent=2)
    prose_clean = (campaign_context_prose or "").strip()
    prose_block = "[캠페인 컨텍스트 — 1차 사실]\n" + (prose_clean or "(없음)")
    instr_clean = (user_instruction or "").strip()
    instr_block = (
        "\n\n[사용자 지시 — 절대 따라야 함]\n" + instr_clean
        if instr_clean else ""
    )

    user_text = (
        f"{catalog_block}\n\n{db_block}\n\n{pairs_block}\n\n{nar_block}\n\n{prose_block}{instr_block}\n\n"
        "[지시] 다양한 각도의 차트 후보 4~5개를 JSON 으로 반환하세요. "
        "메트릭 카탈로그가 있으면 그 메트릭의 display_name 을 차트 제목에 그대로 사용. "
        "다중 view 메트릭은 bar_vertical_pair (market vs motiv) 와 index_lift (relative) 둘 다 후보로 제시 가능."
        f"{JSON_RETURN_HINT}"
    )

    try:
        response = _client().messages.create(
            model=settings.anthropic_text_model,
            max_tokens=3072,
            thinking={"type": "disabled"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT + CANDIDATE_PROMPT_SUFFIX,
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
        debug["raw"] = (text or "")[:800]
    if not text:
        if debug is not None:
            debug.update(reason="no_text", detail="", picked=0)
        return []
    try:
        data = json.loads(_strip_codefence(text))
    except json.JSONDecodeError as e:
        if debug is not None:
            debug.update(reason="json_error", detail=str(e), picked=0)
        return []

    raw = data.get("charts") or []
    if not isinstance(raw, list):
        return []
    validated = _validate_candidates(raw)
    if debug is not None:
        debug.update(reason="validated", detail=f"claude={len(raw)} validated={len(validated)}", picked=len(raw))
    return validated


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
