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

    text_returned: list[str] = []
    for cand in getattr(resp, "candidates", []) or []:
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if data:
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data)
                if isinstance(data, str):
                    return base64.b64decode(data)
            text = getattr(part, "text", None)
            if text:
                text_returned.append(text)

    text_dump = " | ".join(text_returned).strip()[:500] or "(empty)"
    raise RuntimeError(
        f"Gemini 모델 '{s.gemini_image_model}' 가 이미지 대신 텍스트만 반환했습니다.\n\n"
        f"모델 텍스트 응답:\n{text_dump}\n\n"
        "확인사항:\n"
        " 1) GEMINI_IMAGE_MODEL 이 이미지 출력 가능한 모델인지 (현재 알려진: "
        "gemini-2.5-flash-image, gemini-2.5-flash-image-preview, "
        "gemini-2.0-flash-exp-image-generation)\n"
        " 2) 해당 모델이 사용자 리전에서 이미지 생성 활성화돼 있는지\n"
        " 3) 프롬프트에 안전 필터에 걸릴 내용이 없는지"
    )
