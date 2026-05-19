"""python-docx writer for the press release. We build the document
programmatically rather than via a .j2 template — docx is XML under the
hood and Jinja doesn't round-trip cleanly.

Structure mirrors the reference PDF: 요약 + 01~03 본문 + 04 성과표 + 05 인사이트 + About.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Pt, RGBColor

from config import load_settings


OLIVE = RGBColor(0x4E, 0x5A, 0x2D)


def _heading(doc: Document, text: str, *, level: int = 2, size: int = 13):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.size = Pt(size)
        run.font.color.rgb = OLIVE
    return h


def render_press_docx(context: dict[str, Any], out_path: Path) -> Path:
    s = load_settings()
    campaign = context["campaign"]
    narrative = context["narrative"]
    headline = context["headline"]
    subhead = context.get("subhead", "")
    year = context.get("year", _dt.date.today().year)

    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Malgun Gothic"
    style.font.size = Pt(10.5)

    eyebrow = doc.add_paragraph()
    r = eyebrow.add_run(f"[보도자료] {s.company_name} Official Case Study")
    r.bold = True
    r.font.color.rgb = OLIVE
    r.font.size = Pt(10)

    # 헤드라인 — \n 으로 줄바꿈된 경우 run.add_break() 로 같은 단락 안에서 분리
    title = doc.add_heading("", level=1)
    _lines = (headline or "").split("\n")
    for i, line in enumerate(_lines):
        run = title.add_run(line)
        run.font.size = Pt(20)
        if i < len(_lines) - 1:
            run.add_break()

    if subhead:
        sub = doc.add_paragraph()
        _sub_lines = subhead.split("\n")
        for i, line in enumerate(_sub_lines):
            rs = sub.add_run(line)
            rs.italic = True
            rs.font.size = Pt(11)
            if i < len(_sub_lines) - 1:
                rs.add_break()

    # **bold** 마크업은 docx 에서도 같은 형태로 변환 (run.bold = True 적용)
    import re as _re
    _bold_re = _re.compile(r"\*\*(.+?)\*\*")

    if narrative.get("summary"):
        _heading(doc, "요약", size=13)
        # summary 는 단락 — bold 마크업 처리 후 add_paragraph
        _p_sum = doc.add_paragraph()
        # 아래 _add_runs_with_bold 가 곧 정의됨 — re module 이미 위에서 import.
        _sum_text = narrative["summary"]
        _pos = 0
        for _m in _bold_re.finditer(_sum_text):
            if _m.start() > _pos:
                _p_sum.add_run(_sum_text[_pos:_m.start()])
            _r = _p_sum.add_run(_m.group(1))
            _r.bold = True
            _pos = _m.end()
        if _pos < len(_sum_text):
            _p_sum.add_run(_sum_text[_pos:])

    def _add_runs_with_bold(paragraph, text: str):
        """`**bold**` 마크업이 들어간 텍스트를 docx run 으로 쪼개서 추가.
        bold 부분은 run.bold=True, 나머지는 일반."""
        if not text:
            return
        pos = 0
        for m in _bold_re.finditer(text):
            if m.start() > pos:
                paragraph.add_run(text[pos:m.start()])
            r = paragraph.add_run(m.group(1))
            r.bold = True
            pos = m.end()
        if pos < len(text):
            paragraph.add_run(text[pos:])

    for key, title_text in [
        ("overview", "01. 캠페인 목적"),
        ("background", "02. 광고 집행 배경"),
        ("strategy", "03. 적용 전략"),
    ]:
        raw = narrative.get(key)
        # 새 스키마 = list[str] 불릿, 옛 스키마 = str 단락. 둘 다 흡수.
        if isinstance(raw, list):
            items = [str(x).strip() for x in raw if str(x).strip()]
            if not items:
                continue
            _heading(doc, title_text, size=13)
            # strategy 의 첫 항목은 lead-in 단락 (불릿 X), 나머지만 불릿
            if key == "strategy" and len(items) >= 1:
                p = doc.add_paragraph()
                _add_runs_with_bold(p, items[0])
                for item in items[1:]:
                    bp = doc.add_paragraph(style="List Bullet")
                    _add_runs_with_bold(bp, item)
            else:
                for item in items:
                    p = doc.add_paragraph(style="List Bullet")
                    _add_runs_with_bold(p, item)
        else:
            body = (raw or "").strip() if isinstance(raw, str) else ""
            if not body:
                continue
            _heading(doc, title_text, size=13)
            p = doc.add_paragraph()
            _add_runs_with_bold(p, body)

    # 04. results table
    metrics = campaign.get("metrics_table") or []
    if metrics:
        _heading(doc, "04. 캠페인 성과", size=13)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        hdr[0].text = "성과 지표"
        hdr[1].text = "성과"
        hdr[2].text = "비고"
        for m in metrics:
            row = table.add_row().cells
            row[0].text = str(m.get("indicator", ""))
            row[1].text = str(m.get("value", ""))
            row[2].text = str(m.get("note", ""))

    insights = narrative.get("insights") or []
    if insights:
        _heading(doc, "05. 인사이트", size=13)
        for item in insights:
            p = doc.add_paragraph(style="List Bullet")
            _add_runs_with_bold(p, str(item))

    doc.add_paragraph()
    _heading(doc, f"About {s.company_name}", level=3, size=11)
    doc.add_paragraph(s.company_description)

    footer_p = doc.add_paragraph()
    footer_p.add_run(
        f"Website: {s.company_url}"
        + (f", {s.company_url_secondary}" if s.company_url_secondary else "")
        + f"  |  Contact Us: {s.press_contact_email}"
    ).font.size = Pt(9)

    copy_p = doc.add_paragraph()
    cr = copy_p.add_run(f"© {year} {s.company_name} Inc. All Rights Reserved.")
    cr.font.size = Pt(8.5)
    cr.font.color.rgb = RGBColor(0x6B, 0x6F, 0x63)

    doc.save(str(out_path))
    return out_path
