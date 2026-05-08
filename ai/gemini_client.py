"""Gemini wrapper — image generation only.

Text/narrative generation moved to ai/narrative.py via the Anthropic SDK.
This module exists solely so hero_image.py can request a hero PNG.

Model choice note: Imagen models (e.g. imagen-3.0-generate-002) are NOT
callable via `generate_content` — they require the separate /predict
endpoint that the older google-generativeai SDK doesn't expose cleanly.
Use a Gemini multi-modal model with image output instead, e.g.
`gemini-2.5-flash-image-preview`. The model is set via
`GEMINI_IMAGE_MODEL` (env / Streamlit Secrets).
"""
from __future__ import annotations

import base64

import google.generativeai as genai

from config import load_settings


def _configure():
    s = load_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=s.gemini_api_key)
    return s


def generate_image_bytes(prompt: str) -> bytes:
    """Returns PNG bytes from the configured Gemini image-output model.

    The model must support IMAGE output via generate_content. Pass
    `response_modalities=['Text','Image']` so the model includes an
    inline image part in the response (text-only is the default).
    """
    s = _configure()
    model = genai.GenerativeModel(s.gemini_image_model)

    resp = model.generate_content(
        prompt,
        generation_config={"response_modalities": ["Text", "Image"]},
    )

    for cand in getattr(resp, "candidates", []) or []:
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if not data:
                continue
            # SDK normally returns bytes; some versions return base64 str.
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            if isinstance(data, str):
                return base64.b64decode(data)
    raise RuntimeError(
        f"Gemini model '{s.gemini_image_model}' returned no inline image data. "
        "Confirm GEMINI_IMAGE_MODEL is an image-output model "
        "(e.g. gemini-2.5-flash-image-preview)."
    )
