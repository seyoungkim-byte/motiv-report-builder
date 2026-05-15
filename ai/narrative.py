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

[★ 브랜드 익명화 — 가장 중요한 규칙]
출력 본문의 어디에도 **실제 광고주명·경쟁사명·브랜드명·제품명을 사용하지 마세요.**
입력 (캠페인 컨텍스트, DB 페이로드) 에서 발견되는 모든 고유명사는 다음과 같이
익명 라벨로 치환합니다:

  실제 브랜드 → 익명 라벨 예시
  ----------------------------------------------------------
  자사 광고주          → "광고주" / 또는 "{industry} 광고주" (industry 값 사용)
  자사 제품/브랜드     → "자사 브랜드" / "주력 제품" / "캠페인 제품군"
  경쟁사 1개           → "경쟁사" / "주요 경쟁사"
  경쟁사 여러 곳       → "경쟁사 A·B" / "주요 경쟁사 2~3곳"
  특정 매체사·플랫폼   → "주요 미디어" / "OTT 매체"

전략의 핵심 (예: "자체 매체 활용 불가, 경쟁사 사용자 정밀 타겟팅") 은 그대로
유지하되, 식별 가능한 이름만 익명화. 숫자·기간·타게팅 로직은 변경 금지.

좋은 예: "{industry} 광고주가 DMP 기반 경쟁사 사용자 정밀 타겟팅과 CTV 반복
        노출(평균 6.7회)로 통신사 스위칭을 직접 견인했습니다."
나쁜 예: "KT 유선 인터넷이 DMP 기반 경쟁사(SKB·LGU+) 사용자 정밀 타겟팅과..."
        (실 브랜드 KT/SKB/LGU+ 노출 — 절대 금지)

[작성 규칙]
1. 사실 출처 우선순위:
   - **[캠페인 컨텍스트 — 1차 사실]을 사실의 기준으로 삼습니다.**
   - [DB 보조 데이터]는 컨텍스트와 모순되지 않을 때만 사용. 충돌 시 컨텍스트 우선.
   - [캠페인 컨텍스트]에 없지만 DB에 있는 사실은, 컨텍스트의 주장을 강화할 때만 인용.
   - 둘 다 침묵하는 부분은 추측·창작 금지. 해당 필드는 빈 문자열 또는 빈 리스트.
2. 톤: 세일즈 목적. 독자(잠재 광고주)가 "여기 의뢰하면 우리도 성공하겠다"고 느끼게.
   과장·수식어 남발 금지. 숫자·타게팅·오디언스가 근거여야 합니다.
3. 문체: 한국어, 경어체 종결("~했습니다", "~입니다").
   **만연체 금지** — 한투/KB 리서치 노트처럼 **불릿 위주의 간결한 단문**.
4. 길이 + 형식 (A4 1페이지 — 엄격):
   tldr        list[str], 정확히 3개. 각 30~45자 단문.
               헤더 옆 3분할 박스용. 핵심 사실 1개씩.
               예: "구매 성장률 +358.3% — 시즌 효과 입증"
   summary     str. 1~2문장 (80~140자). 캠페인 배경 + 전략 + 대표 성과 압축.
               요약 박스 (앵커) — 본문 진입 전 한 호흡으로 읽히게.
   overview    list[str], 2~3개 불릿. 각 35~55자.
               브랜드/캠페인 기간/목표 등 사실 위주. **단문, 만연체 금지.**
               좋은 예: "린트 — 유럽 프리미엄 초콜릿 하이엔드 브랜드"
                       "캠페인 기간: 2026.01.18 ~ 02.14 (27일)"
                       "목표: 발렌타인 시즌 고가치 소비자 도달 + 구매 전환"
   background  list[str], 2~3개 불릿. 각 35~60자.
               마주한 과제·시장 환경 사실 단문.
   strategy    list[str], 3~4개 불릿. 각 35~65자.
               타게팅 / 미디어 / 빈도 / 측정 등 접근법.
               성과 숫자가 있으면 한 불릿 안에 포함 ("→ +358% 성장" 같은 형태).
   insights    list[str], 정확히 3개. 각 1~2문장 (60~95자).
               "○ 한 줄 학습" 형태. 단락 금지.
5. 출력은 지정된 JSON 스키마만. 설명·머리말·코드펜스 금지.
"""


NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "tldr":       {"type": "array", "items": {"type": "string"}},
        "summary":    {"type": "string"},
        "overview":   {"type": "array", "items": {"type": "string"}},
        "background": {"type": "array", "items": {"type": "string"}},
        "strategy":   {"type": "array", "items": {"type": "string"}},
        "insights":   {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tldr", "summary", "overview", "background", "strategy", "insights"],
    "additionalProperties": False,
}


# Sections that are now bullet lists (overview / background / strategy).
# summary stays as a single paragraph anchor.
BULLET_SECTIONS = {"overview", "background", "strategy"}


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

    def _coerce_bullets(raw: Any, cap: int) -> list[str]:
        """list 면 그대로(공백 제거 + 길이 cap), str 이면 줄 단위로 쪼개서 bullets 화.
        모델이 가끔 옛 paragraph 형식을 반환해도 깨지지 않게."""
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()][:cap]
        if isinstance(raw, str):
            return [line.strip() for line in raw.splitlines() if line.strip()][:cap]
        return []

    out: dict[str, Any] = {}
    for key, _ in NARRATIVE_SECTIONS:
        raw_val = data.get(key)
        if key in BULLET_SECTIONS:
            out[key] = _coerce_bullets(raw_val, 5)
        else:
            out[key] = str(raw_val or "")

    out[INSIGHTS_KEY] = _coerce_bullets(data.get(INSIGHTS_KEY), 5)
    out[TLDR_KEY]     = _coerce_bullets(data.get(TLDR_KEY), 3)

    # Sanity: at least one field non-empty
    if not any(out.values()):
        raise RuntimeError(
            "모든 섹션이 비어있습니다. 모델이 예상 키를 반환하지 않은 듯.\n"
            f"받은 키: {list(data.keys())}\n"
            f"원문 앞 800자: {json.dumps(data, ensure_ascii=False)[:800]}"
        )
    return out
