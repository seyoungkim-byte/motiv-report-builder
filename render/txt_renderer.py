from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from .jinja_env import build_env


def render_press_txt(context: dict[str, Any], out_path: Path) -> Path:
    env = build_env()
    tpl = env.get_template("press_release.txt.j2")
    ctx = dict(context)
    ctx.setdefault("year", _dt.date.today().year)
    out_path.write_text(tpl.render(**ctx), encoding="utf-8")
    return out_path
