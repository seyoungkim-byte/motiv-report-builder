"""Olive palette + matplotlib rcParams shared across all chart templates.

The case study reference (성인영양식_2603_v2.pdf) anchors the brand mood:
warm ivory background, muted olive primary, with a single gold/amber
highlight color used sparingly for the "winner" element. Charts must
read at print-size (~60mm wide) without legends crowding the plot, so
defaults favor in-bar labels over external legends.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")  # noqa: E402  — headless render, must run before pyplot
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import rcParams  # noqa: E402


# ── Palette ─────────────────────────────────────────────────────────
OLIVE          = "#7d8c4e"   # primary
OLIVE_DARK     = "#4e5a2d"   # accent / highlight
OLIVE_LIGHT    = "#a0a682"   # secondary / non-highlighted bars
IVORY          = "#f5efe4"   # canvas / fills
GOLD           = "#c9a961"   # accent for "best/winner" callouts
INK            = "#1d1f1a"   # primary text
MUTED          = "#6b6f63"   # secondary text
GRID           = "#dfd9ca"   # gridlines / dividers
COMPARE_GREY   = "#c8c4b8"   # comparison/control bar

SERIES_COLORS = [OLIVE, GOLD, OLIVE_LIGHT, "#8b7d5a", "#3d4a23", "#6b6f63"]


# ── Font discovery ──────────────────────────────────────────────────
# Linux container: fonts-noto-cjk → "Noto Sans CJK KR" (.ttc collection
#   at /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc — some
#   freetype builds bundled with matplotlib reject .ttc, raising
#   "Can not load face; error code 0x2".)
# Windows local:   Malgun Gothic
# Mac local:       Apple SD Gothic Neo
_FONT_CANDIDATES = [
    # fonts-nanum 가 순수 TTF 라 freetype 에서 가장 안정적
    "NanumGothic",
    "NanumBarunGothic",
    # 로컬 OS 폰트 (Win/Mac)
    "Malgun Gothic",
    "Apple SD Gothic Neo",
    "Pretendard",
    # Noto CJK 는 .ttc 라 freetype 빌드에 따라 거부될 수 있음 — 후순위
    "Noto Sans CJK KR",
    "Noto Sans KR",
    "DejaVu Sans",
]


# Known Korean font paths to register directly. Streamlit Cloud (Debian)
# may have these installed but not yet in matplotlib's cached font list
# if the cache was built before the apt install. addfont() bypasses the
# cache lookup entirely.
_DIRECT_FONT_PATHS = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
]


def _register_direct_fonts() -> None:
    try:
        import os
        from matplotlib import font_manager as fm
    except Exception:
        return
    for p in _DIRECT_FONT_PATHS:
        if os.path.isfile(p):
            try:
                fm.fontManager.addfont(p)
            except Exception:
                continue


def _available_fonts() -> list[str]:
    """Return Korean-capable fonts present on this box, in priority order.

    Defensive: any single font file that freetype refuses (corrupt, .ttc
    edge cases, non-font sneaking into the font dirs) is skipped instead
    of nuking the entire discovery. Worst case we return ['DejaVu Sans']
    and Korean glyphs render as tofu boxes, but the chart still draws —
    much better than crashing the build."""
    _register_direct_fonts()
    try:
        from matplotlib.font_manager import findSystemFonts, FontProperties, fontManager
    except Exception:
        return ["DejaVu Sans"]
    installed: set[str] = set()
    # Names already loaded into the font manager (covers addfont() above)
    try:
        installed.update(f.name for f in fontManager.ttflist)
    except Exception:
        pass
    # Also walk system fonts defensively
    for f in findSystemFonts():
        try:
            installed.add(FontProperties(fname=f).get_name())
        except Exception:
            continue
    picked = [f for f in _FONT_CANDIDATES if f in installed]
    if not picked:
        picked = ["DejaVu Sans"]
    elif "DejaVu Sans" not in picked:
        picked.append("DejaVu Sans")  # last-resort fallback for the chain
    return picked


_STYLE_APPLIED = False


def apply_style() -> None:
    """Apply olive-palette rcParams globally. Idempotent."""
    global _STYLE_APPLIED
    if _STYLE_APPLIED:
        return
    font_chain = _available_fonts()
    rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": font_chain,
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.titleweight": "bold",
        "axes.titlepad": 8,
        "axes.titlecolor": OLIVE_DARK,
        "axes.labelsize": 8,
        "axes.labelcolor": MUTED,
        "axes.edgecolor": GRID,
        "axes.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.facecolor": "white",
        "figure.facecolor": "white",
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "xtick.major.size": 0,
        "ytick.major.size": 0,
        "axes.unicode_minus": False,
        "grid.color": GRID,
        "grid.linestyle": "-",
        "grid.linewidth": 0.4,
        "grid.alpha": 0.6,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "savefig.dpi": 180,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.06,
        "figure.dpi": 100,
    })
    _STYLE_APPLIED = True


def new_figure(width_in: float = 4.2, height_in: float = 2.4):
    """Create a fresh figure pre-styled. Caller closes with plt.close(fig)."""
    apply_style()
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    return fig, ax
