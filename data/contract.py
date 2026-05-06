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
    campaign_name: str
    advertiser: str
    industry: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    channel: str | None = None          # CTV / Mobile / Cross-device
    objective: str | None = None

    metrics_table: list[MetricRow] = field(default_factory=list)
    targeting_summary: str | None = None
    audience_insights: list[str] = field(default_factory=list)
    creative_summary: str | None = None

    # Side-car payload for prompts that want more than the structured
    # fields expose. Keep keys descriptive.
    extras: dict[str, Any] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "campaign_no": self.campaign_no,
            "campaign_name": self.campaign_name,
            "advertiser": self.advertiser,
            "industry": self.industry,
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
