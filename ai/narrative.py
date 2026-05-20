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
    ("overview", "01. 캠페인 목적"),
    ("background", "02. 광고 집행 배경"),
    ("strategy", "03. 적용 전략"),
]

INSIGHTS_KEY = "insights"
INSIGHTS_LABEL = "05. 인사이트 (Motiv's Value)"
# TL;DR 은 v3 레이아웃에서 미사용. 옛 빌드 호환을 위해 키만 유지 — UI 노출 안 함.
TLDR_KEY = "tldr"
TLDR_LABEL = "TL;DR (사용 안 함 — 호환용)"


SYSTEM_PROMPT = """당신은 국내 광고/미디어 대행사(모티브인텔리전스)의 시니어 전략 플래너입니다.
사내 1페이지 케이스스터디 (모티브가 어떤 솔루션을 적용해 어떤 성과를 냈는지 보여주는
세일즈 자료) 의 초안을 작성합니다. 톤은 사람이 읽기 좋게 자연스럽게 — AI 리뷰 톤
(과장·수식어 남발·기계적 나열) 절대 금지.

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

[★ 메시지 주체 규칙 — 매우 중요]
- 03 적용 전략의 주어는 반드시 **"모티브인텔리전스는 ..."** 으로 시작합니다.
  광고주가 한 일이 아니라 **모티브가 적용한 솔루션** 을 명시.
- 05 인사이트는 데이터 해석(수치 paraphrase) 이 아니라 **모티브 솔루션의 가치** 를
  설명. "X 전략은 ~핵심 수단입니다", "Y 분석은 ~기반이 됩니다" 식.
- 타게팅 묘사는 모티브 세그먼트로 표현 (예: "고소득자 비중이 높고 제품 관심도가
  높은 수도권·부산 거주 인구 중 시즌 디저트 구매 경향이 높은 연령대").
- 일자(YYYY-MM-DD)나 DMP·CrossTarget TV 같은 시스템·제품명 본문에 노출 금지.
  집행 기간은 월 단위, 매체는 헤더 메타에서만 표시.
- 동일 지표 용어는 한 리포트 내에서 통일 ('구매 기여도'·'브랜드 관심도 지수'·
  '구매 의향 지수'·'시장 점유율 증감' 표준).

[작성 규칙]
1. 사실 출처 우선순위:
   - **[캠페인 컨텍스트 — 1차 사실]을 사실의 기준으로 삼습니다.**
   - [DB 보조 데이터]는 컨텍스트와 모순되지 않을 때만 사용. 충돌 시 컨텍스트 우선.
   - [캠페인 컨텍스트]에 없지만 DB에 있는 사실은, 컨텍스트의 주장을 강화할 때만 인용.
   - 둘 다 침묵하는 부분은 추측·창작 금지. 해당 필드는 빈 문자열 또는 빈 리스트.
2. 톤: 세일즈 목적. 잠재 광고주가 "여기 의뢰하면 우리도 성공하겠다" 고 느끼게.
   과장·수식어 남발 금지 ('폭발적', '압도적', '획기적' 등 금지). 숫자·타게팅·오디언스
   가 근거여야 합니다.
3. 문체: 한국어, 경어체 종결("~했습니다", "~입니다").
   **만연체 금지** — 단문 위주, 자연스러운 호흡.
4. 강조 마크업 (★ 신규):
   본문 내 핵심 phrase 는 **마크다운 굵게 표기** (`**텍스트**`) 로 감싸세요.
   템플릿이 자동으로 olive 하이라이터 마커 효과로 변환합니다.

   - 한 섹션당 1~2개 phrase 만 강조. 너무 많으면 강조 효과 사라짐.
   - 강조 대상 = 모티브 솔루션의 차별점·핵심 타게팅·결정적 수치.
     좋은 예: "**고소득자 비중이 높고 제품 관심도가 높은 수도권·부산 거주 인구**
              중 시즌 디저트 구매 경향이 높은 연령대를 정밀 세그먼트로 추출"
              "시장 점유율을 **+46.8%** 끌어올리며, 경쟁 집중 구간에서 브랜드
              입지를 견고히 다졌습니다."
     나쁜 예: 모든 문장에 `**` 도배. 일반 명사·관용구 강조.

5. 길이 + 형식 (A4 1페이지 — 엄격):
   tldr        list[str], **빈 리스트 [] 로 비워두세요.** v3 레이아웃에서 미사용.
   summary     str. 2~3문장 (120~200자). 모티브가 어떤 솔루션을 적용해 어떤 성과를
               냈는지 압축. 주어는 "모티브인텔리전스는" 으로 시작.
   overview    list[str], 2~3개 불릿. 각 35~60자.
               광고주가 직면한 시장·KPI·목표 (모티브 관점에서 정의).
               좋은 예: "광고주는 카테고리 최성수기 진입을 앞두고, 단순 노출 확보가
                        아닌 시장 점유율 확장을 핵심 목표로 설정"
   background  list[str], 2~3개 불릿. 각 35~60자.
               광고주가 마주한 한계·과제 (모티브가 어떤 문제를 해결할지 도입부).
               좋은 예: "경쟁 마케팅이 집중되는 단기 구간, 광고 물량 확대만으로
                        차별화 어려움"
                       "광고 노출과 실제 구매 간 연결을 검증할 측정 기반 부재"
   strategy    list[str], 정확히 4개.
               - 첫 항목 (lead-in): 모티브가 적용한 솔루션 한 문장.
                 예: "모티브인텔리전스는 시즌 수요 곡선에 맞춘 정밀 타게팅
                      솔루션을 적용했습니다."
               - 나머지 3개: 모티브가 한 구체 액션. 각 40~80자.
                 타게팅 / 미디어 운영 / 측정 접근 — 한 항목 한 액션.
                 좋은 예: "고소득자 비중이 높고 제품 관심도가 높은 수도권·부산
                          거주 인구 중 시즌 구매 경향이 높은 연령대를 정밀
                          세그먼트로 추출"
                         "시즌 수요 곡선에 맞춰 CTV 노출량을 스케일업하고,
                          프리미엄 대화면 가구에 집중 도달"
                         "오프라인·온라인 결제 데이터를 매칭한 풀퍼널 측정으로
                          실제 구매 전환까지 정량 검증"
   insights    list[str], 정확히 3개. 각 1~2문장 (70~110자).
               **모티브 솔루션의 가치 (Motiv's Value) 를 설명.** 데이터 paraphrase
               금지. 'X대비 Y배' 같은 수치 해석은 04 표의 비고 칸 몫.
               좋은 예: "CTV는 시즌 단기 집중기에 브랜드 메시지를 효율적으로
                        전달하는 채널이며, 고관여 카테고리에서 시장 점유율 확장의
                        핵심 수단으로 기능합니다."
                       "고소득·제품 관심도 기반 정밀 세그먼트 타게팅은 경쟁이
                        집중되는 시기에 광고 효율을 극대화하는 모티브의 솔루션
                        입니다."
                       "광고 노출과 실제 구매 데이터를 결합한 풀퍼널 측정은 매출
                        기여도를 정량적으로 검증하고, 다음 캠페인 전략 수립의
                        기반이 됩니다."
               나쁜 예: "퍼널 기여도가 X→Y→Z 순으로 상승, 점진 상승 구조 확인"
                       (← 표/그래프가 이미 보여준 내용을 글로 또 반복)
6. 출력은 지정된 JSON 스키마만. 설명·머리말·코드펜스 금지.
"""


NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "tldr":       {"type": "array", "items": {"type": "string"}},
        "summary":    {"type": "string"},
        "overview":   {"type": "array", "items": {"type": "string"}},
        "background": {"type": "array", "items": {"type": "string"}},
        # 03 적용 전략 — list[str]. 첫 항목은 모티브 lead-in 한 문장,
        # 나머지는 모티브 액션 bullets. 템플릿에서 첫 항목은 단락으로,
        # 나머지는 ▪ 불릿으로 렌더.
        "strategy":   {"type": "array", "items": {"type": "string"}},
        "insights":   {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tldr", "summary", "overview", "background", "strategy", "insights"],
    "additionalProperties": False,
}


# 불릿 리스트로 렌더할 섹션. strategy 는 별도 처리:
# 첫 항목 = paragraph (lead-in), 나머지 = bullets.
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
    extra_analysis: str = "",
    tone_guide: str = "",
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
    extra_clean = (extra_analysis or "").strip()

    parts: list[str] = []
    if prose_clean:
        parts.append("[캠페인 컨텍스트 — 1차 사실]\n" + prose_clean)
    else:
        parts.append(
            "[캠페인 컨텍스트 — 1차 사실]\n"
            "(사용자가 작성하지 않음 — [DB 보조 데이터]를 사실의 기준으로 사용하세요. "
            "DB에 없는 부분은 추측 금지, 빈 문자열로 둡니다.)"
        )
    if extra_clean:
        parts.append(
            "[추가 분석 데이터 — 1차 사실 (사용자 수기 입력)]\n"
            + extra_clean
            + "\n(여기 들어있는 표·수치·발견은 DB 데이터와 동일한 권위로 다룹니다. "
              "요약·인사이트에 자연스럽게 통합해 본문 흐름의 일부로 인용하세요. "
              "단, 실제 광고주/경쟁사 브랜드명은 그대로 노출하지 말고 익명 라벨로 치환.)"
        )
    parts.append("[지시] 위 [작성 규칙]에 따라 6개 필드 (tldr/summary/overview/background/strategy/insights) JSON 을 작성하세요.")
    prose_text = "\n\n".join(parts)

    # tone_guide 는 cache 깨지지 않게 별도 system block 으로 *뒤에* 둔다.
    # 메인 SYSTEM_PROMPT 는 ephemeral cache 그대로 유지, tone block 만 volatile.
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    tone_clean = (tone_guide or "").strip()
    if tone_clean:
        system_blocks.append({
            "type": "text",
            "text": (
                "[★ 사용자 톤 가이드 — 위 규칙과 충돌 시 톤 가이드를 우선]\n"
                "아래 가이드는 모든 캠페인에 공통 적용되는 마케팅 팀 합의 사항입니다.\n"
                "내용·사실은 위 작성 규칙을 따르되, 문장 톤·길이·표현 스타일은 아래를 따르세요.\n\n"
                + tone_clean
            ),
        })

    response = _client().messages.create(
        model=settings.anthropic_text_model,
        max_tokens=2048,
        thinking={"type": "disabled"},
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": NARRATIVE_SCHEMA},
        },
        system=system_blocks,
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
