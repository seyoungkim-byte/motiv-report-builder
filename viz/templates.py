"""Chart templates — each function takes a spec dict, returns PNG bytes.

Design rules (1-page case study, charts sit beside text at 6~7:3~4 ratio):
  - Default figure size 4.2x2.4in @ 180 DPI -> ~750x430 px, ~60mm in print.
  - No legend unless absolutely necessary; prefer in-bar / on-axis labels.
  - Highlight the "winning" data point in OLIVE_DARK; others in OLIVE_LIGHT.
  - GOLD is reserved for a single hero/best annotation.
  - Korean labels: rely on `style._available_font()` for CJK fallback.

Spec contract (all templates accept these top-level keys, template-specific
keys are documented per-function):
  title:    str   shown above plot, optional (we use template's title)
  subtitle: str   small italic under title
  source:   str   tiny grey footnote at bottom-right
  size:     "small"|"medium"|"large"  scales the figure
"""
from __future__ import annotations

import base64
import io
import math
from typing import Any, Callable

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from .style import (
    OLIVE, OLIVE_DARK, OLIVE_LIGHT, GOLD, IVORY, MUTED, INK,
    GRID, COMPARE_GREY, SERIES_COLORS,
    apply_style, new_figure,
)


# ── Helpers ────────────────────────────────────────────────────────
_SIZE_PRESETS = {
    "small":  (3.6, 2.0),
    "medium": (4.2, 2.4),
    "large":  (5.4, 3.0),
}


def _figsize(spec: dict[str, Any]) -> tuple[float, float]:
    return _SIZE_PRESETS.get(spec.get("size") or "medium", _SIZE_PRESETS["medium"])


def _fig_to_png_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _format_value(value: float, fmt: str | None) -> str:
    """Apply a Python str.format spec, gracefully fall back.

    Claude sometimes returns Excel/Korean format codes like '+#,##0.0%'
    or '+0.0%' instead of Python's '{:+,.1f}%'. Those strings parse fine
    via `.format()` but contain no placeholder, so the literal pattern
    leaks into the chart. We require a real `{` placeholder to trust the
    template; otherwise fall through to sensible defaults."""
    if fmt and "{" in fmt and "}" in fmt:
        try:
            out = fmt.format(value)
            # double-check: must look different from the template itself,
            # i.e. the placeholder was actually substituted
            if out != fmt:
                return out
        except Exception:
            pass
    if isinstance(value, float):
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        if abs(value) >= 100:
            return f"{value:.1f}"
        if abs(value) >= 1:
            return f"{value:.1f}"
        return f"{value:.2f}"
    return f"{value:,}"


def _add_title(fig, ax, spec: dict[str, Any]) -> None:
    """Title rendered as bold olive header with a short underline accent.

    Positioned via fig.text in figure coords so tight_layout doesn't
    push it around. The underline mimics the magazine-style heading
    used by the reference 1p case studies.
    """
    import matplotlib.lines as mlines
    title = spec.get("title") or ""
    subtitle = spec.get("subtitle") or ""
    if title:
        fig.text(0.015, 0.95, title, fontsize=10.5, fontweight="bold",
                 color=OLIVE_DARK, ha="left", va="top")
        underline = mlines.Line2D(
            [0.015, 0.055], [0.918, 0.918],
            transform=fig.transFigure,
            color=OLIVE, linewidth=1.4, solid_capstyle="butt",
        )
        fig.add_artist(underline)
    if subtitle:
        fig.text(0.015, 0.892, subtitle, fontsize=7.5, style="italic",
                 color=MUTED, ha="left", va="top")


def _add_source(fig, spec: dict[str, Any]) -> None:
    src = spec.get("source")
    if not src:
        return
    fig.text(0.98, 0.005, src, fontsize=6.5, color=MUTED,
             ha="right", va="bottom", style="italic")


# ── 1. bar_horizontal ─────────────────────────────────────────────
def bar_horizontal(spec: dict[str, Any]) -> str:
    """Horizontal bars with a highlighted winner.

    Spec:
      labels:        list[str]
      values:        list[float]
      value_format:  str  e.g. "{:.2f}%", "{:,.0f}명"
      highlight_idx: int | None  index of the bar to emphasize (default: argmax)
      x_label:       str (optional)

    Use for: rate comparisons, conversion%, CTR by segment, lift by audience.
    """
    apply_style()
    labels = list(spec["labels"])
    values = [float(v) for v in spec["values"]]
    fmt = spec.get("value_format")
    hi = spec.get("highlight_idx")
    if hi is None:
        hi = max(range(len(values)), key=lambda i: values[i])

    w, h = _figsize(spec)
    fig, ax = plt.subplots(figsize=(w, h))

    y = list(range(len(labels)))
    colors = [OLIVE_DARK if i == hi else OLIVE_LIGHT for i in y]
    bars = ax.barh(y, values, color=colors, height=0.65,
                   edgecolor="none")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8, color=INK)
    ax.invert_yaxis()  # first label on top
    ax.set_xlim(0, max(values) * 1.22 if values else 1)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(left=False)

    for bar, v in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.015,
                bar.get_y() + bar.get_height() / 2,
                _format_value(v, fmt),
                va="center", ha="left",
                fontsize=8.5, fontweight="bold",
                color=OLIVE_DARK)

    if spec.get("x_label"):
        ax.set_xlabel(spec["x_label"])

    _add_title(fig, ax, spec)
    _add_source(fig, spec)
    fig.tight_layout(rect=(0, 0.02, 1, 0.92))
    return _fig_to_png_b64(fig)


# ── 2. bar_vertical_pair ──────────────────────────────────────────
def bar_vertical_pair(spec: dict[str, Any]) -> str:
    """Two-series grouped vertical bars (e.g. control vs motiv, 전월 vs 당월).

    Spec:
      categories:    list[str]            x-axis groups
      series_a:      {label, values}      "control" / "전월"  → grey
      series_b:      {label, values}      "treatment" / "당월" → olive
      value_format:  str
      highlight_b:   bool (default True)  draw delta on series_b bars
    """
    apply_style()
    cats = list(spec["categories"])
    a = spec["series_a"]
    b = spec["series_b"]
    a_vals = [float(v) for v in a["values"]]
    b_vals = [float(v) for v in b["values"]]
    fmt = spec.get("value_format")

    w, h = _figsize(spec)
    fig, ax = plt.subplots(figsize=(w, h))

    n = len(cats)
    x = list(range(n))
    width = 0.36
    bars_a = ax.bar([i - width/2 for i in x], a_vals,
                    width=width, color=COMPARE_GREY,
                    label=a.get("label", "A"), edgecolor="none")
    bars_b = ax.bar([i + width/2 for i in x], b_vals,
                    width=width, color=OLIVE_DARK,
                    label=b.get("label", "B"), edgecolor="none")

    ax.set_xticks(x)
    ax.set_xticklabels(cats, fontsize=8.5, color=INK)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(bottom=False)

    max_v = max(max(a_vals or [0]), max(b_vals or [0])) or 1
    ax.set_ylim(0, max_v * 1.22)

    for bar, v, color in [(b, v, OLIVE_DARK) for b, v in zip(bars_b, b_vals)] + \
                         [(b, v, MUTED) for b, v in zip(bars_a, a_vals)]:
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_v * 0.025,
                _format_value(v, fmt),
                ha="center", va="bottom",
                fontsize=7.8, fontweight="bold",
                color=color)

    # Delta annotation between paired bars
    if spec.get("highlight_b", True):
        for i, (av, bv) in enumerate(zip(a_vals, b_vals)):
            if av == 0:
                continue
            delta_pct = (bv - av) / av * 100
            arrow = "▲" if delta_pct >= 0 else "▼"
            ax.text(i, max_v * 1.13,
                    f"{arrow} {abs(delta_pct):.1f}%",
                    ha="center", va="bottom",
                    fontsize=7.5, fontweight="bold",
                    color=(GOLD if delta_pct >= 0 else "#b35a3a"))

    leg = ax.legend(loc="upper right", bbox_to_anchor=(1.0, 1.18),
                    fontsize=7.5, ncols=2, handlelength=1.2,
                    handleheight=0.6, columnspacing=1)
    for text in leg.get_texts():
        text.set_color(MUTED)

    _add_title(fig, ax, spec)
    _add_source(fig, spec)
    fig.tight_layout(rect=(0, 0.02, 1, 0.88))
    return _fig_to_png_b64(fig)


# ── 3. donut ──────────────────────────────────────────────────────
def donut(spec: dict[str, Any]) -> str:
    """Donut chart with right-side legend showing percentages.

    Spec:
      labels:        list[str]
      values:        list[float]   raw counts or percentages
      highlight_idx: int | None    slice to emphasize (default: argmax)
      center_label:  str           text in donut hole (e.g. "전체 100만")

    Use for: target share, impression share, segment composition.
    Limit 5 slices — collapse the tail into "기타" before passing.
    """
    apply_style()
    labels = list(spec["labels"])
    values = [float(v) for v in spec["values"]]
    total = sum(values) or 1
    pcts = [v / total * 100 for v in values]
    hi = spec.get("highlight_idx")
    if hi is None:
        hi = max(range(len(values)), key=lambda i: values[i])

    w, h = _figsize(spec)
    fig, ax = plt.subplots(figsize=(w, h))

    colors = []
    for i in range(len(values)):
        if i == hi:
            colors.append(OLIVE_DARK)
        else:
            colors.append(SERIES_COLORS[(i + 1) % len(SERIES_COLORS)])

    explode = [0.04 if i == hi else 0 for i in range(len(values))]
    wedges, _ = ax.pie(values, colors=colors, startangle=90,
                       counterclock=False, explode=explode,
                       wedgeprops=dict(width=0.36, edgecolor="white", linewidth=2))

    ax.set(aspect="equal")
    if spec.get("center_label"):
        ax.text(0, 0.05, spec["center_label"],
                ha="center", va="center",
                fontsize=9, fontweight="bold", color=OLIVE_DARK)

    # Right-side legend
    legend_items = [f"{lbl}  {pct:.1f}%" for lbl, pct in zip(labels, pcts)]
    leg = ax.legend(wedges, legend_items, loc="center left",
                    bbox_to_anchor=(1.0, 0.5), fontsize=7.8,
                    handlelength=1.0, handleheight=0.8, borderaxespad=0)
    for text in leg.get_texts():
        text.set_color(INK)

    _add_title(fig, ax, spec)
    _add_source(fig, spec)
    fig.tight_layout(rect=(0, 0.02, 1, 0.92))
    return _fig_to_png_b64(fig)


# ── 4. funnel ─────────────────────────────────────────────────────
def funnel(spec: dict[str, Any]) -> str:
    """Descending horizontal bars representing a conversion funnel.

    Spec:
      stages:   list[{name, value}]  ordered top→bottom
      value_format: str
      show_drop: bool (default True)  show step-to-step retention %
    """
    apply_style()
    stages = list(spec["stages"])
    names = [s["name"] for s in stages]
    values = [float(s["value"]) for s in stages]
    fmt = spec.get("value_format")
    max_v = max(values) or 1

    w, h = _figsize(spec)
    fig, ax = plt.subplots(figsize=(w, h))

    # Funnel widths: proportional, but clamped so the smallest bar still
    # fits its label. For 노출→구매 funnels the ratio is often ~1000:1, so
    # raw proportional widths make terminal stages invisible — we instead
    # interpolate between the true ratio and a readable minimum.
    MIN_W = 0.22
    y = list(range(len(stages)))
    widths = [max(v / max_v, MIN_W) for v in values]
    lefts = [(1 - wd) / 2 for wd in widths]
    # darker tone as we descend
    color_grad = []
    n = len(stages)
    for i in range(n):
        t = i / max(n - 1, 1)
        # OLIVE_LIGHT → OLIVE_DARK
        color_grad.append(OLIVE if i < n - 1 else OLIVE_DARK)
    color_grad[0] = OLIVE_LIGHT
    if n >= 3:
        color_grad[1] = OLIVE

    bars = ax.barh(y, widths, left=lefts, color=color_grad,
                   height=0.7, edgecolor="white", linewidth=1.5)

    for i, (bar, v, name) in enumerate(zip(bars, values, names)):
        cx = 0.5
        cy = bar.get_y() + bar.get_height() / 2
        ax.text(cx, cy, f"{name}  ·  {_format_value(v, fmt)}",
                ha="center", va="center",
                fontsize=8.5, fontweight="bold", color="white")
        if spec.get("show_drop", True) and i > 0 and values[i - 1] > 0:
            retain = values[i] / values[i - 1] * 100
            ax.text(0.99, cy, f"{retain:.1f}% ↘",
                    ha="right", va="center",
                    fontsize=7, color=MUTED)

    ax.set_xlim(0, 1)
    ax.set_ylim(-0.5, len(stages) - 0.5)
    ax.invert_yaxis()
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    _add_title(fig, ax, spec)
    _add_source(fig, spec)
    fig.tight_layout(rect=(0, 0.02, 1, 0.92))
    return _fig_to_png_b64(fig)


# ── 5. index_lift ─────────────────────────────────────────────────
def index_lift(spec: dict[str, Any]) -> str:
    """Single highlight bar vs 100-baseline (control). For brand-lift
    style "index = X" comparisons.

    Spec:
      label:    str          name of the treatment group
      index:    float        e.g. 137 means 1.37x of control
      baseline_label: str    default "대조군 (100)"
      note:     str (optional) shown below value
    """
    apply_style()
    label = spec.get("label", "광고 노출 그룹")
    idx = float(spec["index"])
    baseline = spec.get("baseline_label", "대조군")

    w, h = _figsize(spec)
    fig, ax = plt.subplots(figsize=(w, h))

    bars = ax.bar([0, 1], [100, idx],
                  width=0.55,
                  color=[COMPARE_GREY, OLIVE_DARK],
                  edgecolor="none")
    ax.set_xticks([0, 1])
    ax.set_xticklabels([baseline, label], fontsize=8.5, color=INK)
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.spines["bottom"].set_visible(True)

    max_v = max(100, idx) * 1.25
    ax.set_ylim(0, max_v)

    # Numeric labels on bars
    ax.text(0, 100 + max_v * 0.03, "100",
            ha="center", va="bottom",
            fontsize=9, fontweight="bold", color=MUTED)
    ax.text(1, idx + max_v * 0.03, f"{idx:,.0f}",
            ha="center", va="bottom",
            fontsize=11, fontweight="bold", color=OLIVE_DARK)

    # Lift annotation (axes-fraction coords so it always sits near the top)
    lift_pct = idx - 100
    arrow = "▲" if lift_pct >= 0 else "▼"
    color = GOLD if lift_pct >= 0 else "#b35a3a"
    ax.text(0.5, 0.92,
            f"{arrow} {abs(lift_pct):.1f}p Lift",
            transform=ax.transAxes,
            ha="center", va="top",
            fontsize=9.5, fontweight="bold", color=color)

    if spec.get("note"):
        fig.text(0.5, 0.04, spec["note"],
                 ha="center", va="bottom",
                 fontsize=7.5, color=MUTED, style="italic")

    _add_title(fig, ax, spec)
    _add_source(fig, spec)
    fig.tight_layout(rect=(0, 0.1, 1, 0.92))
    return _fig_to_png_b64(fig)


# ── 6. freq_distribution ──────────────────────────────────────────
def freq_distribution(spec: dict[str, Any]) -> str:
    """Stacked-segment frequency distribution (1회/2회/3-4회/5+).

    Spec:
      buckets:  list[{name, value, share}]  share is 0~100 percent
      total_label: str   shown to the right (e.g. "Reach 1+ 565,772명")

    Use for: 빈도 구간별 도달 분포, 노출 횟수 분포, 시청 횟수 분포.
    """
    apply_style()
    buckets = list(spec["buckets"])
    names = [b["name"] for b in buckets]
    shares = [float(b.get("share", 0)) for b in buckets]
    counts = [b.get("value") for b in buckets]

    # Normalize shares to sum 100 if they came in as raw counts
    if abs(sum(shares) - 100) > 5 and counts and all(c is not None for c in counts):
        tot = sum(float(c) for c in counts) or 1
        shares = [float(c) / tot * 100 for c in counts]

    w, h = _figsize(spec)
    fig, ax = plt.subplots(figsize=(w, 0.9 + 0.35 * len(buckets)))

    palette = [OLIVE_DARK, OLIVE, OLIVE_LIGHT, GOLD, "#8b7d5a"]
    y = list(range(len(buckets)))
    bars = ax.barh(y, shares, color=palette[: len(buckets)],
                   height=0.55, edgecolor="none")

    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8.5, color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, max(shares) * 1.25 if shares else 100)
    ax.set_xticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False)

    for bar, share, count in zip(bars, shares, counts):
        txt = f"{share:.1f}%"
        if count is not None:
            try:
                txt += f"  ·  {int(float(count)):,}명"
            except (TypeError, ValueError):
                pass
        ax.text(bar.get_width() + max(shares) * 0.02,
                bar.get_y() + bar.get_height() / 2,
                txt,
                va="center", ha="left",
                fontsize=8, fontweight="bold", color=OLIVE_DARK)

    if spec.get("total_label"):
        fig.text(0.98, 0.02, spec["total_label"], ha="right", va="bottom",
                 fontsize=8, fontweight="bold", color=OLIVE_DARK)

    _add_title(fig, ax, spec)
    _add_source(fig, spec)
    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    return _fig_to_png_b64(fig)


# ── Registry ───────────────────────────────────────────────────────
TEMPLATE_REGISTRY: dict[str, Callable[[dict[str, Any]], str]] = {
    "bar_horizontal":    bar_horizontal,
    "bar_vertical_pair": bar_vertical_pair,
    "donut":             donut,
    "funnel":            funnel,
    "index_lift":        index_lift,
    "freq_distribution": freq_distribution,
}

TEMPLATE_NAMES = list(TEMPLATE_REGISTRY.keys())


def render_chart(template: str, spec: dict[str, Any]) -> str:
    """Dispatch — returns base64-encoded PNG, ready for data: URI.

    Raises KeyError if template name is unknown — caller should validate
    against TEMPLATE_NAMES before invoking.
    """
    fn = TEMPLATE_REGISTRY[template]
    return fn(spec)
