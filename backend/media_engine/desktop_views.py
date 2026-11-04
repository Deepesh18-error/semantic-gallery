"""SQLite desktop path. Index jobs remain queued until the worker migration."""
import hashlib
import mimetypes
import os
import re
import json
import time
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4, uuid5, NAMESPACE_URL

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .auth_service import create_token, hash_password, token_required, verify_password
from .catalog_store import SQLiteCatalog, SQLiteJobScheduler
from .contracts import SEARCH_SCHEMA_VERSION, SearchRequest, SearchValidationError
from .local_retrieval import SQLiteExactVectors, SQLiteFullText
from .models import CatalogAccount, CatalogCollection, CatalogFile, CatalogJob, CatalogUsage, CatalogVectorRecord, CatalogSource, ProviderQuota
from .utils import validate_file

catalog = SQLiteCatalog()
scheduler = SQLiteJobScheduler()


def source_document(source):
    return {"id": source.id, "root_path": source.root_path, "kind": source.kind,
            "state": source.state, "paused": source.paused, "error": source.error,
            "collection_id": source.collection_id, "recursive": source.recursive,
            "exclusions": source.exclusions, "settle_seconds": source.settle_seconds,
            "scan_interval_seconds": source.scan_interval_seconds,
            "last_scan_at": source.last_scan_at.isoformat() if source.last_scan_at else None}


@api_view(["GET", "POST"])
@token_required
def sources(request):
    if request.method == "GET":
        return Response([source_document(source) for source in CatalogSource.objects.filter(user_id=request.user_id)])
    if not isinstance(request.data, dict):
        return Response({"error": "Source settings must be a JSON object"}, status=400)
    from .source_sync import register_source
    try:
        root = request.data.get("root_path")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("root_path is required")
        source = register_source(request.user_id, root,
            collection_id=request.data.get("collection_id"), exclusions=request.data.get("exclusions", []),
            recursive=request.data.get("recursive", True))
    except (ValueError, OSError) as error:
        return Response({"error": str(error)}, status=400)
    return Response(source_document(source), status=201)


@api_view(["PATCH", "POST"])
@token_required
def source_settings(request, source_id):
    if not isinstance(request.data, dict):
        return Response({"error": "Source settings must be a JSON object"}, status=400)
    source = CatalogSource.objects.filter(id=source_id, user_id=request.user_id, kind="linked_folder").first()
    if source is None:
        return Response({"error": "Source not found"}, status=404)
    from django.utils import timezone
    action = request.data.get("action")
    if action is not None and action not in ("pause", "resume", "scan"):
        return Response({"error": "action must be pause, resume, or scan"}, status=400)
    for key, minimum, maximum in (("settle_seconds", 1, 60), ("scan_interval_seconds", 1, 3600)):
        if key in request.data:
            value = request.data[key]
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                return Response({"error": f"{key} must be an integer from {minimum} to {maximum}"}, status=400)
            setattr(source, key, value)
    if "recursive" in request.data:
        if not isinstance(request.data["recursive"], bool):
            return Response({"error": "recursive must be a boolean"}, status=400)
        source.recursive = request.data["recursive"]
    if "collection_id" in request.data:
        collection_id = request.data["collection_id"]
        if collection_id is not None and (not isinstance(collection_id, str) or
                not CatalogCollection.objects.filter(id=collection_id, user_id=request.user_id).exists()):
            return Response({"error": "Collection not found or access denied"}, status=400)
        source.collection_id = collection_id
    if "exclusions" in request.data:
        values = request.data["exclusions"]
        if not isinstance(values, list) or len(values) > 100 or not all(isinstance(v, str) and 0 < len(v) <= 255 for v in values):
            return Response({"error": "Invalid exclusion patterns"}, status=400)
        source.exclusions = values
    if action == "pause":
        source.paused, source.state = True, "paused"
    elif action == "resume":
        source.paused, source.state = False, "active"
        CatalogJob.objects.filter(file_version__file__source=source, state="paused",
                                  error_category="source_paused").update(state="queued", error_category="", attempts=0)
    source.next_scan_at = timezone.now()
    source.save(update_fields=["settle_seconds", "scan_interval_seconds", "recursive", "exclusions",
                               "paused", "state", "next_scan_at", "collection"])
    return Response(source_document(source))


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
    temporary = path.with_suffix(path.suffix + ".importing")
    try:
        with temporary.open("wb") as handle:
            for chunk in upload.chunks():
                handle.write(chunk)
                digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        return Response({"error": "Could not copy the imported file"}, status=503)
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
    if file is None or not file.available:
        return HttpResponse(status=404)
    thumbnail = request.GET.get("thumbnail") == "true"
    if thumbnail and not file.thumbnail_path:
        return HttpResponse(status=404)
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
            (Path(settings.MEDIA_VAULT) / "derivatives" / file.id / str(file.published_version_id or file.current_version_id)).resolve()):
        return HttpResponse(status=404)
    try:
        handle = path.open("rb")
    except PermissionError:
        return HttpResponse(status=423)
    except OSError:
        return HttpResponse(status=404)
    return FileResponse(handle, content_type="image/jpeg" if thumbnail and file.thumbnail_path else file.mime_type)


@api_view(["GET"])
@token_required
def audio_playback_link(request, media_id):
    file = catalog.get_file(media_id, request.user_id)
    if file is None or not file.available or file.source_type != "AUDIO":
        return Response({"error": "Audio file is unavailable"}, status=404)
    token = signing.dumps({"file_id": file.id, "user_id": request.user_id},
                          salt="semantic-gallery-audio-playback")
    path = f"/api/media/file/{file.id}/stream/?token={quote(token)}"
    return Response({"url": request.build_absolute_uri(path)})


def stream_audio(request, media_id):
    try:
        identity = signing.loads(request.GET.get("token", ""),
            salt="semantic-gallery-audio-playback", max_age=3600)
    except (signing.BadSignature, signing.SignatureExpired):
        return HttpResponse(status=403)
    if identity.get("file_id") != media_id:
        return HttpResponse(status=403)
    file = catalog.get_file(media_id, identity.get("user_id", ""))
    if file is None or not file.available or file.source_type != "AUDIO":
        return HttpResponse(status=404)
    path = Path(file.path)
    root = Path(settings.MEDIA_VAULT if file.source.kind == "managed_import" else file.source.root_path)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return HttpResponse(status=404)
    try:
        size = path.stat().st_size
        header = request.headers.get("Range", "")
        start, end = 0, size - 1
        if header:
            import re as _re
            match = _re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
            if not match or (not match.group(1) and not match.group(2)):
                response = HttpResponse(status=416)
                response["Content-Range"] = f"bytes */{size}"
                return response
            if not match.group(1):
                start = max(0, size - int(match.group(2)))
            else:
                start = int(match.group(1))
                end = min(size - 1, int(match.group(2))) if match.group(2) else size - 1
            if start >= size or end < start:
                response = HttpResponse(status=416)
                response["Content-Range"] = f"bytes */{size}"
                return response
        handle = path.open("rb")
        handle.seek(start)
    except PermissionError:
        return HttpResponse(status=423)
    except OSError:
        return HttpResponse(status=404)

    def blocks():
        try:
            remaining = end - start + 1
            while remaining:
                data = handle.read(min(1024 * 1024, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            handle.close()

    response = StreamingHttpResponse(blocks(), status=206 if header else 200,
                                     content_type=file.mime_type or "application/octet-stream")
    response["Content-Length"] = str(end - start + 1)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, no-store"
    if header:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response


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
        catalog.queue_reindex(file.id, request.user_id)
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
    mode = request.data.get("mode", "semantic")
    if mode not in ("local", "semantic"):
        return Response({"error": "mode must be local or semantic", "code": "INVALID_SEARCH_REQUEST"}, status=400)
    from . import search_cache
    from .desktop_embedding import (ACTIVE_INDEX_GENERATION, DIMENSIONS, INDEX_GENERATION,
                                    MODEL_CONFIG, PREVIOUS_MODEL_CONFIG, QUERY_CACHE_HIT, PROVIDER_CALLS)
    QUERY_CACHE_HIT.set(False)
    PROVIDER_CALLS.set(0)
    from django.utils import timezone
    indexing_active = (CatalogJob.objects.filter(state="running", lease_until__gt=timezone.now()).exists()
                       if request.data.get("measure_load") is True else None)
    from .retrieval_pipeline import filename_hits, fuse, resolve_constraints
    from .ranking_policy import (accept_features, features_from_entry, load_policy,
                                 score_features)
    active_config = MODEL_CONFIG if ACTIVE_INDEX_GENERATION == INDEX_GENERATION else PREVIOUS_MODEL_CONFIG
    try:
        policy = load_policy()
        if (policy.get("index_generation") not in (None, ACTIVE_INDEX_GENERATION) or
                policy.get("index_config") not in (None, active_config)):
            raise ValueError("Ranking policy was calibrated for a different index configuration")
    except (OSError, ValueError) as error:
        return Response({"error": str(error), "code": "RANK_POLICY_INVALID"}, status=503)
    catalog_revision = search_cache.revision()
    cache_key = (request.user_id, search.query, search.file_type, request.data.get("file_type"),
                 search.collection_id, search.limit, mode, ACTIVE_INDEX_GENERATION, active_config,
                 json.dumps(policy, sort_keys=True), catalog_revision)
    debug = request.data.get("debug_candidates") is True
    cached = None if debug else search_cache.get(cache_key)
    if cached is not None:
        paths = CatalogFile.objects.filter(id__in=[row["file_id"] for row in cached["results"]],
                                           user_id=request.user_id).values_list("path", flat=True)
        if all(Path(path).is_file() for path in paths):
            cached["search_time_ms"] = round((time.perf_counter() - started) * 1000, 2)
            cached["metrics"] = {"api_wall_ms": cached["search_time_ms"], "result_cache_hit": True,
                                 "embedding_provider_calls": 0, "indexing_active": indexing_active}
            return Response(cached)
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
        if mode == "local":
            raise LookupError("Local suggestions do not call the provider")
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
    candidate_files = CatalogFile.objects.filter(id__in=candidates, user_id=request.user_id).select_related("current_version", "published_version").in_bulk()
    for file_id, entry in candidates.items():
        candidate = candidate_files.get(file_id)
        entry["features"] = features_from_entry(entry,
            filename=candidate.original_name if candidate else "", query=lexical_query,
            constraints_ok=bool(candidate and (not source_type or candidate.source_type == source_type)
                                and (not mime_type or candidate.mime_type == mime_type)))
        entry["rank_score"] = score_features(entry["features"], policy)
    ranked = [(file_id, entry) for file_id, entry in candidates.items()
              if accept_features(entry["features"], policy)]
    ranked.sort(key=lambda pair: (-pair[1]["rank_score"],
                                  candidate_files[pair[0]].original_name.casefold()
                                  if pair[0] in candidate_files else pair[0]))
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
        for candidate_file_id, entry in sorted(candidates.items(), key=lambda pair: pair[0])[:500]:
            candidate_file = candidate_files.get(candidate_file_id)
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
                "features": entry["features"],
                "rank_score": entry["rank_score"],
                "passed_policy": accept_features(entry["features"], policy)})
    evidence_ids = {item["segment_id"] for _, entry in ranked[:search.limit]
                    for item in entry["evidence"] if item.get("segment_id")}
    segments = CatalogSegment.objects.in_bulk(evidence_ids)
    for file_id, entry in ranked[:search.limit]:
        file = candidate_files.get(file_id)
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
        row = {"_id": file.id, "file_id": file.id, "media_type": file.source_type,
               "source_type": file.source_type, "processing_status": file.processing_status,
               "file_version": (file.published_version or file.current_version).fingerprint if file.current_version else None,
               "created_at": file.created_at.isoformat(),
               "file_metadata": {"original_name": file.original_name, "size_bytes": file.size_bytes,
                                 "mime_type": file.mime_type}}
        row.update({"schema_version": SEARCH_SCHEMA_VERSION,
                    "media_item_id": file_id, "original_filename": file.original_name,
                    "file_size_bytes": file.size_bytes, "mime_type": file.mime_type,
                    "similarity_percentage": None, "rank_score": entry["rank_score"],
                    "retrieval_channels": entry["channels"],
                    "supporting_evidence": supporting,
                    "availability": "available" if file.available else "missing",
                    "indexing_coverage": {"status": file.processing_status,
                        "partial": file.processing_status == "PARTIAL",
                        "published_generation": file.published_version.generation if file.published_version else None,
                        "pending_generation": file.current_version.generation if file.current_version else None,
                        "stale": bool(file.published_version_id and (
                            file.published_version_id != file.current_version_id or file.processing_status == "WAITING_FOR_FILE")),
                        **((file.published_version or file.current_version).extraction_coverage if file.current_version else {})},
                    "representation": primary["representation"] if primary else "filename",
                    "evidence_source": primary["source"] if primary else "filename",
                    "matched_chunk_preview": primary["text_preview"] if primary else "",
                    "matched_segment_start_time": primary["location"]["start_seconds"] if primary else None,
                    "location": primary["location"] if primary else None})
        results.append(row)
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    from .desktop_embedding import ACTIVE_INDEX_GENERATION
    payload = {"schema_version": SEARCH_SCHEMA_VERSION,
                     "search_mode": mode, "semantic_status": "not_requested" if mode == "local" else
                        "unavailable" if provider_error else "complete" if semantic_groups else "not_indexed",
                     "index_generation": ACTIVE_INDEX_GENERATION, "query": search.query,
                     "index_config": active_config,
                     "ranking_policy_version": policy["schema_version"],
                     "effective_file_type": source_type, "match_decision": "results" if results else "no_match",
                     "results": results, "total_count": len(results), "search_time_ms": elapsed,
                     **({"candidate_debug": candidate_debug,
                         "candidate_pool_count": len(candidates),
                         "candidate_debug_truncated": len(candidates) > 500}
                        if candidate_debug is not None else {}),
                     "metrics": {"api_wall_ms": elapsed,
                                 "parse_ms": round((parsed_at - started) * 1000, 2),
                                 "keyword_ms": round((keywords_at - parsed_at) * 1000, 2),
                                 "semantic_ms": round((retrieved_at - keywords_at) * 1000, 2),
                                 "embedding_provider_calls": PROVIDER_CALLS.get(),
                                 "query_embedding_cache_hit": QUERY_CACHE_HIT.get(),
                                 "indexing_active": indexing_active,
                                 "result_cache_hit": False}}
    if not debug and provider_error is None and search_cache.revision() == catalog_revision:
        search_cache.put(cache_key, payload)
    return Response(payload)
