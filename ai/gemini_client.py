"""Gemini wrapper — image generation only.

Text/narrative generation moved to ai/narrative.py via the Anthropic SDK
(Claude follows the prose-primary / DB-supplementary priority rule more
faithfully than Gemini did). This module exists solely so hero_image.py
can keep calling Imagen.
"""
from __future__ import annotations

import google.generativeai as genai

from config import load_settings


def _configure():
    s = load_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=s.gemini_api_key)
    return s


def generate_image_bytes(prompt: str) -> bytes:
    """Returns PNG bytes from the configured image model (Imagen by default)."""
    s = _configure()
    model = genai.GenerativeModel(s.gemini_image_model)
    resp = model.generate_content(prompt)
    for part in getattr(resp, "candidates", []) or []:
        for p in getattr(part.content, "parts", []) or []:
            inline = getattr(p, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data
    raise RuntimeError("Gemini image response contained no inline image data")
