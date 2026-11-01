"""SQLite desktop path. Index jobs remain queued until the worker migration."""
import hashlib
import mimetypes
import os
import re
import time
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, HttpResponse
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .auth_service import create_token, hash_password, token_required, verify_password
from .catalog_store import SQLiteCatalog, SQLiteJobScheduler
from .contracts import SEARCH_SCHEMA_VERSION, SearchRequest, SearchValidationError
from .local_retrieval import SQLiteExactVectors, SQLiteFullText
from .models import CatalogAccount, CatalogCollection, CatalogFile, CatalogJob, CatalogUsage, CatalogVectorRecord, ProviderQuota
from .utils import validate_file

catalog = SQLiteCatalog()
scheduler = SQLiteJobScheduler()


def file_document(file):
    version = file.current_version
    collections = list(file.collections.values_list("id", flat=True))
    job = CatalogJob.objects.filter(file_version=version).order_by("-created_at").first() if version else None
    return {"_id": file.id, "file_id": file.id, "user_id": file.user_id,
            "collection_id": collections[0] if collections else None,
            "media_type": file.source_type, "source_type": file.source_type,
            "file_path": file.path, "file_version": version.fingerprint if version else None,
            "processing_status": file.processing_status, "error_message": file.error_message,
            "job": {"id": job.id, "stage": job.stage, "state": job.state,
                    "attempts": job.attempts, "progress_done": job.progress_done,
                    "progress_total": job.progress_total, "error_category": job.error_category,
                    "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None} if job else None,
            "ai_description": file.ai_description, "description_status": file.description_status,
            "created_at": file.created_at.isoformat(),
            "file_metadata": {"original_name": file.original_name, "stored_name": Path(file.path).name,
                              "mime_type": file.mime_type, "size_bytes": file.size_bytes,
                              "thumbnail_path": file.thumbnail_path},
            "vector_ids": list(CatalogVectorRecord.objects.filter(
                segment__file_version=version).values_list("id", flat=True)) if version else [],
            "total_vectors": CatalogVectorRecord.objects.filter(segment__file_version=version).count() if version else 0}


@api_view(["POST"])
def register_user(request):
    email = str(request.data.get("email", "")).strip().lower()
    password = request.data.get("password", "")
    if not email or not isinstance(password, str) or not password:
        return Response({"error": "Email and password are required"}, status=400)
    if CatalogAccount.objects.filter(email=email).exists():
        return Response({"error": "User with this email already exists"}, status=400)
    user = CatalogAccount.objects.create(id=str(uuid4()), email=email,
        full_name=str(request.data.get("full_name", "")), password_hash=hash_password(password))
    return Response({"message": "User registered successfully!", "user_id": user.id}, status=201)


@api_view(["POST"])
def login_user(request):
    email = str(request.data.get("email", "")).strip().lower()
    user = CatalogAccount.objects.filter(email=email).first()
    if user is None or not verify_password(str(request.data.get("password", "")), user.password_hash):
        return Response({"error": "Invalid email or password"}, status=401)
    return Response({"message": "Login Successful", "token": create_token(user.id),
                     "user": {"name": user.full_name, "email": user.email, "id": user.id}})


@api_view(["POST"])
@token_required
def create_collection(request):
    name = str(request.data.get("name", "")).strip()
    if not name:
        return Response({"error": "Collection name is required"}, status=400)
    row = catalog.put_collection(collection_id=str(uuid4()), user_id=request.user_id,
        name=name, description=request.data.get("description", ""),
        theme_color=request.data.get("theme_color", "#3498db"),
        icon_tag=request.data.get("icon_tag", "folder"))
    return Response({"message": "Collection created!", "collection": collection_document(row)}, status=201)


def collection_document(row):
    return {"_id": row.id, "user_id": row.user_id, "name": row.name,
            "description": row.description, "theme_color": row.theme_color,
            "icon_tag": row.icon_tag, "created_at": row.created_at.isoformat()}


@api_view(["GET"])
@token_required
def list_collections(request):
    return Response([collection_document(row) for row in CatalogCollection.objects.filter(user_id=request.user_id)])


@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
@token_required
def upload_media(request):
    upload = request.FILES.get("file")
    collection_id = request.data.get("collection_id")
    if not upload or not collection_id:
        return Response({"error": "File and collection_id are required"}, status=400)
    if not CatalogCollection.objects.filter(id=collection_id, user_id=request.user_id).exists():
        return Response({"error": "Collection not found or access denied"}, status=404)
    valid, message, source_type = validate_file(upload)
    if not valid:
        return Response({"error": message}, status=400)
    file_id = str(uuid4())
    root = Path(settings.MEDIA_VAULT).resolve()
    folder = root / request.user_id / collection_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_id}{Path(upload.name).suffix.lower()}"
    digest = hashlib.sha256()
    with path.open("wb") as handle:
        for chunk in upload.chunks():
            handle.write(chunk)
            digest.update(chunk)
    source_id = str(uuid5(NAMESPACE_URL, f"managed:{request.user_id}:{root}"))
    catalog.put_source(source_id=source_id, user_id=request.user_id,
                       kind="managed_import", root_path=str(root))
    with transaction.atomic():
        file = catalog.put_file(file_id=file_id, user_id=request.user_id, source_id=source_id,
            path=str(path), source_type=source_type,
            mime_type=upload.content_type or mimetypes.guess_type(upload.name)[0] or "application/octet-stream",
            fingerprint=digest.hexdigest(), collection_id=collection_id, original_name=upload.name)
        scheduler.enqueue(file.current_version_id, "discover")
    return Response(file_document(file), status=201)


@api_view(["GET"])
@token_required
def list_collection_media(request, collection_id):
    if not CatalogCollection.objects.filter(id=collection_id, user_id=request.user_id).exists():
        return Response({"error": "Collection not found"}, status=404)
    return Response([file_document(row) for row in catalog.list_files(request.user_id, collection_id)])


@api_view(["GET"])
@token_required
def serve_media_file(request, media_id):
    file = catalog.get_file(media_id, request.user_id)
    if file is None:
        return HttpResponse(status=404)
    thumbnail = request.GET.get("thumbnail") == "true"
    path = Path(file.thumbnail_path if thumbnail and file.thumbnail_path else file.path)
    if thumbnail and file.source_type not in ("IMAGE", "VIDEO", "DOCUMENT"):
        return HttpResponse(status=404)
    if not path.is_file():
        return HttpResponse(status=404)
    allowed_root = (Path(settings.MEDIA_VAULT).resolve() if thumbnail and file.thumbnail_path
                    else Path(settings.MEDIA_VAULT).resolve() if file.source.kind == "managed_import"
                    else Path(file.source.root_path).resolve())
    if not path.resolve().is_relative_to(allowed_root):
        return HttpResponse(status=404)
    if thumbnail and file.source_type == "IMAGE" and file.thumbnail_path and not path.resolve().is_relative_to(
            (Path(settings.MEDIA_VAULT) / "derivatives" / file.id / str(file.current_version_id)).resolve()):
        return HttpResponse(status=404)
    return FileResponse(path.open("rb"), content_type="image/jpeg" if thumbnail and file.thumbnail_path else file.mime_type)


@api_view(["DELETE"])
@token_required
def delete_media_item(request, media_id):
    file = catalog.get_file(media_id, request.user_id)
    if file is None:
        return Response({"error": "File not found"}, status=404)
    if CatalogJob.objects.filter(file_version__file=file, state="running").exists():
        CatalogJob.objects.filter(file_version__file=file, state="running").update(cancel_requested=True)
        return Response({"error": "Cancellation requested; delete after the active stage stops"}, status=409)
    CatalogJob.objects.filter(file_version__file=file, state__in=["queued", "paused"]).update(
        state="cancelled", cancel_requested=True)
    if CatalogVectorRecord.objects.filter(segment__file_version__file=file, backend="pinecone").exists():
        try:
            from .pinecone_service import get_pinecone_index
            ids = list(CatalogVectorRecord.objects.filter(segment__file_version__file=file, backend="pinecone").values_list("id", flat=True))
            get_pinecone_index().delete(ids=ids)
        except Exception:
            return Response({"error": "Vector deletion failed; file was retained for retry"}, status=503)
    if file.source.kind == "managed_import":
        for path in (file.path, file.thumbnail_path):
            if path and Path(path).is_file() and Path(path).resolve().is_relative_to(Path(settings.MEDIA_VAULT).resolve()):
                try:
                    Path(path).unlink()
                except OSError:
                    return Response({"error": "File deletion failed; catalog entry was retained"}, status=503)
    file.delete()
    return Response({"message": "Fully erased."})


@api_view(["GET"])
@token_required
def get_media_batch_status(request):
    ids = [value for value in request.query_params.get("ids", "").split(",") if value][:100]
    return Response([{"_id": row.id, "processing_status": row.processing_status,
                      "error_message": row.error_message,
                      "job": file_document(row)["job"]} for row in CatalogFile.objects.filter(
                          id__in=ids, user_id=request.user_id)])


@api_view(["GET"])
@token_required
def get_embedding_info(request, media_id):
    file = catalog.get_file(media_id, request.user_id)
    if file is None:
        return Response({"error": "Not found"}, status=404)
    return Response(file_document(file))


@api_view(["POST"])
@token_required
def retry_embedding(request, media_id):
    file = catalog.get_file(media_id, request.user_id)
    if file is None:
        return Response({"error": "File not found"}, status=404)
    job = CatalogJob.objects.filter(file_version=file.current_version).order_by("-created_at").first()
    if job and job.state in ("failed", "cancelled", "unsupported"):
        scheduler.control(job.id, request.user_id, "retry")
    elif job and job.state == "paused":
        scheduler.control(job.id, request.user_id, "resume")
    elif not job or job.state == "done":
        scheduler.enqueue(file.current_version_id, "discover")
    file.processing_status = "PENDING"
    file.save(update_fields=["processing_status"])
    return Response({"message": "Retry queued."})


@api_view(["POST"])
@token_required
def control_job(request, job_id, action):
    if action not in ("pause", "resume", "cancel", "retry"):
        return Response({"error": "Unknown action"}, status=400)
    try:
        job = scheduler.control(job_id, request.user_id, action)
    except ValueError as error:
        return Response({"error": str(error)}, status=409)
    if job is None:
        return Response({"error": "Job not found"}, status=404)
    file = job.file_version.file
    if action == "pause":
        file.processing_status = "PAUSED"
    elif action == "cancel":
        file.processing_status = "CANCELLED"
    else:
        file.processing_status = "PENDING"
    file.save(update_fields=["processing_status"])
    return Response({"job_id": job.id, "state": job.state, "stage": job.stage})


@api_view(["GET"])
@token_required
def provider_usage(request):
    from django.utils import timezone
    rows = CatalogUsage.objects.filter(user_id=request.user_id, day=timezone.localdate())
    return Response({"day": timezone.localdate().isoformat(),
        "usage": [{"provider": row.provider, "operation": row.operation, "calls": row.calls,
                   "input_units": row.input_units, "output_units": row.output_units,
                   "estimated_spend_usd": str(row.estimated_spend_usd)} for row in rows],
        "configured_models": list(ProviderQuota.objects.filter(enabled=True).values_list("model", flat=True))})


@api_view(["POST"])
@token_required
def retry_all_pending(request):
    files = CatalogFile.objects.filter(user_id=request.user_id, processing_status="PENDING")
    count = 0
    for file in files:
        if not CatalogJob.objects.filter(file_version=file.current_version, state__in=["queued", "running"]).exists():
            scheduler.enqueue(file.current_version_id, "discover")
            count += 1
    return Response({"message": f"Queued {count} items."})


@api_view(["GET"])
@token_required
def get_search_history(request):
    return Response([])


@api_view(["POST"])
@token_required
def search_media(request):
    started = time.perf_counter()
    provider_error = None
    try:
        search = SearchRequest.parse(request.data)
    except SearchValidationError as error:
        return Response({"error": str(error), "code": "INVALID_SEARCH_REQUEST", "schema_version": SEARCH_SCHEMA_VERSION}, status=400)
    if search.collection_id and not CatalogCollection.objects.filter(id=search.collection_id, user_id=request.user_id).exists():
        return Response({"error": "Collection not found or access denied", "code": "COLLECTION_NOT_FOUND"}, status=404)
    from .retrieval_pipeline import filename_hits, fuse, relevant, resolve_constraints
    source_type, lexical_query = resolve_constraints(search, request.data)
    mime_type = ("application/pdf" if request.data.get("file_type") is None and
                 source_type == "DOCUMENT" and
                 re.search(r"\bpdfs?\b", search.query, re.I) else None)
    parsed_at = time.perf_counter()
    keyword_hits = SQLiteFullText().search_evidence(lexical_query, user_id=request.user_id,
        collection_id=search.collection_id, source_type=source_type, mime_type=mime_type, limit=100)
    names = filename_hits(lexical_query, user_id=request.user_id,
        collection_id=search.collection_id, source_type=source_type, mime_type=mime_type)
    keywords_at = time.perf_counter()
    semantic_groups = {}
    from .models import CatalogSegment
    try:
        from .desktop_embedding import (ACTIVE_INDEX_GENERATION, DIMENSIONS, INDEX_GENERATION,
                                        MODEL_CONFIG, PREVIOUS_MODEL_CONFIG)
        active_config = MODEL_CONFIG if ACTIVE_INDEX_GENERATION == INDEX_GENERATION else PREVIOUS_MODEL_CONFIG
        local_available = CatalogVectorRecord.objects.filter(backend="sqlite_exact",
            model_config=active_config, index_generation=ACTIVE_INDEX_GENERATION).exists()
        if not local_available:
            raise LookupError("No vector records yet")
        from .desktop_embedding import embed_query
        vector = embed_query(search.query, request.user_id, generation=ACTIVE_INDEX_GENERATION)
        if vector:
            semantic_groups = SQLiteExactVectors().query_grouped(vector, user_id=request.user_id,
                model_config=active_config, generation=ACTIVE_INDEX_GENERATION,
                collection_id=search.collection_id, source_type=source_type, mime_type=mime_type,
                per_group=20,
                expected_dimensions=DIMENSIONS)
    except LookupError:
        pass
    except Exception as error:
        # Exact local evidence remains available when the provider is offline.
        provider_error = error
    retrieved_at = time.perf_counter()
    candidates = fuse(keyword_hits, semantic_groups, names)
    ranked = [(file_id, entry) for file_id, entry in candidates.items() if relevant(entry)]
    ranked.sort(key=lambda pair: pair[1]["rank_score"], reverse=True)
    if provider_error and not ranked:
        from .provider_quota import QuotaError
        if isinstance(provider_error, QuotaError):
            status_code = 429 if provider_error.category in ("rate_limit", "daily_budget") else 503
            return Response({"error": provider_error.category.replace("_", " "),
                             "code": provider_error.category.upper(),
                             "retry_after_seconds": provider_error.retry_seconds,
                             "schema_version": SEARCH_SCHEMA_VERSION}, status=status_code)
        detail = str(provider_error).lower()
        if "429" in detail or "resource_exhausted" in detail or "rate limit" in detail:
            return Response({"error": "AI provider rate limit reached", "code": "RATE_LIMITED",
                             "schema_version": SEARCH_SCHEMA_VERSION}, status=429)
        return Response({"error": "Search service is unavailable", "code": "SEARCH_UNAVAILABLE",
                         "schema_version": SEARCH_SCHEMA_VERSION}, status=503)
    results = []
    candidate_debug = None
    if request.data.get("debug_candidates") is True:
        candidate_debug = []
        for candidate_file_id, entry in sorted(candidates.items(),
                                               key=lambda pair: pair[1]["rank_score"], reverse=True)[:100]:
            candidate_file = catalog.get_file(candidate_file_id, request.user_id)
            if candidate_file is None:
                continue
            candidate_debug.append({"original_filename": candidate_file.original_name,
                "file_id": candidate_file_id,
                "semantic_cosine": max((details["raw_score"] for channel, details in
                    entry["channels"].items() if channel.startswith("semantic:") and
                    details["raw_score"] is not None), default=None),
                "keyword_coverage": (entry["channels"].get("keyword") or {}).get("matched_terms"),
                "query_terms": (entry["channels"].get("keyword") or {}).get("query_terms"),
                "filename_match": "filename" in entry["channels"],
                "passed_policy": relevant(entry)})
    evidence_ids = {item["segment_id"] for _, entry in ranked[:search.limit]
                    for item in entry["evidence"] if item.get("segment_id")}
    segments = CatalogSegment.objects.in_bulk(evidence_ids)
    for file_id, entry in ranked[:search.limit]:
        file = catalog.get_file(file_id, request.user_id)
        if file is None or not Path(file.path).is_file():
            continue
        supporting = []
        for item in entry["evidence"]:
            segment = segments.get(item.get("segment_id"))
            if segment is None:
                continue
            supporting.append({"representation": segment.representation,
                "source": item["channel"], "semantic_cosine": item.get("score"),
                "keyword_bm25": item.get("bm25"), "text_preview": segment.text[:300],
                "location": {"page_number": segment.page_number,
                    "source_start": segment.source_start, "source_end": segment.source_end,
                    "start_seconds": segment.start_seconds,
                    "end_seconds": segment.end_seconds}})
        keyword_channel = entry["channels"].get("keyword") or {}
        keyword_strong = (keyword_channel.get("matched_terms") is not None and
                          keyword_channel.get("matched_terms") == keyword_channel.get("query_terms"))
        primary = (next((item for item in supporting if item["source"] == "keyword"), None)
                   if keyword_strong else
                   max((item for item in supporting if item["source"].startswith("semantic:")),
                       key=lambda item: item["semantic_cosine"], default=None))
        if primary is None and "filename" not in entry["channels"]:
            primary = supporting[0] if supporting else None
        row = file_document(file)
        row.update({"schema_version": SEARCH_SCHEMA_VERSION,
                    "media_item_id": file_id, "original_filename": file.original_name,
                    "file_size_bytes": file.size_bytes, "mime_type": file.mime_type,
                    "similarity_percentage": None, "rank_score": entry["rank_score"],
                    "retrieval_channels": entry["channels"],
                    "supporting_evidence": supporting,
                    "availability": "available" if file.available else "missing",
                    "indexing_coverage": {"status": file.processing_status,
                        "partial": file.processing_status == "PARTIAL",
                        **(file.current_version.extraction_coverage if file.current_version else {})},
                    "representation": primary["representation"] if primary else "filename",
                    "evidence_source": primary["source"] if primary else "filename",
                    "matched_chunk_preview": primary["text_preview"] if primary else "",
                    "matched_segment_start_time": primary["location"]["start_seconds"] if primary else None,
                    "location": primary["location"] if primary else None})
        results.append(row)
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    from .desktop_embedding import ACTIVE_INDEX_GENERATION
    return Response({"schema_version": SEARCH_SCHEMA_VERSION,
                     "index_generation": ACTIVE_INDEX_GENERATION, "query": search.query,
                     "effective_file_type": source_type, "match_decision": "results" if results else "no_match",
                     "results": results, "total_count": len(results), "search_time_ms": elapsed,
                     **({"candidate_debug": candidate_debug} if candidate_debug is not None else {}),
                     "metrics": {"api_wall_ms": elapsed,
                                 "parse_ms": round((parsed_at - started) * 1000, 2),
                                 "keyword_ms": round((keywords_at - parsed_at) * 1000, 2),
                                 "semantic_ms": round((retrieved_at - keywords_at) * 1000, 2)}})
