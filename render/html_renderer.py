from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from config import ASSETS_DIR, load_settings

from .jinja_env import build_env


def _css(name: str) -> str:
    return (ASSETS_DIR / "css" / name).read_text(encoding="utf-8")


def _build_jsonld(ctx: dict[str, Any]) -> str:
    s = load_settings()
    campaign = ctx["campaign"]
    narrative = ctx["narrative"]
    description = (narrative.get("summary") or narrative.get("overview") or "")[:280]
    # JSON-LD 는 search engine / AI 가 직접 인덱싱하므로 실제 광고주명 노출 금지.
    # category (industry) 기반 마스킹 라벨로 대체. 매핑 없으면 generic.
    industry = campaign.get("industry") or ""
    masked = f"{industry} 광고주" if industry else "광고주"
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": ctx["headline"],
        "description": description,
        "about": {
            "@type": "AdvertisingCampaign",
            "name": masked,
            "advertiser": {"@type": "Organization", "name": masked},
            "channel": campaign.get("channel"),
        },
        "author": {"@type": "Organization", "name": s.company_name, "url": s.company_url},
        "publisher": {
            "@type": "Organization",
            "name": s.company_name,
            "url": s.company_url,
            **({"logo": s.company_logo_url} if s.company_logo_url else {}),
        },
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def _split_charts(chart_set: Any) -> dict[str, Any]:
    """Reshape the flat chart list into the structure templates expect:

      {performance: list[spec], inline_strategy: spec | None}

    Each entry has shape {template, placement, title, subtitle, caption,
    data, image_b64}. The b64 is what the template actually inlines.
    """
    perf: list[dict[str, Any]] = []
    inline: dict[str, Any] | None = None
    if not chart_set:
        return {"performance": perf, "inline_strategy": inline}
    for spec in chart_set:
        if not isinstance(spec, dict) or not spec.get("image_b64"):
            continue
        plc = spec.get("placement") or "performance"
        if plc == "inline_strategy" and inline is None:
            inline = spec
        elif plc == "performance" and len(perf) < 2:
            perf.append(spec)
    return {"performance": perf, "inline_strategy": inline}


def _enrich(context: dict[str, Any]) -> dict[str, Any]:
    ctx = dict(context)
    ctx.setdefault("year", _dt.date.today().year)
    ctx["charts"] = _split_charts(ctx.get("chart_set"))
    return ctx


def render_web_html(context: dict[str, Any], out_path: Path) -> Path:
    ctx = _enrich(context)
    env = build_env()
    tpl = env.get_template("web.html.j2")
    html = tpl.render(**ctx, css=_css("web.css"), jsonld=_build_jsonld(ctx))
    out_path.write_text(html, encoding="utf-8")
    return out_path


def render_print_html(context: dict[str, Any], out_path: Path) -> Path:
    ctx = _enrich(context)
    env = build_env()
    tpl = env.get_template("print.html.j2")
    html = tpl.render(**ctx, css=_css("print.css"))
    out_path.write_text(html, encoding="utf-8")
    return out_path
