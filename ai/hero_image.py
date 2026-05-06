"""Hero image generation via Gemini Imagen.

OpenAI/DALL·E was on the original menu but the user has no OpenAI account,
so the alternative to AI generation is now manual upload — handled in
app.py via st.file_uploader, not here. This module only does Gemini.
"""
from __future__ import annotations

from pathlib import Path

from config import load_settings


_BRAND_STYLE = (
    "Clean editorial hero image for a B2B advertising case study. "
    "Minimalist, airy, brand-safe, no text, no watermarks, "
    "soft studio lighting, wide 16:9 composition."
)


def generate_hero_image(brief: str, *, filename: str = "hero.png") -> Path:
    """Generate a hero image with Gemini Imagen and save under output/hero/."""
    from .gemini_client import generate_image_bytes

    prompt = f"{brief}\n\nStyle: {_BRAND_STYLE}"
    data = generate_image_bytes(prompt)
    s = load_settings()
    out_dir = s.output_dir / "hero"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_bytes(data)
    return path
