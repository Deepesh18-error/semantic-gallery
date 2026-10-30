"""SQLite desktop path. Index jobs remain queued until the worker migration."""
import hashlib
import mimetypes
import os
import time
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL

from django.conf import settings
from django.http import FileResponse, HttpResponse
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .auth_service import create_token, hash_password, token_required, verify_password
from .catalog_store import SQLiteCatalog, SQLiteJobScheduler
from .contracts import SEARCH_SCHEMA_VERSION, SearchRequest, SearchValidationError
from .local_retrieval import SQLiteExactVectors, SQLiteFullText
from .models import CatalogAccount, CatalogCollection, CatalogFile, CatalogJob, CatalogVectorRecord
from .utils import validate_file

catalog = SQLiteCatalog()
scheduler = SQLiteJobScheduler()


def file_document(file):
    version = file.current_version
    collections = list(file.collections.values_list("id", flat=True))
    return {"_id": file.id, "file_id": file.id, "user_id": file.user_id,
            "collection_id": collections[0] if collections else None,
            "media_type": file.source_type, "source_type": file.source_type,
            "file_path": file.path, "file_version": version.fingerprint if version else None,
            "processing_status": file.processing_status, "error_message": file.error_message,
            "ai_description": file.ai_description, "description_status": file.description_status,
            "created_at": file.created_at.isoformat(),
            "file_metadata": {"original_name": file.original_name, "stored_name": Path(file.path).name,
                              "mime_type": file.mime_type, "size_bytes": file.size_bytes,
                              "thumbnail_path": file.thumbnail_path},
            "vector_ids": list(CatalogVectorRecord.objects.filter(
                segment__file_version=version).values_list("id", flat=True)) if version else []}


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
    file = catalog.put_file(file_id=file_id, user_id=request.user_id, source_id=source_id,
        path=str(path), source_type=source_type,
        mime_type=upload.content_type or mimetypes.guess_type(upload.name)[0] or "application/octet-stream",
        fingerprint=digest.hexdigest(), collection_id=collection_id, original_name=upload.name)
    scheduler.enqueue(file.current_version_id, "extract")
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
    path = Path(file.thumbnail_path if thumbnail and file.source_type != "IMAGE" else file.path)
    if thumbnail and file.source_type not in ("IMAGE", "VIDEO", "DOCUMENT"):
        return HttpResponse(status=404)
    if not path.is_file():
        return HttpResponse(status=404)
    allowed_root = Path(settings.MEDIA_VAULT).resolve() if file.source.kind == "managed_import" else Path(file.source.root_path).resolve()
    if not path.resolve().is_relative_to(allowed_root):
        return HttpResponse(status=404)
    return FileResponse(path.open("rb"), content_type="image/jpeg" if thumbnail and file.source_type != "IMAGE" else file.mime_type)


@api_view(["DELETE"])
@token_required
def delete_media_item(request, media_id):
    file = catalog.get_file(media_id, request.user_id)
    if file is None:
        return Response({"error": "File not found"}, status=404)
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
                      "error_message": row.error_message} for row in CatalogFile.objects.filter(
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
    scheduler.enqueue(file.current_version_id, "extract")
    file.processing_status = "PENDING"
    file.save(update_fields=["processing_status"])
    return Response({"message": "Retry queued."})


@api_view(["POST"])
@token_required
def retry_all_pending(request):
    files = CatalogFile.objects.filter(user_id=request.user_id, processing_status="PENDING")
    count = 0
    for file in files:
        if not CatalogJob.objects.filter(file_version=file.current_version, state__in=["queued", "running"]).exists():
            scheduler.enqueue(file.current_version_id, "extract")
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
    hits = SQLiteFullText().search(search.query, user_id=request.user_id,
        collection_id=search.collection_id, source_type=search.file_type, limit=50)
    ranked = {}
    from .models import CatalogSegment
    for hit in hits:
        segment = CatalogSegment.objects.select_related("file_version__file").get(pk=hit["segment_id"])
        ranked[segment.file_version.file_id] = (1.0, segment)
    # Existing imported vectors remain in Pinecone. The local catalog supplies ownership and paths.
    try:
        if not CatalogVectorRecord.objects.filter(backend__in=["sqlite_exact", "pinecone"]).exists():
            raise LookupError("No vector records yet")
        from .gemini_service import get_query_embedding
        vector = get_query_embedding(search.query)
        if vector:
            local_hits = SQLiteExactVectors().query(vector, user_id=request.user_id,
                model_config="gemini-embedding-2-preview:3072:v1",
                collection_id=search.collection_id, source_type=search.file_type, limit=50)
            for hit in local_hits:
                segment = CatalogSegment.objects.select_related("file_version__file").get(pk=hit["segment_id"])
                previous = ranked.get(hit["file_id"])
                if previous is None or hit["score"] > previous[0]:
                    ranked[hit["file_id"]] = (hit["score"], segment)
            if CatalogVectorRecord.objects.filter(backend="pinecone").exists():
                from .pinecone_service import get_pinecone_index
                vector_filter = {"user_id": request.user_id}
                if search.collection_id:
                    vector_filter["collection_id"] = search.collection_id
                if search.file_type:
                    vector_filter["file_type"] = {"$in": ["DOCUMENT", "TEXT"]} if search.file_type == "DOCUMENT" else search.file_type
                response = get_pinecone_index().query(vector=vector, filter=vector_filter, top_k=50, include_metadata=True)
                for match in response.matches:
                    file_id = match.metadata.get("media_item_id")
                    file = catalog.get_file(file_id, request.user_id) if file_id else None
                    if not file or not file.available or (search.file_type and file.source_type != search.file_type):
                        continue
                    if search.collection_id and not file.collections.filter(id=search.collection_id).exists():
                        continue
                    previous = ranked.get(file_id)
                    if previous is None or match.score > previous[0]:
                        ranked[file_id] = (match.score, None)
    except LookupError:
        pass
    except Exception as error:
        # Full text remains available when the provider or Pinecone is offline.
        provider_error = error
    if provider_error and not ranked:
        detail = str(provider_error).lower()
        if "429" in detail or "resource_exhausted" in detail or "rate limit" in detail:
            return Response({"error": "AI provider rate limit reached", "code": "RATE_LIMITED",
                             "schema_version": SEARCH_SCHEMA_VERSION}, status=429)
        return Response({"error": "Search service is unavailable", "code": "SEARCH_UNAVAILABLE",
                         "schema_version": SEARCH_SCHEMA_VERSION}, status=503)
    results = []
    for file_id, (score, segment) in sorted(ranked.items(), key=lambda pair: pair[1][0], reverse=True)[:search.limit]:
        file = catalog.get_file(file_id, request.user_id)
        if file is None or not Path(file.path).is_file():
            continue
        row = file_document(file)
        row.update({"schema_version": SEARCH_SCHEMA_VERSION,
                    "media_item_id": file_id, "original_filename": file.original_name,
                    "file_size_bytes": file.size_bytes, "mime_type": file.mime_type,
                    "similarity_percentage": round(score * 100),
                    "availability": "available" if file.available else "missing",
                    "indexing_coverage": {"status": file.processing_status, "partial": file.processing_status == "PARTIALLY_EMBEDDED"},
                    "representation": segment.representation if segment else "legacy_vector",
                    "evidence_source": "full_text" if segment and segment.text else "vector",
                    "matched_chunk_preview": segment.text[:300] if segment else ""})
        results.append(row)
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    return Response({"schema_version": SEARCH_SCHEMA_VERSION, "query": search.query,
                     "results": results, "total_count": len(results), "search_time_ms": elapsed,
                     "metrics": {"api_wall_ms": elapsed}})
