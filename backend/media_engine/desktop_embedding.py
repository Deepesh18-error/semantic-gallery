"""One provider attempt per segment; the durable worker owns retry timing."""
import os
from pathlib import Path

from google import genai
from google.genai import types

from .provider_quota import QuotaError, call_with_quota

MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2")
MODEL_CONFIG = f"{MODEL}:3072:search-prefix-v1"


def embed_segment(segment, user_id):
    if not os.getenv("GOOGLE_API_KEY"):
        raise QuotaError("credentials")
    if segment.representation in ("text", "ocr_text"):
        content = f"title: {segment.file_version.file.original_name} | text: {segment.text}"
        estimate = max(1, len(content) // 4)
    else:
        path = Path(segment.derivative_path)
        data = path.read_bytes()
        mime = {"visual": "image/jpeg", "video": "video/mp4", "audio": "audio/mpeg"}[segment.representation]
        content = [types.Part.from_bytes(data=data, mime_type=mime)]
        seconds = max(1, (segment.end_seconds or 0) - (segment.start_seconds or 0))
        estimate = {"visual": 2000, "video": int(seconds * 270),
                    "audio": int(seconds * 55)}[segment.representation]
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    response = call_with_quota(MODEL, "embedding", estimate,
        lambda: client.models.embed_content(model=MODEL, contents=content,
            config=types.EmbedContentConfig(output_dimensionality=3072)),
        user_id=user_id, priority="background")
    return response.embeddings[0].values


def embed_query(text, user_id):
    if not os.getenv("GOOGLE_API_KEY"):
        raise QuotaError("credentials")
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    content = f"task: search result | query: {text}"
    response = call_with_quota(MODEL, "query_embedding", max(1, len(content) // 4),
        lambda: client.models.embed_content(model=MODEL, contents=content,
            config=types.EmbedContentConfig(output_dimensionality=3072)),
        user_id=user_id, priority="interactive")
    return response.embeddings[0].values
