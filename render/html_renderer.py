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
    # summary/overview 가 list 일 수도 있음 (옛 빌드는 str). join 으로 평탄화.
    _summary = narrative.get("summary") or ""
    if isinstance(_summary, list):
        _summary = " ".join(str(x) for x in _summary)
    _overview = narrative.get("overview") or ""
    if isinstance(_overview, list):
        _overview = " ".join(str(x) for x in _overview)
    description = (_summary or _overview)[:280]
    # JSON-LD 는 search engine / AI 가 직접 인덱싱하므로 실제 광고주명 노출 금지.
    # category (industry) 기반 마스킹 라벨로 대체. 매핑 없으면 generic.
    industry = campaign.get("industry") or ""
    masked = f"{industry} 광고주" if industry else "광고주"
    # JSON-LD 의 headline 은 plain text 만 — <br> 없는 버전 사용
    headline_for_json = ctx.get("headline_plain") or str(ctx.get("headline", "")).replace("\n", " ").strip()
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": headline_for_json,
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


def _newline_to_br(value: Any) -> Any:
    """\\n → <br>. escape 처리 후 Markup 으로 wrap.
    HTML 특수문자는 그대로 escape 되고 줄바꿈만 <br> 로 살아남음.

    주의: Markup.replace() 는 new 값(<br>)을 자동 escape 해버리므로
    intermediate 단계에서 반드시 plain str 로 변환해 .replace 수행.

    Defensive: 어떤 이유든 escape 가 실패하면 원본 그대로 안전 반환.
    빌드 전체가 죽는 것보다 줄바꿈 한번 잃는 게 낫다."""
    from markupsafe import Markup, escape
    if value is None:
        return ""
    if not isinstance(value, str) or not value:
        return value
    try:
        escaped_plain = str(escape(value))
        with_br = escaped_plain.replace("\n", "<br>")
        return Markup(with_br)
    except Exception:
        # 비정상 입력 (Undefined 등) 받아도 빌드는 계속
        return Markup(str(value).replace("\n", "<br>"))


def _flatten_newlines(value: Any) -> str:
    """\\n → ' '. HTML 속성 (title / og:title / JSON-LD) 자리 용도."""
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return ""
    return value.replace("\n", " ").strip()


def _enrich(context: dict[str, Any]) -> dict[str, Any]:
    ctx = dict(context)
    ctx.setdefault("year", _dt.date.today().year)
    ctx["charts"] = _split_charts(ctx.get("chart_set"))
    # 헤드라인·서브헤드: body 용 <br> 버전 + 속성 용 plain 버전 두 가지 동시 제공
    _raw_h = ctx.get("headline", "")
    _raw_s = ctx.get("subhead", "")
    ctx["headline_plain"] = _flatten_newlines(_raw_h)
    ctx["subhead_plain"]  = _flatten_newlines(_raw_s)
    ctx["headline"]       = _newline_to_br(_raw_h)
    ctx["subhead"]        = _newline_to_br(_raw_s)
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
