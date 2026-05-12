"""Narrative generation via Anthropic Claude (Sonnet 4.6).

Migrated from Gemini Pro because Claude follows the prose-primary /
DB-supplementary priority rule more faithfully — the rule is the load-
bearing requirement of this flow.

Sources of truth, in priority order:
  1. PROSE — user-typed Korean prose describing the campaign. 1차 사실.
  2. DB    — structured campaign payload from Supabase. 보조용.

Caching strategy (2 ephemeral breakpoints):
  • system block            — frozen across all campaigns (rule + spec)
  • messages[0].content[0]  — frozen per campaign (DB JSON)
  • messages[0].content[1]  — volatile per prose edit (NOT cached)
Render order is system → messages, so on a prose-only edit the cache
walks back and hits system+DB. On a campaign switch it still hits system.

Output shape matches the reference layout (성인영양식_2603_v2.pdf):
  summary / overview / background / strategy + insights (list[str], len 3)
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import anthropic

from config import load_settings


NARRATIVE_SECTIONS = [
    ("summary", "요약"),
    ("overview", "01. 캠페인 개요"),
    ("background", "02. 광고 집행 배경"),
    ("strategy", "03. 적용 전략 · 핵심 성과"),
]

INSIGHTS_KEY = "insights"
INSIGHTS_LABEL = "04. 인사이트 (불릿)"
TLDR_KEY = "tldr"
TLDR_LABEL = "TL;DR (헤더 옆 3 항목)"


SYSTEM_PROMPT = """당신은 국내 광고/미디어 대행사(모티브인텔리전스)의 시니어 전략 플래너입니다.
사내 1페이지 케이스스터디 (증권사 리서치 노트 톤 + 마케팅 인포그래픽 톤의 중간) 의
초안을 작성합니다.

[작성 규칙]
1. 다음 두 출처를 모두 참고하되, **[캠페인 컨텍스트 — 1차 사실]을 사실의 기준으로 삼습니다.**
   - [캠페인 컨텍스트]에 명시된 숫자·기간·타게팅·전략·결과는 그대로 따릅니다.
   - [DB 보조 데이터]는 [캠페인 컨텍스트]와 모순되지 않을 때만 사용합니다. 충돌 시 [캠페인 컨텍스트]를 따릅니다.
   - [캠페인 컨텍스트]에 없지만 DB에 있는 사실은, [캠페인 컨텍스트]의 주장을 강화할 때만 인용합니다.
   - 둘 다 침묵하는 부분은 추측·창작 금지. 해당 필드는 빈 문자열 또는 빈 리스트.
2. 톤: 세일즈 목적. 독자(잠재 광고주)가 "여기에 의뢰하면 우리도 성공하겠다"고 느끼게.
   과장·수식어 남발 금지. 숫자·타게팅·오디언스가 근거여야 합니다.
3. 문체: 평서문, 한국어, 경어체 종결("~했습니다", "~입니다").
4. 길이 (A4 1페이지 — 엄격):
   tldr       정확히 3개 항목. 각 한국어 30~45자 단문.
              헤더 옆 3분할 박스에 들어감. 핵심 사실 1개씩.
              예: "구매 성장률 +358.3% — 시즌 효과 입증"
              나쁜 예: 동어반복, 형용사 가득한 문장
   summary    3문장. 캠페인 배경 + 전략 + 대표 성과 1개.
   overview   2~3문장. 브랜드/제품 상황, 캠페인 목표.
   background 2~3문장. 마주했던 과제.
   strategy   3~4문장. CTV/모바일/타게팅 접근 + 핵심 성과를 함께 서술.
              본문 흐름 안에서 차트가 옆에 붙으니 "그림 1·2" 정도 암시 가능.
   insights   정확히 3개 항목, 각 1~2문장 (60~95자).
              "○ 한 줄 학습" 형태. 단락 금지.
5. 출력은 지정된 JSON 스키마만. 설명·머리말·코드펜스 금지.
"""


NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "tldr": {
            "type": "array",
            "items": {"type": "string"},
        },
        "summary": {"type": "string"},
        "overview": {"type": "string"},
        "background": {"type": "string"},
        "strategy": {"type": "string"},
        "insights": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["tldr", "summary", "overview", "background", "strategy", "insights"],
    "additionalProperties": False,
}


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    s = load_settings()
    if not s.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=s.anthropic_api_key)


def generate_narrative(
    campaign_payload: dict[str, Any],
    *,
    campaign_context_prose: str = "",
) -> dict[str, Any]:
    """Generate the 5-section narrative draft.

    Returns a dict with keys summary/overview/background/strategy (str)
    and insights (list[str]). Prose is treated as 1차 사실; DB is supplementary.
    """
    settings = load_settings()

    db_text = (
        "[DB 보조 데이터]\n"
        + json.dumps(campaign_payload, ensure_ascii=False, indent=2, sort_keys=True)
    )

    prose_clean = (campaign_context_prose or "").strip()
    if prose_clean:
        prose_text = (
            "[캠페인 컨텍스트 — 1차 사실]\n"
            + prose_clean
            + "\n\n[지시] 위 [작성 규칙]에 따라 5개 섹션 JSON을 작성하세요."
        )
    else:
        prose_text = (
            "[캠페인 컨텍스트 — 1차 사실]\n"
            "(사용자가 작성하지 않음 — [DB 보조 데이터]를 사실의 기준으로 사용하세요. "
            "DB에 없는 부분은 추측 금지, 빈 문자열로 둡니다.)\n\n"
            "[지시] 위 [작성 규칙]에 따라 5개 섹션 JSON을 작성하세요."
        )

    response = _client().messages.create(
        model=settings.anthropic_text_model,
        max_tokens=2048,
        thinking={"type": "disabled"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": NARRATIVE_SCHEMA},
        },
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": db_text,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": prose_text},
                ],
            }
        ],
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        block_types = [b.type for b in response.content]
        raise RuntimeError(
            f"Claude가 텍스트 블록을 반환하지 않았습니다. "
            f"stop_reason={response.stop_reason!r}, content_types={block_types}"
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"JSON 파싱 실패: {e}\n"
            f"모델 반환 원문 앞 800자:\n{text[:800]}"
        ) from e

    out: dict[str, Any] = {k: str(data.get(k, "")) for k, _ in NARRATIVE_SECTIONS}
    raw_insights = data.get(INSIGHTS_KEY, [])
    out[INSIGHTS_KEY] = (
        [str(x).strip() for x in raw_insights if str(x).strip()][:5]
        if isinstance(raw_insights, list)
        else []
    )
    raw_tldr = data.get(TLDR_KEY, [])
    out[TLDR_KEY] = (
        [str(x).strip() for x in raw_tldr if str(x).strip()][:3]
        if isinstance(raw_tldr, list)
        else []
    )

    if not any(out[k] for k in out if k != INSIGHTS_KEY) and not out[INSIGHTS_KEY]:
        raise RuntimeError(
            "모든 섹션이 비어있습니다. 모델이 예상 키를 반환하지 않은 듯.\n"
            f"받은 키: {list(data.keys())}\n"
            f"원문 앞 800자: {json.dumps(data, ensure_ascii=False)[:800]}"
        )
    return out
