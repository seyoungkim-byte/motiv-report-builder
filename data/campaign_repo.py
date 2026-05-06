"""Campaign repository — the one place that knows the current DB shape.

Three sources are stitched together here so the rest of the app stays
oblivious:

  1. `campaign_performance_full_report` (existing dashboard view) —
     conversion-funnel metrics keyed on `campaign_no` (which is actually
     a *report* identifier, e.g. "5295" = "KT (26' 02)").
  2. `crosstarget_metrics_raw` (scraped) — TV/CTV ad-delivery metrics
     keyed on `(report_id, campaign_no)`. **`report_id` here ↔ the
     dashboard view's `campaign_no`.** Same identifier, different name.
  3. `crosstarget_ctv_reach_raw` (scraped) — CTV product reach by
     frequency, keyed on `(report_id, product)`. Same `report_id` join.

Coverage differs: the dashboard view only has rows where conversion
tracking is complete (~24 reports as of 2026-04). The scraper has all
reports it's been run against. So a given `campaign_no` may have data
in any combination of the three sources — every step degrades
gracefully.

When the DMP overhaul lands, only this file should need to change.
"""
from __future__ import annotations

import re
from typing import Any

from .contract import CampaignData, MetricRow
from .supabase_client import get_client


TABLE = "campaign_performance_full_report"
SCRAPED_METRICS_TABLE = "crosstarget_metrics_raw"
SCRAPED_CTV_TABLE = "crosstarget_ctv_reach_raw"


# ─────────────────────────────────────────── Brand extraction
def _extract_brand(name: str) -> str:
    """Pull the advertiser/brand prefix out of a campaign_name.

    Handles the two patterns we see in the wild:
      "맥도날드 (26' 03)"               → "맥도날드"
      "[20053] 하이트_테라 캠페인_FAST_..." → "하이트 테라"
    """
    if not name:
        return ""
    cleaned = re.sub(r"^\[\d+\]\s*", "", name).strip()
    m = re.match(r"^(.+?)\s*\(", cleaned)  # "BRAND (...)" form
    if m:
        return m.group(1).strip()
    m = re.match(r"^(.+?)\s*캠페인", cleaned)  # "BRAND_PRODUCT 캠페인_..." form
    if m:
        return m.group(1).replace("_", " ").strip()
    return cleaned.split("_")[0].split(" ")[0]


# ─────────────────────────────────────────── Number formatters
def _fmt_pct(v: Any, signed: bool = False) -> str | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return (f"{f:+.1f}%" if signed else f"{f:.1f}%")


def _fmt_money(v: Any) -> str | None:
    try:
        return f"{int(float(v)):,}원"
    except (TypeError, ValueError):
        return None


def _fmt_count(v: Any, unit: str = "회") -> str | None:
    try:
        return f"{int(float(v)):,}{unit}"
    except (TypeError, ValueError):
        return None


def _fmt_index(v: Any) -> str | None:
    try:
        return f"{float(v):.1f}"
    except (TypeError, ValueError):
        return None


def _add(metrics: list[MetricRow], indicator: str, value: str | None, note: str = ""):
    if value is not None and value != "":
        metrics.append(MetricRow(indicator=indicator, value=value, note=note))


# ─────────────────────────────────────────── Repo
class CampaignRepository:
    def __init__(self):
        self._client = get_client()

    def is_available(self) -> bool:
        return self._client is not None

    def list_campaigns(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self._client:
            return []
        res = (
            self._client.table(TABLE)
            .select("campaign_no,campaign_name,start_date,end_date")
            .order("start_date", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    def search(self, keyword: str, limit: int = 50) -> list[dict[str, Any]]:
        """Case-insensitive partial match on campaign_name. Latest first."""
        if not self._client:
            return []
        safe = re.sub(r"[,()]", " ", (keyword or "").strip())
        if not safe:
            return []
        pattern = f"%{safe}%"
        res = (
            self._client.table(TABLE)
            .select("campaign_no,campaign_name,start_date,end_date")
            .ilike("campaign_name", pattern)
            .order("start_date", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    def get(self, campaign_no: str) -> CampaignData | None:
        """Fetch a single report's full data — view row + scraped extras."""
        if not self._client:
            return None
        res = (
            self._client.table(TABLE)
            .select("*")
            .eq("campaign_no", campaign_no)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None

        scraped_subs = self._fetch_scraped_subs(campaign_no)
        ctv_rows = self._fetch_ctv_reach(campaign_no)
        return self._to_campaign(rows[0], scraped_subs, ctv_rows)

    def _fetch_scraped_subs(self, campaign_no: str) -> list[dict[str, Any]]:
        """Pull all sub-campaigns the scraper recorded for this report."""
        if not self._client:
            return []
        try:
            res = (
                self._client.table(SCRAPED_METRICS_TABLE)
                .select("*")
                .eq("report_id", campaign_no)
                .execute()
            )
            return res.data or []
        except Exception:
            return []  # table may not exist yet (scraper not run)

    def _fetch_ctv_reach(self, campaign_no: str) -> list[dict[str, Any]]:
        """Pull CTV product reach for this report, if scraped."""
        if not self._client:
            return []
        try:
            res = (
                self._client.table(SCRAPED_CTV_TABLE)
                .select("*")
                .eq("report_id", campaign_no)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    @staticmethod
    def _to_campaign(
        row: dict[str, Any],
        scraped_subs: list[dict[str, Any]] | None = None,
        ctv_rows: list[dict[str, Any]] | None = None,
    ) -> CampaignData:
        """Stitch all three sources into one CampaignData with auto-filled metrics."""
        scraped_subs = scraped_subs or []
        ctv_rows = ctv_rows or []
        metrics: list[MetricRow] = []

        # ── Tier 1: Conversion + value (from dashboard view)
        _add(metrics, "구매 기여도",
             _fmt_pct(row.get("pur_contribution")),
             "캠페인 노출자 중 구매 비중")

        _add(metrics, "구매 성장률",
             _fmt_pct(row.get("motiv_pur_growth"), signed=True),
             "광고 노출자 전월 동기 대비")

        _add(metrics, "유저 가치 지수",
             _fmt_index(row.get("user_value_index")),
             "100 = 평균. 광고 노출자 vs 전체 비교")

        _add(metrics, "평균 구매 단가",
             _fmt_money(row.get("motiv_avg_amount")),
             "광고 노출자 평균")

        # ── Tier 2: Reach (motiv vs total)
        motiv_view = row.get("motiv_view_uv")
        total_view = row.get("total_view_uv")
        try:
            if motiv_view and total_view and float(total_view) > 0:
                ratio = float(motiv_view) / float(total_view) * 100
                _add(metrics, "광고 도달 시청자",
                     _fmt_count(motiv_view, unit="명"),
                     f"전체 시청자 중 {ratio:.1f}%")
        except (TypeError, ValueError):
            pass

        # ── Tier 2: Engagement growth
        _add(metrics, "카트 추가 성장률",
             _fmt_pct(row.get("motiv_eng_growth"), signed=True),
             "광고 노출자 기준")

        # ── Tier 3: CTV ad-delivery aggregates (from scraper)
        if scraped_subs:
            total_imps = sum((s.get("impressions") or 0) for s in scraped_subs)
            total_replay_starts = sum((s.get("replay_starts") or 0) for s in scraped_subs)
            total_replay_100 = sum((s.get("replay_100") or 0) for s in scraped_subs)
            total_budget = sum((s.get("budget_total") or 0) for s in scraped_subs)

            if total_imps:
                _add(metrics, "총 광고 노출",
                     _fmt_count(total_imps),
                     f"{len(scraped_subs)}개 sub-캠페인 합산")

            if total_replay_starts and total_replay_100:
                vtr = total_replay_100 / total_replay_starts * 100
                _add(metrics, "광고 완시청률 (VTR)",
                     f"{vtr:.1f}%",
                     "100% 재생 / 재생시작")

            if total_budget and total_imps:
                cpi = total_budget / total_imps
                _add(metrics, "노출 1회당 비용",
                     f"{cpi:,.1f}원",
                     "총 예산 / 총 노출")

        # ── Tier 3: CTV product reach concentration
        if ctv_rows:
            total = next((r for r in ctv_rows if r.get("product") == "전체"), None)
            if total:
                r5 = total.get("reach_5plus") or 0
                r1 = total.get("reach_1plus") or 0
                if r1:
                    pct = float(r5) / float(r1) * 100
                    _add(metrics, "고빈도 노출 도달 (5회+)",
                         _fmt_count(r5, unit="명"),
                         f"1회+ 도달 중 {pct:.1f}%")

        # Cap to keep the table case-study-sized
        metrics = metrics[:9]

        # ── Identity
        name = str(row.get("campaign_name") or "")
        return CampaignData(
            campaign_no=str(row.get("campaign_no") or ""),
            campaign_name=name,
            advertiser=_extract_brand(name),
            industry=None,
            period_start=str(row.get("start_date") or "") or None,
            period_end=str(row.get("end_date") or "") or None,
            channel=None,
            objective=None,
            metrics_table=metrics,
            targeting_summary=None,
            audience_insights=[],
            creative_summary=None,
            extras={
                "view_row": {k: v for k, v in row.items() if v is not None},
                "scraped_subs_count": len(scraped_subs),
                "ctv_products_count": len(ctv_rows),
            },
        )
