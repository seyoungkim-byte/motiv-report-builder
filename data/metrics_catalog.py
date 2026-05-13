"""Metric definitions catalog — L3 layer.

Loads from Supabase `metric_definitions` table (canonical source of truth).
Both apps (dashboard + builder) reference metric_id strings; this module
turns those into display labels, formats, and view-binding info.

When metrics get renamed or rewired, edit the table in Supabase Studio —
no code change required. App caches for 1 hour to avoid hammering the DB
on every campaign render.
"""
from __future__ import annotations

import operator
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .supabase_client import get_client


TABLE = "metric_definitions"
_CACHE: dict[str, Any] = {"rows": [], "ts": 0.0}
_CACHE_TTL_SECONDS = 3600  # 1 hour — metric defs change rarely


@dataclass
class MetricView:
    """One sub-view of a metric (market / motiv / relative, etc.)."""
    key:     str                # "market" / "motiv" / "relative" / arbitrary
    label:   str                # 시장평균 / 광고노출자 / 상대지수
    value_column:    str | None = None   # view column if direct lookup
    computed_expr:   str | None = None   # arithmetic expression if computed
    unit:    str | None = None
    format_spec: str | None = None


@dataclass
class MetricDef:
    metric_id:    str
    display_name: str
    display_long: str | None = None
    tier1:        str | None = None
    tier3:        str | None = None
    description:  str | None = None
    formula:      str | None = None
    unit:         str | None = None
    format_spec:  str | None = None
    channel:      str | None = None
    is_active:    bool = True
    sort_order:   int = 0
    notes:        str | None = None
    # parsed view bindings
    primary_view: str | None = None      # which views[key] to display by default
    views:        dict[str, MetricView] = field(default_factory=dict)
    single_value_column: str | None = None  # shortcut for {"value": "X"} shape
    status:       str | None = None       # "planned" / None
    raw_bindings: dict[str, Any] = field(default_factory=dict)  # original JSONB

    @property
    def is_multi_view(self) -> bool:
        return bool(self.views)

    @property
    def is_planned(self) -> bool:
        return self.status == "planned"

    def primary_view_def(self) -> MetricView | None:
        if not self.views:
            return None
        key = self.primary_view or next(iter(self.views.keys()))
        return self.views.get(key)


def _parse_view_bindings(vb: Any) -> tuple[str | None, dict[str, MetricView], str | None, str | None]:
    """Return (primary_view, views_dict, single_value_column, status)."""
    if not isinstance(vb, dict):
        return None, {}, None, None
    status = vb.get("status")
    if status:
        return None, {}, None, str(status)

    # case: {"value": "col"} — single-view shortcut
    if "value" in vb and "views" not in vb:
        return None, {}, vb.get("value"), None

    # case: {"primary": ..., "views": {...}}
    if "views" in vb and isinstance(vb["views"], dict):
        views: dict[str, MetricView] = {}
        for key, sub in vb["views"].items():
            if not isinstance(sub, dict):
                continue
            views[key] = MetricView(
                key=key,
                label=str(sub.get("label") or key),
                value_column=sub.get("value"),
                computed_expr=(sub.get("computed") or {}).get("expression"),
                unit=sub.get("unit"),
                format_spec=sub.get("format"),
            )
        return vb.get("primary"), views, None, None

    # case: legacy {motiv, total} pair without views wrapper
    if "motiv" in vb or "total" in vb:
        views = {}
        if "motiv" in vb:
            views["motiv"] = MetricView(
                key="motiv", label="광고노출자", value_column=vb["motiv"]
            )
        if "total" in vb:
            views["total"] = MetricView(
                key="total", label="시장평균", value_column=vb["total"]
            )
        return None, views, None, None

    return None, {}, None, None


def _row_to_def(row: dict[str, Any]) -> MetricDef:
    vb = row.get("view_bindings") or {}
    primary, views, single, status = _parse_view_bindings(vb)
    return MetricDef(
        metric_id    = str(row.get("metric_id") or ""),
        display_name = str(row.get("display_name") or ""),
        display_long = row.get("display_long"),
        tier1        = row.get("tier1"),
        tier3        = row.get("tier3"),
        description  = row.get("description"),
        formula      = row.get("formula"),
        unit         = row.get("unit"),
        format_spec  = row.get("format_spec"),
        channel      = row.get("channel"),
        is_active    = bool(row.get("is_active", True)),
        sort_order   = int(row.get("sort_order", 0) or 0),
        notes        = row.get("notes"),
        primary_view = primary,
        views        = views,
        single_value_column = single,
        status       = status,
        raw_bindings = vb if isinstance(vb, dict) else {},
    )


def _fetch_all() -> list[MetricDef]:
    client = get_client()
    if not client:
        return []
    try:
        res = (
            client.table(TABLE)
            .select("*")
            .eq("is_active", True)
            .order("sort_order")
            .execute()
        )
    except Exception:
        return []
    rows = res.data or []
    return [_row_to_def(r) for r in rows]


def load_catalog(force: bool = False) -> list[MetricDef]:
    """All active metric definitions, cached for an hour."""
    now = time.time()
    if (not force) and _CACHE["rows"] and (now - _CACHE["ts"]) < _CACHE_TTL_SECONDS:
        return _CACHE["rows"]
    rows = _fetch_all()
    _CACHE["rows"] = rows
    _CACHE["ts"] = now
    return rows


def get_metric(metric_id: str) -> MetricDef | None:
    return next((m for m in load_catalog() if m.metric_id == metric_id), None)


def reset_cache() -> None:
    _CACHE["rows"] = []
    _CACHE["ts"] = 0.0


# ── Value extraction ────────────────────────────────────────────────
# Safe arithmetic for `computed.expression` — supports +,-,*,/,(,) and
# nullif(x, 0). No name lookups beyond the supplied view row dict.

_SAFE_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _eval_expr(expr: str, row: dict[str, Any]) -> float | None:
    """Tiny arithmetic evaluator for computed.expression.

    Recognized:
      - column names (A-Za-z_..)  → looked up in row
      - numeric literals
      - operators: + - * / ( )
      - nullif(<expr>, <value>) — returns NULL when <expr> == <value>

    Returns None on any failure (missing column, divide by zero, parse error)."""
    if not expr or not isinstance(expr, str):
        return None
    # Replace nullif(a, b) with a Python expression using a helper sentinel.
    # Simple non-nested case is enough for now.
    try:
        tokens = _tokenize(expr)
        rpn = _shunting_yard(tokens)
        return _eval_rpn(rpn, row)
    except Exception:
        return None


def _tokenize(expr: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    i = 0
    s = expr
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c in "+-*/(),":
            out.append(("op", c))
            i += 1
            continue
        # number
        m = re.match(r"\d+(\.\d+)?", s[i:])
        if m:
            out.append(("num", float(m.group())))
            i += m.end()
            continue
        # identifier (column or function name)
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", s[i:])
        if m:
            name = m.group()
            i += m.end()
            # function call?
            if i < len(s) and s[i] == "(":
                out.append(("func", name))
            else:
                out.append(("ident", name))
            continue
        raise ValueError(f"unexpected char {c!r}")
    return out


_PREC = {"+": 1, "-": 1, "*": 2, "/": 2}


def _shunting_yard(tokens: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    stack: list[tuple[str, Any]] = []
    for tp, val in tokens:
        if tp in ("num", "ident"):
            out.append((tp, val))
        elif tp == "func":
            stack.append((tp, val))
        elif tp == "op" and val == ",":
            while stack and not (stack[-1][0] == "op" and stack[-1][1] == "("):
                out.append(stack.pop())
        elif tp == "op" and val == "(":
            stack.append((tp, val))
        elif tp == "op" and val == ")":
            while stack and not (stack[-1][0] == "op" and stack[-1][1] == "("):
                out.append(stack.pop())
            stack.pop()  # discard "("
            if stack and stack[-1][0] == "func":
                out.append(stack.pop())
        elif tp == "op":  # arithmetic op
            while (
                stack
                and stack[-1][0] == "op"
                and stack[-1][1] in _PREC
                and _PREC[stack[-1][1]] >= _PREC[val]
            ):
                out.append(stack.pop())
            stack.append((tp, val))
    while stack:
        out.append(stack.pop())
    return out


def _eval_rpn(rpn: list[tuple[str, Any]], row: dict[str, Any]) -> float | None:
    stack: list[Any] = []
    ops = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}
    for tp, val in rpn:
        if tp == "num":
            stack.append(float(val))
        elif tp == "ident":
            v = row.get(val)
            try:
                stack.append(float(v) if v is not None else None)
            except (TypeError, ValueError):
                stack.append(None)
        elif tp == "op":
            b = stack.pop()
            a = stack.pop()
            if a is None or b is None:
                stack.append(None)
                continue
            if val == "/" and b == 0:
                stack.append(None)
                continue
            stack.append(ops[val](a, b))
        elif tp == "func":
            # nullif(a, b) — pop in reverse order
            if val == "nullif":
                b = stack.pop()
                a = stack.pop()
                if a == b:
                    stack.append(None)
                else:
                    stack.append(a)
            else:
                # unknown function — fail safely
                return None
    if len(stack) != 1:
        return None
    return stack[0]


def resolve_value(view: MetricView, row: dict[str, Any]) -> float | None:
    """Compute one view's numeric value from a view row."""
    if view.value_column:
        v = row.get(view.value_column)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    if view.computed_expr:
        return _eval_expr(view.computed_expr, row)
    return None


def resolve_primary(md: MetricDef, row: dict[str, Any]) -> float | None:
    """Compute the metric's primary (KPI-card) numeric value."""
    if md.is_planned:
        return None
    if md.single_value_column:
        v = row.get(md.single_value_column)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    pv = md.primary_view_def()
    if pv is None:
        return None
    return resolve_value(pv, row)


def format_value(value: float | None, fmt: str | None, fallback: str = "—") -> str:
    if value is None:
        return fallback
    if fmt and "{" in fmt and "}" in fmt:
        try:
            return fmt.format(value)
        except Exception:
            pass
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.1f}"
