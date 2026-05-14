"""Stable data contract the renderers and AI prompts consume.

The DMP metric schema is scheduled for a major overhaul, so templates must
never read raw column names. Instead, the repository layer maps whatever
the DB currently looks like into these structures.

Shape matches the reference case study (성인영양식_2603_v2.pdf):
  - 3-column results table:  indicator · value · note
  - Insights as a short bulleted list (3 items is the reference count)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricRow:
    """One row of the 04. 캠페인 성과 table."""
    indicator: str     # e.g. "크로스디바이스 도달률"
    value: str         # e.g. "2.2%", "53% 절감", "노출 3.1배 / 도달 1.9배"
    note: str = ""     # e.g. "TV·Mobile 모두 광고 시청"


@dataclass
class CampaignData:
    campaign_no: str
    campaign_name: str                  # 실제 캠페인명 (UI 식별·검색용)
    advertiser: str                     # 실제 광고주명 (UI 식별용)
    industry: str | None = None         # Tier2 카테고리명 (예: "식품", "뷰티") — 마스킹 베이스
    period_start: str | None = None
    period_end: str | None = None
    channel: str | None = None          # CTV / Mobile / Cross-device
    objective: str | None = None

    metrics_table: list[MetricRow] = field(default_factory=list)
    targeting_summary: str | None = None
    audience_insights: list[str] = field(default_factory=list)
    creative_summary: str | None = None

    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def masked_advertiser(self) -> str:
        """리포트 산출물 + AI prompt 에서 사용하는 마스킹 라벨.

        "{Tier2 카테고리명} 광고주" 형태. 예: '식품 광고주', '뷰티 광고주'.
        category 매핑 없으면 'advertiser' 로 fallback (마스킹 실패 시 안전).
        """
        if self.industry:
            return f"{self.industry} 광고주"
        return self.advertiser

    def to_prompt_dict(self) -> dict[str, Any]:
        # AI 가 받는 모든 자리에 실제 브랜드명 노출 금지 — 마스킹 라벨로 대체.
        # 검색·로드 단계 (UI) 에서는 self.advertiser / self.campaign_name 그대로 사용.
        masked = self.masked_advertiser
        return {
            "campaign_no": self.campaign_no,
            "campaign_name": masked,
            "advertiser":   masked,
            "industry":     self.industry,
            "period": f"{self.period_start or '?'} ~ {self.period_end or '?'}",
            "channel": self.channel,
            "objective": self.objective,
            "metrics_table": [
                {"indicator": m.indicator, "value": m.value, "note": m.note}
                for m in self.metrics_table
            ],
            "targeting_summary": self.targeting_summary,
            "audience_insights": self.audience_insights,
            "creative_summary": self.creative_summary,
            "extras": self.extras,
        }
