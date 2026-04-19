import os
import time
import base64
from datetime import datetime
from google import genai
from google.genai import types

class EmbeddingFailedError(Exception):
    pass

def get_embedding(package):
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is missing from .env")

    client = genai.Client(api_key=api_key)

    mime_map = {
        "IMAGE": "image/jpeg",
        "VIDEO": "video/mp4",
        "AUDIO": "audio/mpeg",
        "DOCUMENT": "application/pdf",
        "TEXT": "text/plain"
    }

    p_type = package['type']
    mime_type = mime_map.get(p_type, "text/plain")
    max_retries = 3
    attempt = 0

    while attempt < max_retries:
        try:
            # ⏱️ PAYLOAD BUILD TIME
            payload_start = time.time()

            content_to_embed = None

            if p_type == "TEXT":
                content_to_embed = package['data']
                payload_size = f"{len(package['data'])} chars"

            elif p_type == "IMAGE":
                image_bytes = base64.b64decode(package['data'])
                content_to_embed = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
                payload_size = f"{len(image_bytes) / 1024:.1f} KB"

            elif p_type in ["VIDEO", "AUDIO"]:
                with open(package['file_path'], 'rb') as f:
                    media_bytes = f.read()
                content_to_embed = types.Part.from_bytes(data=media_bytes, mime_type=mime_type)
                payload_size = f"{len(media_bytes) / 1024:.1f} KB"

            payload_end = time.time()
            print(f"    ⏱️  [GEMINI INTERNAL] Payload built in: {payload_end - payload_start:.3f}s | Size: {payload_size}")

            # ⏱️ ACTUAL NETWORK CALL
            api_call_start = time.time()
            print(f"    ⏱️  [GEMINI INTERNAL] Sending to API at: {datetime.utcnow().strftime('%H:%M:%S.%f')}")

            response = client.models.embed_content(
                model="gemini-embedding-2-preview",
                contents=content_to_embed,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=3072
                )
            )

            api_call_end = time.time()
            network_time = api_call_end - api_call_start
            print(f"    ⏱️  [GEMINI INTERNAL] API responded at: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
            print(f"    ⏱️  [GEMINI INTERNAL] Network round-trip: {network_time:.3f}s")

            # ⏱️ RESPONSE PARSE TIME
            parse_start = time.time()
            vector = response.embeddings[0].values
            parse_end = time.time()
            print(f"    ⏱️  [GEMINI INTERNAL] Response parsed in: {parse_end - parse_start:.4f}s")
            print(f"    ⏱️  [GEMINI INTERNAL] Attempt {attempt + 1} SUCCESS — Total: {parse_end - payload_start:.3f}s")

            return vector

        except Exception as e:
            attempt += 1
            retry_msg = f"Attempt {attempt} FAILED: {str(e)}"
            print(f"    ⚠️  [GEMINI INTERNAL] {retry_msg}")

            if attempt < max_retries:
                wait = 2 * attempt  # Exponential: 2s, 4s
                print(f"    ⏳ [GEMINI INTERNAL] Waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                print(f"    ❌ [GEMINI INTERNAL] All {max_retries} attempts failed.")
                raise EmbeddingFailedError(f"Gemini API failed: {str(e)}")

    return None

def get_query_embedding(text_query):
    """
    Uses task_type="RETRIEVAL_QUERY" to match against stored documents.
    """
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is missing from .env")

    client = genai.Client(api_key=api_key)
    
    max_retries = 3
    attempt = 0

    while attempt < max_retries:
        try:
            # ⏱️ LOGGING START
            payload_start = time.time()
            print(f"    🔍 [SEARCH ENGINE] Embedding Query: '{text_query[:50]}...'")

            # ⏱️ ACTUAL NETWORK CALL
            api_call_start = time.time()
            
            response = client.models.embed_content(
                model="gemini-embedding-2-preview",
                contents=text_query,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY", 
                    output_dimensionality=3072
                )
            )

            api_call_end = time.time()
            vector = response.embeddings[0].values
            
            print(f"    ✅ [SEARCH ENGINE] Query Vectorized in {api_call_end - payload_start:.3f}s")
            return vector

        except Exception as e:
            attempt += 1
            print(f"    ⚠️ [SEARCH ENGINE] Query Attempt {attempt} FAILED: {str(e)}")

            if attempt < max_retries:
                wait = 1 * attempt 
                time.sleep(wait)
            else:
                raise EmbeddingFailedError(f"Gemini Search API failed: {str(e)}")

    return None