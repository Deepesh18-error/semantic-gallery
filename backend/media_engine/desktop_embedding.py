"""Versioned single-object embedding contract for the desktop index."""
import hashlib
import math
import logging
import os
import threading
from contextvars import ContextVar
from array import array
from pathlib import Path

from google import genai
from google.genai import types

from .models import CatalogEmbeddingCache
from .provider_quota import QuotaError, call_with_quota

log = logging.getLogger(__name__)
_clients = threading.local()
QUERY_CACHE_HIT = ContextVar("query_embedding_cache_hit", default=False)
PROVIDER_CALLS = ContextVar("embedding_provider_calls", default=0)


def provider_client():
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        raise QuotaError("credentials")
    if getattr(_clients, "key", None) != key:
        previous = getattr(_clients, "client", None)
        if previous is not None:
            previous.close()
        _clients.client = genai.Client(api_key=key)
        _clients.key = key
    return _clients.client

MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2")
DIMENSIONS = 3072
INDEX_GENERATION = 2
PASSAGE_TOKENS = int(os.getenv("SEMANTIC_GALLERY_PASSAGE_TOKENS", "600"))
MODEL_CONFIG = (f"{MODEL}:d{DIMENSIONS}:phase4-preprocess-v1:p{PASSAGE_TOKENS}:"
                "retrieval-format-v2:l2")
CONFIG_FINGERPRINT = hashlib.sha256(MODEL_CONFIG.encode("utf-8")).hexdigest()[:12]
PREVIOUS_MODEL_CONFIG = f"{MODEL}:3072:search-prefix-v1"
ACTIVE_INDEX_GENERATION = int(os.getenv("SEMANTIC_GALLERY_SEARCH_GENERATION", "1"))
if ACTIVE_INDEX_GENERATION not in (1, INDEX_GENERATION):
    raise ValueError("Unsupported search index generation")


class InvalidEmbedding(ValueError):
    pass


def _normalize(values):
    if values is None or len(values) != DIMENSIONS:
        raise InvalidEmbedding(f"Expected {DIMENSIONS} dimensions")
    try:
        numbers = [float(value) for value in values]
    except (TypeError, ValueError, OverflowError) as error:
        raise InvalidEmbedding("Embedding contains a nonnumeric value") from error
    if not all(math.isfinite(value) for value in numbers):
        raise InvalidEmbedding("Embedding contains a nonfinite value")
    norm = math.sqrt(math.fsum(value * value for value in numbers))
    if not norm or not math.isfinite(norm):
        raise InvalidEmbedding("Embedding has zero or invalid norm")
    return [value / norm for value in numbers]


def _single_embedding(response):
    embeddings = getattr(response, "embeddings", None)
    if embeddings is None or len(embeddings) != 1:
        raise InvalidEmbedding("Provider must return one vector for one semantic object")
    return _normalize(embeddings[0].values)


def _cache_key(kind, payload):
    digest = hashlib.sha256()
    digest.update(kind.encode("utf-8"))
    digest.update(b"\0")
    digest.update(payload)
    return digest.hexdigest()


def _cached(user_id, digest, model_config=MODEL_CONFIG):
    row = CatalogEmbeddingCache.objects.filter(user_id=user_id, model_config=model_config,
                                               content_digest=digest).first()
    if row is None or row.dimensions != DIMENSIONS:
        return None
    vector = array("f")
    vector.frombytes(row.vector_blob)
    return _normalize(vector)


def _save_cache(user_id, digest, values, model_config=MODEL_CONFIG):
    CatalogEmbeddingCache.objects.update_or_create(
        user_id=user_id, model_config=model_config, content_digest=digest,
        defaults={"dimensions": DIMENSIONS, "vector_blob": array("f", values).tobytes()})


def _embed_one(content, estimate, *, user_id, operation, digest, priority, model_config=MODEL_CONFIG):
    cached = _cached(user_id, digest, model_config)
    if cached is not None:
        if operation == "query_embedding":
            QUERY_CACHE_HIT.set(True)
        return cached
    if not os.getenv("GOOGLE_API_KEY"):
        raise QuotaError("credentials")
    client = provider_client()
    response = call_with_quota(MODEL, operation, estimate,
        lambda: client.models.embed_content(model=MODEL, contents=content,
            config=types.EmbedContentConfig(output_dimensionality=DIMENSIONS)),
        user_id=user_id, priority=priority,
        on_send=lambda: PROVIDER_CALLS.set(PROVIDER_CALLS.get() + 1))
    vector = _single_embedding(response)
    try:
        _save_cache(user_id, digest, vector, model_config)
    except Exception:
        log.exception("Embedding cache write failed after successful provider call")
    return vector


def embed_segment(segment, user_id):
    if segment.representation in ("text", "ocr_text", "transcript"):
        title = segment.title or segment.file_version.file.original_name or "none"
        passage = f"{segment.heading}\n{segment.text}" if segment.heading else segment.text
        content = f"title: {title} | text: {passage}"
        digest = _cache_key("document-text-v2", content.encode("utf-8"))
        estimate = max(1, len(content) // 4)
    else:
        path = Path(segment.derivative_path)
        data = path.read_bytes()
        mime = {"visual": "image/jpeg", "video": "video/mp4", "audio": "audio/mpeg"}[segment.representation]
        # One Part represents one file segment. Do not combine unrelated items.
        content = [types.Part.from_bytes(data=data, mime_type=mime)]
        digest = _cache_key(segment.representation + ":" + mime, data)
        seconds = max(1, (segment.end_seconds or 0) - (segment.start_seconds or 0))
        estimate = {"visual": 2000, "video": int(seconds * 270),
                    "audio": int(seconds * 55)}[segment.representation]
    return _embed_one(content, estimate, user_id=user_id, operation="embedding",
                      digest=digest, priority="background")


def embed_query(text, user_id, *, generation=INDEX_GENERATION):
    content = f"task: search result | query: {text}"
    if generation == 1:
        return _embed_one(content, max(1, len(content) // 4), user_id=user_id,
            operation="query_embedding", digest=_cache_key("search-query-v1", content.encode("utf-8")),
            priority="interactive", model_config=PREVIOUS_MODEL_CONFIG)
    if generation != INDEX_GENERATION:
        raise ValueError("Unsupported index generation")
    digest = _cache_key("search-query-v2", content.encode("utf-8"))
    return _embed_one(content, max(1, len(content) // 4), user_id=user_id,
                      operation="query_embedding", digest=digest, priority="interactive")
