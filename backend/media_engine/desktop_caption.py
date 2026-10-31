"""Optional low priority caption after a file has been published."""
import os
from pathlib import Path

from google import genai
from google.genai import types

from .provider_quota import QuotaError, call_with_quota


def generate_caption(file):
    if not os.getenv("GOOGLE_API_KEY"):
        raise QuotaError("credentials")
    segment = file.current_version.segments.exclude(derivative_path="").order_by("ordinal").first()
    if not segment:
        return ""
    data = Path(segment.derivative_path).read_bytes()
    mime = {"IMAGE": "image/jpeg", "VIDEO": "video/mp4", "AUDIO": "audio/mpeg"}.get(file.source_type)
    if not mime:
        return ""
    model = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash-lite")
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    response = call_with_quota(model, "caption", 2000 if file.source_type == "IMAGE" else 16000,
        lambda: client.models.generate_content(model=model, contents=[
            types.Part.from_bytes(data=data, mime_type=mime),
            "Describe the visible or audible content in one concise sentence for file search."],),
        estimated_output_tokens=200, user_id=file.user_id, priority="background")
    return (response.text or "").strip()[:1000]
