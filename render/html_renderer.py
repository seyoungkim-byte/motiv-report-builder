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
    data = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": ctx["headline"],
        "description": description,
        "about": {
            "@type": "AdvertisingCampaign",
            "name": campaign["campaign_name"],
            "advertiser": {"@type": "Organization", "name": campaign["advertiser"]},
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


def _enrich(context: dict[str, Any]) -> dict[str, Any]:
    ctx = dict(context)
    ctx.setdefault("year", _dt.date.today().year)
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
