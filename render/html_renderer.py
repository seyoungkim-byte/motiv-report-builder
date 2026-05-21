from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

from config import ASSETS_DIR, load_settings

from .jinja_env import build_env


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# 항목이 '- ' / '* ' / '▪ ' 로 시작하면 명시적 불릿. 그 외 = paragraph.
_BULLET_PREFIXES = ("- ", "* ", "▪ ", "• ")


def _css(name: str) -> str:
    return (ASSETS_DIR / "css" / name).read_text(encoding="utf-8")


def _strip_md(value: Any) -> str:
    """**bold** 마크업과 \\n 을 제거한 plain 문자열. JSON-LD / meta 속성 자리용."""
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return ""
    s = _BOLD_RE.sub(r"\1", value)
    return s.replace("\n", " ").strip()


def _md_to_safe_html(value: Any) -> Any:
    """본문 string → escape 후 \\n→<br>, **text**→<strong>text</strong> 변환.
    템플릿 안에서 그대로 출력해도 안전한 Markup 반환.
    Defensive: 비정상 입력이면 원본 그대로 반환 (빌드 멈추지 않게)."""
    from markupsafe import Markup, escape
    if value is None:
        return ""
    if not isinstance(value, str) or not value:
        return value
    try:
        s = str(escape(value))
        s = s.replace("\n", "<br>")
        # escape 후엔 ** 그대로 살아있음 (markdown 특수문자 아님). 변환.
        s = _BOLD_RE.sub(r"<strong>\1</strong>", s)
        return Markup(s)
    except Exception:
        return Markup(str(value))


def _is_bullet_line(s: str) -> bool:
    return isinstance(s, str) and any(s.startswith(p) for p in _BULLET_PREFIXES)


def _strip_bullet_prefix(s: str) -> str:
    """`- 텍스트` → `텍스트` (불릿 마커 제거). 마커 없으면 원본."""
    for p in _BULLET_PREFIXES:
        if s.startswith(p):
            return s[len(p):].lstrip()
    return s


def _to_blocks(items: Any) -> list[dict[str, Any]]:
    """list[str] → [{kind: 'p', text: Markup} | {kind: 'ul', items: [Markup,...]}]

    명시 규칙:
      - '- ' / '* ' / '▪ ' / '• ' 로 시작 = 불릿 항목
      - 그 외 = paragraph 항목
    연속된 불릿 항목은 하나의 <ul> 로 묶이고, 그 사이 일반 항목은 <p> 로.

    Defensive: list 가 아니어도 iterable 이면 받아서 처리. 항목이 이미 dict
    형태(kind/items/text) 면 그대로 통과시킴 — 옛 빌드 load 같은 비정상 경로
    에서도 jinja UndefinedError 가 안 나도록.
    """
    # str / 단일 값은 list 가 아니라 빈 결과 — 호출측에서 paragraph 분기 처리.
    if isinstance(items, str) or items is None:
        return []
    try:
        iter(items)
    except TypeError:
        return []

    blocks: list[dict[str, Any]] = []
    cur_ul: list[Any] = []

    def _flush_ul():
        nonlocal cur_ul
        if cur_ul:
            blocks.append({"kind": "ul", "items": cur_ul})
            cur_ul = []

    for raw in items:
        # 이미 block dict 형태로 들어오면 그대로 통과 (kind 보장).
        if isinstance(raw, dict) and raw.get("kind") in ("p", "ul", "spacer"):
            _flush_ul()
            blocks.append(raw)
            continue
        # None / 비-str 은 무시 또는 str 화.
        if raw is None:
            continue
        if not isinstance(raw, str):
            try:
                raw_s = str(raw)
            except Exception:
                continue
        else:
            raw_s = raw
        raw_s = raw_s.strip()
        if not raw_s:
            # 빈 줄 = spacer 블록. 연속 빈 줄은 1개로 collapse.
            _flush_ul()
            if not (blocks and isinstance(blocks[-1], dict) and blocks[-1].get("kind") == "spacer"):
                blocks.append({"kind": "spacer"})
            continue
        if _is_bullet_line(raw_s):
            cur_ul.append(_md_to_safe_html(_strip_bullet_prefix(raw_s)))
        else:
            _flush_ul()
            blocks.append({"kind": "p", "text": _md_to_safe_html(raw_s)})
    _flush_ul()
    # 앞/뒤 spacer 제거 (중간 spacer 만 의미)
    while blocks and isinstance(blocks[0], dict) and blocks[0].get("kind") == "spacer":
        blocks.pop(0)
    while blocks and isinstance(blocks[-1], dict) and blocks[-1].get("kind") == "spacer":
        blocks.pop()
    return blocks


# 새 블록 구조를 사용하는 narrative 필드 (template 이 blocks 로 받음)
_BLOCK_FIELDS = {"overview", "background", "strategy"}


def _enrich_narrative(nar: dict[str, Any]) -> dict[str, Any]:
    """narrative dict 의 모든 string 값을 _md_to_safe_html 로 변환.
    overview/background/strategy 는 추가로 _to_blocks 로 그룹화 — 템플릿이
    {kind: 'p'|'ul', ...} 블록 단위로 렌더. insights 는 항상 list[str] 유지
    (별도 박스 안에서 단순 ul 또는 p 처리).

    Defensive: list 가 아닌 입력(str, None, tuple 등)도 안전 처리.
    block field 값은 변환 후 항상 list[dict({kind})] 또는 Markup 보장.
    """
    out: dict[str, Any] = {}
    for k, v in (nar or {}).items():
        if k in _BLOCK_FIELDS:
            # 옛 빌드: str — paragraph 분기를 위해 그대로 변환된 Markup 유지
            if isinstance(v, str):
                out[k] = _md_to_safe_html(v)
                out[f"{k}_items"] = [_md_to_safe_html(v)]
            elif v is None:
                out[k] = []
                out[f"{k}_items"] = []
            else:
                # list / tuple / 기타 iterable — _to_blocks 가 안전 처리
                out[k] = _to_blocks(v)
                try:
                    out[f"{k}_items"] = [
                        _md_to_safe_html(_strip_bullet_prefix(x)) if isinstance(x, str) else x
                        for x in v
                    ]
                except TypeError:
                    out[f"{k}_items"] = []
            continue
        if isinstance(v, str):
            out[k] = _md_to_safe_html(v)
        elif isinstance(v, list):
            out[k] = [
                _md_to_safe_html(item) if isinstance(item, str) else item
                for item in v
            ]
        else:
            out[k] = v
    return out


def _build_jsonld(ctx: dict[str, Any]) -> str:
    s = load_settings()
    campaign = ctx["campaign"]
    narrative = ctx["narrative"]
    # summary/overview 가 list 일 수도 있음 (옛 빌드는 str). join 으로 평탄화.
    # 또한 본문엔 **bold** 마크업이 살아있으니 plain 으로 strip 후 사용.
    _summary = narrative.get("summary") or ""
    if isinstance(_summary, list):
        _summary = " ".join(_strip_md(x) for x in _summary)
    else:
        _summary = _strip_md(_summary)
    _overview = narrative.get("overview") or ""
    if isinstance(_overview, list):
        _overview = " ".join(_strip_md(x) for x in _overview)
    else:
        _overview = _strip_md(_overview)
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
    intermediate 단계에서 반드시 plain str 로 변환해 .replace 수행."""
    from markupsafe import Markup, escape
    if not isinstance(value, str) or not value:
        return value
    escaped_plain = str(escape(value))   # Markup → plain str (특수문자만 escape)
    with_br = escaped_plain.replace("\n", "<br>")
    return Markup(with_br)


def _flatten_newlines(value: Any) -> str:
    """\\n → ' '. HTML 속성 (title / og:title / JSON-LD) 자리 용도."""
    if not isinstance(value, str):
        return value
    return value.replace("\n", " ").strip()


def _enrich(context: dict[str, Any]) -> dict[str, Any]:
    ctx = dict(context)
    ctx.setdefault("year", _dt.date.today().year)
    ctx["charts"] = _split_charts(ctx.get("chart_set"))
    # 헤드라인·서브헤드: body 용 <br> 버전 + 속성 용 plain 버전 두 가지 동시 제공.
    # 헤드라인도 **bold** 마크업 지원 — 사용자가 강조 단어 지정 가능.
    _raw_h = ctx.get("headline", "")
    _raw_s = ctx.get("subhead", "")
    ctx["headline_plain"] = _strip_md(_raw_h)
    ctx["subhead_plain"]  = _strip_md(_raw_s)
    ctx["headline"]       = _md_to_safe_html(_raw_h)
    ctx["subhead"]        = _md_to_safe_html(_raw_s)
    # narrative 의 string 필드들도 동일 변환 (template 에서 그대로 출력)
    ctx["narrative"] = _enrich_narrative(ctx.get("narrative") or {})
    # 04 표 아래 footnote — 줄 단위 분리 + **bold** 마크업 지원
    _fn_raw = ctx.get("metrics_footnotes") or ""
    if isinstance(_fn_raw, str) and _fn_raw.strip():
        ctx["metrics_footnotes_lines"] = [
            _md_to_safe_html(ln) for ln in _fn_raw.split("\n") if ln.strip()
        ]
    else:
        ctx["metrics_footnotes_lines"] = []
    return ctx


def render_web_html(context: dict[str, Any], out_path: Path) -> Path:
    # jsonld 는 원본 plain narrative 로 먼저 만들어야 escape 깨지지 않음.
    jsonld = _build_jsonld(context)
    ctx = _enrich(context)
    env = build_env()
    tpl = env.get_template("web.html.j2")
    html = tpl.render(**ctx, css=_css("web.css"), jsonld=jsonld)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def render_print_html(context: dict[str, Any], out_path: Path) -> Path:
    ctx = _enrich(context)
    env = build_env()
    tpl = env.get_template("print.html.j2")
    html = tpl.render(**ctx, css=_css("print.css"))
    out_path.write_text(html, encoding="utf-8")
    return out_path
