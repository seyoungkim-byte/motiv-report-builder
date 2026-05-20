"""Gemini wrapper — image generation only.

Text/narrative generation moved to ai/narrative.py via the Anthropic SDK.
This module exists solely so hero_image.py can request a hero PNG.

SDK choice: the new `google-genai` package (`from google import genai`).
The legacy `google-generativeai` (>=0.5) rejects `response_modalities`
with cryptic 'Text' / 'Image' errors because it predates multimodal
image output. New SDK uses `GenerateContentConfig(response_modalities=
["TEXT", "IMAGE"])` and Gemini multimodal image models like
`gemini-2.5-flash-image-preview` work cleanly through it.

Model choice note: Imagen models (e.g. imagen-3.0-generate-002) are NOT
callable via `generate_content` — they require the separate /predict
endpoint. Use a Gemini multi-modal model with image output instead.
The model is set via `GEMINI_IMAGE_MODEL` (env / Streamlit Secrets);
default is `gemini-2.5-flash-image-preview`.
"""
from __future__ import annotations

import base64

from config import load_settings


def _load_settings():
    s = load_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return s


def generate_image_bytes(prompt: str) -> bytes:
    """Returns PNG bytes from the configured Gemini image-output model.

    Uses the new google-genai SDK. `response_modalities=["TEXT", "IMAGE"]`
    makes the model emit an inline image part alongside any text.
    """
    s = _load_settings()
    # 사전 검사: imagen-* 계열은 generate_content 로 호출 불가.
    if "imagen" in s.gemini_image_model.lower():
        raise RuntimeError(
            f"GEMINI_IMAGE_MODEL='{s.gemini_image_model}' 은 generate_content API 와 "
            "호환되지 않습니다 (imagen 계열은 별도 /predict 엔드포인트 전용).\n\n"
            "조치: Streamlit Cloud → 앱 Settings → Secrets 에서\n"
            "  GEMINI_IMAGE_MODEL = \"gemini-2.5-flash-image-preview\"\n"
            "로 변경하거나, 그 줄을 통째로 삭제하세요 (코드 default 가 적용됨)."
        )

    # google-genai SDK (new). 옛 google-generativeai 와 별개 패키지.
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as e:
        raise RuntimeError(
            "google-genai SDK 가 설치되지 않았습니다. "
            "requirements.txt 의 `google-genai` 줄을 확인하세요."
        ) from e

    client = genai.Client(api_key=s.gemini_api_key)

    try:
        response = client.models.generate_content(
            model=s.gemini_image_model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )
    except Exception as e:
        # API 호출 자체 실패 (auth/quota/region) — 메시지 그대로 위로.
        raise RuntimeError(f"Gemini API 호출 실패: {e}") from e

    candidates = getattr(response, "candidates", None) or []
    text_returned: list[str] = []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is not None:
                data = getattr(inline, "data", None)
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
        " 1) 모델이 이미지 출력 가능 (현재 알려진: gemini-2.5-flash-image, "
        "gemini-2.5-flash-image-preview, gemini-2.0-flash-exp-image-generation)\n"
        " 2) 해당 모델이 사용자 리전/계정에서 이미지 생성 활성화\n"
        " 3) 프롬프트에 안전 필터에 걸릴 내용이 없는지"
    )
