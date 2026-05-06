from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from config import TEMPLATES_DIR


def build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env
