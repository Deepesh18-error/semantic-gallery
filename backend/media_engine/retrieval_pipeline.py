"""Local constraint parsing, file-level rank fusion and final relevance policy."""
import re
from collections import defaultdict

from .models import CatalogFile

RRF_K = 30
GROUPS = ("visual", "text", "ocr_text", "transcript", "audio", "video")
_TYPE_PATTERNS = {
    "IMAGE": r"\b(?:photos?|images?|pictures?|photographs?)\b",
    "DOCUMENT": r"\b(?:pdfs?|documents?)\b",
    "AUDIO": r"\b(?:audio|songs?|recordings?)\b",
    "VIDEO": r"\bvideos?\b",
    "TEXT": r"\b(?:txt|text files?)\b",
}
_FILLER = frozenset({"give", "show", "find", "me", "my", "all", "the", "a", "an", "of",
                     "in", "which", "where", "that", "related", "to", "having", "with",
                     "get", "please", "file", "files"})


def resolve_constraints(search, data):
    # A selected chip, including ALL, is an explicit instruction.
    detected = [kind for kind, pattern in _TYPE_PATTERNS.items()
                if re.search(pattern, search.query, re.I)]
    if data.get("file_type") is not None:
        source_type = search.file_type
    else:
        source_type = detected[0] if len(detected) == 1 else None
    words = [word for word in re.findall(r"\w+", search.query.casefold()) if word not in _FILLER]
    if source_type:
        type_words = {"IMAGE": {"photo", "photos", "image", "images", "picture", "pictures"},
                      "DOCUMENT": {"pdf", "pdfs", "document", "documents"},
                      "AUDIO": {"audio", "song", "songs", "recording", "recordings"},
                      "VIDEO": {"video", "videos"}, "TEXT": {"txt", "text"}}[source_type]
        if data.get("file_type") is not None:
            type_words |= {word for kind in detected for word in {
                "IMAGE": {"photo", "photos", "image", "images", "picture", "pictures"},
                "DOCUMENT": {"pdf", "pdfs", "document", "documents"},
                "AUDIO": {"audio", "song", "songs", "recording", "recordings"},
                "VIDEO": {"video", "videos"}, "TEXT": {"txt", "text"}}[kind]}
        words = [word for word in words if word not in type_words]
    lexical_query = " ".join(words) or search.query
    return source_type, lexical_query


def filename_hits(query, *, user_id, collection_id=None, source_type=None, mime_type=None, limit=50):
    terms = list(dict.fromkeys(re.findall(r"\w+", query.casefold())))[:8]
    if not terms:
        return []
    files = CatalogFile.objects.filter(user_id=user_id, available=True)
    if collection_id:
        files = files.filter(collections__id=collection_id)
    if source_type:
        files = files.filter(source_type=source_type)
    if mime_type:
        files = files.filter(mime_type=mime_type)
    for term in terms:
        files = files.filter(original_name__icontains=term)
    return list(files.order_by("original_name").values_list("id", flat=True)[:limit])


def fuse(keyword_hits, semantic_groups, exact_filenames):
    """RRF on distinct files, with bounded evidence from each independent list."""
    files = defaultdict(lambda: {"rank_score": 0.0, "channels": {}, "evidence": []})
    lists = {"keyword": keyword_hits, "filename": [{"file_id": item} for item in exact_filenames]}
    lists.update({"semantic:" + kind: hits for kind, hits in semantic_groups.items() if kind in GROUPS})
    for channel, rows in lists.items():
        seen = set()
        for row in rows:
            file_id = row["file_id"]
            if file_id in seen:
                if channel == "keyword":
                    details = files[file_id]["channels"][channel]
                    for field in ("matched_terms", "passage_terms", "heading_terms", "title_terms", "filename_terms"):
                        details[field] = max(details.get(field) or 0, row.get(field) or 0)
                if row.get("segment_id") and sum(e["channel"] == channel for e in files[file_id]["evidence"]) < 2:
                    files[file_id]["evidence"].append({"channel": channel, **row})
                continue
            seen.add(file_id)
            rank = len(seen)
            entry = files[file_id]
            contribution = (2 if channel == "filename" else 1) / (RRF_K + rank)
            entry["rank_score"] += contribution
            entry["channels"][channel] = {"rank": rank, "rrf": contribution,
                                           "raw_score": row.get("score"),
                                           "bm25": row.get("bm25"),
                                           "matched_terms": row.get("matched_terms"),
                                           "query_terms": row.get("query_terms"),
                                           "passage_terms": row.get("passage_terms"),
                                           "heading_terms": row.get("heading_terms"),
                                           "title_terms": row.get("title_terms"),
                                           "filename_terms": row.get("filename_terms")}
            if row.get("segment_id"):
                entry["evidence"].append({"channel": channel, **row})
    return files
