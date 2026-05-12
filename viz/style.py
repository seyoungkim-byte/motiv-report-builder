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
# Linux container: fonts-noto-cjk → "Noto Sans CJK KR"
# Windows local:   Malgun Gothic
# Mac local:       Apple SD Gothic Neo
_FONT_CANDIDATES = [
    "Noto Sans CJK KR",
    "Noto Sans KR",
    "Malgun Gothic",
    "Apple SD Gothic Neo",
    "Pretendard",
    "DejaVu Sans",
]


def _available_font() -> str:
    """Pick the first installed Korean-capable font on this box."""
    from matplotlib.font_manager import findSystemFonts, FontProperties
    installed = {FontProperties(fname=f).get_name() for f in findSystemFonts()}
    for f in _FONT_CANDIDATES:
        if f in installed:
            return f
    return "DejaVu Sans"


_STYLE_APPLIED = False


def apply_style() -> None:
    """Apply olive-palette rcParams globally. Idempotent."""
    global _STYLE_APPLIED
    if _STYLE_APPLIED:
        return
    font = _available_font()
    rcParams.update({
        "font.family": font,
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
