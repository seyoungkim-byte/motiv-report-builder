"""Server-side chart rendering for the case study.

Matplotlib is invoked head-less. Each template returns base64-encoded PNG
bytes ready to inline via `<img src="data:image/png;base64,...">`. The
Claude planner picks 2~3 templates per campaign and supplies the data
payloads — see `ai.chart_planner`.
"""
from .templates import (
    TEMPLATE_REGISTRY,
    TEMPLATE_NAMES,
    render_chart,
)

__all__ = ["TEMPLATE_REGISTRY", "TEMPLATE_NAMES", "render_chart"]
