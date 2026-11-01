"""Versioned public search contract and canonical source file types."""
from dataclasses import dataclass

SEARCH_SCHEMA_VERSION = 1
SOURCE_TYPES = frozenset({"IMAGE", "VIDEO", "AUDIO", "DOCUMENT", "TEXT"})
SEARCH_FILTERS = SOURCE_TYPES | {"ALL"}
REPRESENTATIONS = frozenset({"visual", "text", "ocr_text", "transcript", "audio", "video", "description"})
MAX_QUERY_LENGTH = 500
MAX_RESULTS = 20


class SearchValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SearchRequest:
    query: str
    collection_id: str | None
    file_type: str | None
    limit: int

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            raise SearchValidationError("Search body must be a JSON object")
        query = data.get("query")
        if not isinstance(query, str) or not query.strip():
            raise SearchValidationError("Search query must be non-empty text")
        query = query.strip()
        if len(query) > MAX_QUERY_LENGTH:
            raise SearchValidationError(f"Search query must be at most {MAX_QUERY_LENGTH} characters")
        collection_id = data.get("collection_id")
        if collection_id is not None and (not isinstance(collection_id, str) or not collection_id.strip() or len(collection_id) > 128):
            raise SearchValidationError("Invalid collection_id")
        file_type = data.get("file_type")
        if file_type is not None and (not isinstance(file_type, str) or file_type not in SEARCH_FILTERS):
            raise SearchValidationError("Invalid file_type filter")
        limit = data.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
            raise SearchValidationError(f"limit must be an integer between 1 and {MAX_RESULTS}")
        return cls(query, collection_id.strip() if collection_id else None, None if file_type == "ALL" else file_type, limit)


def representation_for_vector(metadata):
    vector_type = metadata.get("vector_type")
    if vector_type == "description":
        return "description"
    if vector_type == "ocr_text":
        return "ocr_text"
    return {"IMAGE": "visual", "VIDEO": "video", "AUDIO": "audio", "TEXT": "text"}.get(metadata.get("representation_type") or metadata.get("file_type"), "text")


def source_matches_filter(item, file_type):
    return file_type in (None, "ALL") or item.get("media_type") == file_type


def coverage_for_item(item):
    status = item.get("processing_status", "PENDING")
    total = item.get("total_chunks") or item.get("total_vectors") or 0
    failed = len(item.get("failed_chunks") or [])
    return {"status": status, "partial": status == "PARTIALLY_EMBEDDED", "indexed_vectors": item.get("total_vectors", 0),
            "failed_segments": failed, "expected_segments": total if total else None}
