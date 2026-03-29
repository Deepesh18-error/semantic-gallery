import os
import time
import base64
from google import genai
from google.genai import types
from django.conf import settings

# --- CUSTOM EXCEPTION ---
class EmbeddingFailedError(Exception):
    """Raised when all 3 retries for Gemini API fail."""
    pass

# --- THE TRANSLATOR ---

def get_embedding(package):
    """
    Receives a 'Data Package' from preprocessor.py.
    Returns a list of floats (the vector).
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY is missing from .env")

    client = genai.Client(api_key=api_key)
    
    # 1. Standardized Mapping for MIME Types
    mime_map = {
        "IMAGE": "image/jpeg",
        "VIDEO": "video/mp4",
        "AUDIO": "audio/mpeg",
        "DOCUMENT": "application/pdf",
        "TEXT": "text/plain"
    }

    p_type = package['type']
    mime_type = mime_map.get(p_type, "text/plain")

    # 2. RETRY LOGIC (The Safety Net)
    max_retries = 3
    attempt = 0

    while attempt < max_retries:
        try:
            # 3. CONSTRUCT CONTENT (The Input)
            content_to_embed = None

            if p_type == "TEXT":
                # Text is passed directly as a string
                content_to_embed = package['data']
            
            elif p_type == "IMAGE":
                # Images are passed as bytes (decoded from Base64)
                image_bytes = base64.b64decode(package['data'])
                content_to_embed = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

            elif p_type in ["VIDEO", "AUDIO"]:
                # Multimedia is read from the temp file path provided by the Butcher
                with open(package['file_path'], 'rb') as f:
                    media_bytes = f.read()
                content_to_embed = types.Part.from_bytes(data=media_bytes, mime_type=mime_type)

            # 4. CALL THE ENGINE (Gemini Embedding 2)
            # CRITICAL: We use 'RETRIEVAL_DOCUMENT' for storage and 'RETRIEVAL_QUERY' for search
            # Since this is the indexing phase, we use RETRIEVAL_DOCUMENT.
            response = client.models.embed_content(
                model="gemini-embedding-2-preview",
                contents=content_to_embed,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT", 
                    output_dimensionality=3072 # Best Quality
                )
            )

            # 5. RETURN THE VECTOR
            # The API returns a list of vectors; we take the first one
            vector = response.embeddings[0].values
            print(f"✅ Gemini: Successfully embedded {p_type} package.")
            return vector

        except Exception as e:
            attempt += 1
            print(f"⚠️ Gemini Attempt {attempt} failed: {str(e)}")
            
            if attempt < max_retries:
                time.sleep(2) # Wait 2 seconds before trying again
            else:
                print(f"❌ Gemini: Permanent failure after {max_retries} attempts.")
                raise EmbeddingFailedError(f"Gemini API failed: {str(e)}")

    return None