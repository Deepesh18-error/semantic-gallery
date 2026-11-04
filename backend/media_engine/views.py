from rest_framework.decorators import api_view
from django.http import FileResponse  
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.decorators import parser_classes
from django.http import FileResponse, HttpResponse
import mimetypes
from django.conf import settings
import os
import time
import hashlib
import re
import uuid
from datetime import datetime
from .database import db
from .auth_service import hash_password, verify_password, create_token , token_required
from .utils import validate_file
from .embedding_tasks import start_background_embedding
from .pinecone_service import get_pinecone_index
# Add at the top of views.py
from bson import ObjectId
import json
from .search_service import execute_search
from .contracts import SearchRequest, SearchValidationError, SEARCH_SCHEMA_VERSION
from .provider_quota import QuotaError


class MongoJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)

def serialize_doc(doc):
    """Converts a MongoDB document to JSON-safe dict."""
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        elif isinstance(value, list):
            result[key] = [serialize_doc(i) if isinstance(i, dict) else str(i) if isinstance(i, ObjectId) else i for i in value]
        else:
            result[key] = value
    return result


@api_view(['POST'])
def register_user(request):
    data = request.data
    email = data.get('email')
    password = data.get('password')
    full_name = data.get('full_name')

    # 1. Check if user already exists
    if db.users.find_one({"email": email}):
        return Response({"error": "User with this email already exists"}, status=status.HTTP_400_BAD_REQUEST)
    
    # 2. Create the User Document
    user_id = str(uuid.uuid4()) # Generate a unique ID
    new_user = {
        "_id": user_id,
        "full_name": full_name,
        "email": email,
        "password_hash": hash_password(password), # Scramble it!
        "created_at": datetime.utcnow()
    }

    db.users.insert_one(new_user)

    return Response({
        "message": "User registered successfully!",
        "user_id": user_id
    }, status=status.HTTP_201_CREATED)



# --- LOGIN VIEW ---
@api_view(['POST'])
def login_user(request):
    data = request.data
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    print("\n--- 📥 BACKEND: LOGIN ATTEMPT ---")
    user = db.users.find_one({"email": email})

    if user and verify_password(password, user['password_hash']):
        print("✅ AUTH SUCCESS: Password matched!")
        
        # 1. Create the Token
        token = create_token(user['_id'])
        
        # 2. YOU MUST RETURN THIS RESPONSE! 
        # (This was likely missing or indented wrong in your file)
        return Response({
            "message": "Login Successful",
            "token": token,
            "user": {
                "name": user['full_name'],
                "email": user['email'],
                "id": user['_id']
            }
        }, status=status.HTTP_200_OK)
    
    else:
        print("❌ AUTH ERROR: Invalid credentials")
        return Response({
            "error": "Invalid email or password"
        }, status=status.HTTP_401_UNAUTHORIZED)


# --- CREATE COLLECTION ---
@api_view(['POST'])
@token_required # The bouncer checks the token before this code runs
def create_collection(request):
    data = request.data
    name = data.get('name')
    description = data.get('description', "")
    theme_color = data.get('theme_color', "#3498db") # Default blue
    icon_tag = data.get('icon_tag', "folder")

    if not name:
        return Response({"error": "Collection name is required"}, status=status.HTTP_400_BAD_REQUEST)

    # Create the document for MongoDB
    collection_id = str(uuid.uuid4())
    new_collection = {
        "_id": collection_id,
        "user_id": request.user_id, # This comes from the decoded token!
        "name": name,
        "description": description,
        "theme_color": theme_color,
        "icon_tag": icon_tag,
        "created_at": datetime.utcnow()
    }

    db.collections.insert_one(new_collection)

    return Response({
        "message": "Collection created!",
        "collection": new_collection
    }, status=status.HTTP_201_CREATED)


# --- LIST MY COLLECTIONS ---
@api_view(['GET'])
@token_required
def list_collections(request):
    # Search MongoDB for collections where user_id matches the logged-in user
    # Intuition: "Show me only MY lockers, not everyone's."
    user_collections = list(db.collections.find({"user_id": request.user_id}))
    
    return Response(user_collections)



@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@token_required
def upload_media(request):
    # 1. Get data from the "Multipart" form
    file_obj = request.FILES.get('file')
    collection_id = request.data.get('collection_id')

    print(f"DEBUG: File arrived -> {file_obj}")
    print(f"DEBUG: Collection ID arrived -> {collection_id}")
    
    if not file_obj or not collection_id:
        return Response({"error": "File and collection_id are required"}, status=400)

    # 2. Verify collection belongs to this user
    collection = db.collections.find_one({"_id": collection_id, "user_id": request.user_id})
    if not collection:
        return Response({"error": "Collection not found or access denied"}, status=404)

    # 3. Validate File (The Bouncer)
    is_valid, message, media_type = validate_file(file_obj)
    if not is_valid:
        return Response({"error": message}, status=400)

    # 4. Storage Logic (The Vault)
    # Create unique name: a1b2-c3d4.jpg
    file_ext = os.path.splitext(file_obj.name)[1].lower()
    media_id = str(uuid.uuid4())
    stored_name = f"{media_id}{file_ext}"

    # Target path: media_vault / user_id / collection_id / filename
    upload_dir = os.path.join(settings.MEDIA_VAULT, request.user_id, collection_id)
    os.makedirs(upload_dir, exist_ok=True) # Create folders if they don't exist
    
    file_path = os.path.join(upload_dir, stored_name)

    # Write the actual bytes to the hard drive
    file_hash = hashlib.sha256()
    with open(file_path, 'wb+') as destination:
        for chunk in file_obj.chunks():
            destination.write(chunk)
            file_hash.update(chunk)

    # 5. Record in MongoDB (The Catalog Card)
    media_doc = {
        "_id": media_id,
        "user_id": request.user_id,
        "collection_id": collection_id,
        "media_type": media_type,
        "file_path": file_path,
        "file_metadata": {
            "original_name": file_obj.name,
            "stored_name": stored_name,
            "mime_type": file_obj.content_type,
            "size_bytes": file_obj.size
        },
        "file_version": file_hash.hexdigest(),
        "processing_status": "PENDING", 
        
        # --- PHASE 6: INTELLIGENCE CATALOG FIELDS ---
        "ai_description": None,
        "description_status": "PENDING" if media_type in ['IMAGE', 'VIDEO', 'AUDIO'] else "SKIPPED",
        "description_error": None,
        "description_generated_at": None,
        # ---------------------------------------------

        "created_at": datetime.utcnow(),
        "vector_ids": [] 
    }


    db.media_items.insert_one(media_doc)

    # 🚀 TRIGGER THE AI PIPELINE
    # We send the ID in a list so the dispatcher can handle batches
    start_background_embedding([media_id]) 
    
    return Response(media_doc, status=status.HTTP_201_CREATED)
    
    
    
    
# --- 1. THE STREAMER (View File) ---
@api_view(['GET'])
@token_required
def serve_media_file(request, media_id):
    item = db.media_items.find_one({"_id": media_id, "user_id": request.user_id})
    if not item:
        return HttpResponse(status=404)

    is_thumbnail = request.GET.get('thumbnail') == 'true'
    media_type = item.get('media_type', '')

    if is_thumbnail:
        if media_type == 'IMAGE':
            # Images: serve the actual file as its own preview
            file_path = item.get('file_path')
            content_type = item.get('file_metadata', {}).get('mime_type', 'image/jpeg')

        elif media_type in ['VIDEO', 'DOCUMENT']:
            # Videos and PDFs: serve the generated JPG thumbnail
            file_path = item.get('file_metadata', {}).get('thumbnail_path')
            content_type = 'image/jpeg'
            if not file_path:
                print(f"❌ No thumbnail generated yet for {media_type} {media_id}")
                return HttpResponse(status=404)
        else:
            # AUDIO, TEXT — no visual thumbnail
            return HttpResponse(status=404)
    else:
        file_path = item.get('file_path')
        content_type = item.get('file_metadata', {}).get('mime_type', 'application/octet-stream')

    if not file_path:
        return HttpResponse(status=404)

    if not os.path.exists(file_path):
        print(f"❌ Physical file not found: {file_path}")
        return HttpResponse(status=404)

    return FileResponse(open(file_path, 'rb'), content_type=content_type)



@api_view(['POST'])
@token_required
def check_embedding_status(request):
    """
    Receives a list of media_ids.
    Returns their current processing_status.
    Used by the React Frontend for polling.
    """
    media_ids = request.data.get('media_ids', [])
    
    # Fetch only the status and error_message for these IDs
    items = list(db.media_items.find(
        {"_id": {"$in": media_ids}, "user_id": request.user_id},
        {"_id": 1, "processing_status": 1, "error_message": 1}
    ))
    
    return Response(items)



@api_view(['GET'])
@token_required
def get_media_batch_status(request):
    """
    Called by React every 3s.
    Input: ?ids=uuid1,uuid2
    Output: Status of each file.
    """
    ids_string = request.query_params.get('ids', '')
    if not ids_string:
        return Response([])

    media_ids = ids_string.split(',')
    
    # We only fetch the essential status fields for speed
    items = list(db.media_items.find(
        {"_id": {"$in": media_ids}, "user_id": request.user_id},
        {"_id": 1, "processing_status": 1, "error_message": 1, "total_vectors": 1, "failed_chunks": 1}
    ))
    
    return Response(items)

# --- 2. THE SECOND CHANCE (Retry) ---
@api_view(['POST'])
@token_required
def retry_embedding(request, media_id):
    """
    If a file is FAILED, this kicks it back into the pipeline.
    """
    item = db.media_items.find_one({"_id": media_id, "user_id": request.user_id})
    if not item:
        return Response({"error": "File not found"}, status=404)

    # Reset status to PENDING
    db.media_items.update_one(
        {"_id": media_id},
        {"$set": {"processing_status": "PENDING", "error_message": None}}
    )

    # Re-trigger the background thread
    start_background_embedding([media_id])
    
    return Response({"message": "Retry started."})

# --- 3. THE INSPECTOR (Embedding Info) ---
@api_view(['GET'])
@token_required
def get_embedding_info(request, media_id):
    """Returns the deep AI metadata for a file."""
    item = db.media_items.find_one(
        {"_id": media_id, "user_id": request.user_id},
        {
            "processing_status": 1, 
            "total_vectors": 1, 
            "failed_chunks": 1,
            "file_version": 1,
            "vector_ids": 1, 
            "embedding_started_at": 1, 
            "embedding_ended_at": 1,
            "ai_description": 1,
            "description_status": 1,
            "description_error": 1,
            "description_generated_at": 1
        }
    )
    if not item:
        return Response({"error": "Not found"}, status=404)
    return Response(item)

# --- 4. THE ERASER (Updated Delete Logic) ---
@api_view(['DELETE'])
@token_required
def delete_media_item(request, media_id):
    item = db.media_items.find_one({"_id": media_id, "user_id": request.user_id})
    if not item:
        return Response({"error": "File not found"}, status=404)

    vector_ids = item.get('vector_ids', [])
    if vector_ids:
        try:
            index = get_pinecone_index()
            index.delete(ids=vector_ids)
        except Exception:
            return Response({"error": "Vector deletion failed; file was retained for retry", "code": "VECTOR_DELETE_FAILED"}, status=503)

    try:
        for file_path in (item.get('file_path'), item.get('file_metadata', {}).get('thumbnail_path')):
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
    except OSError:
        return Response({"error": "File deletion failed; catalog entry was retained", "code": "FILE_DELETE_FAILED"}, status=503)

    db.media_items.delete_one({"_id": media_id, "user_id": request.user_id})

    return Response({"message": "Fully erased."})


@api_view(['GET'])
@token_required
def list_collection_media(request, collection_id):
    collection = db.collections.find_one({
        "_id": collection_id,
        "user_id": request.user_id
    })
    if not collection:
        return Response({"error": "Collection not found"}, status=404)

    items = list(db.media_items.find(
        {"collection_id": collection_id, "user_id": request.user_id},
        sort=[("created_at", -1)]
    ))
    
    # Serialize all MongoDB docs to JSON-safe format
    safe_items = [serialize_doc(item) for item in items]
    return Response(safe_items)

@api_view(['POST'])
@token_required
def search_media(request):
    """
    Entry point for the Multimodal Search.
    Payload: { "query": "burger photo", "collection_id": "uuid", "file_type": "IMAGE", "limit": 10 }
    """
    request_started = time.perf_counter()
    try:
        search_request = SearchRequest.parse(request.data)
    except SearchValidationError as exc:
        return Response({"error": str(exc), "code": "INVALID_SEARCH_REQUEST", "schema_version": SEARCH_SCHEMA_VERSION}, status=400)
    query_text = search_request.query
    collection_id = search_request.collection_id
    file_type = search_request.file_type
    limit = search_request.limit
    if collection_id and not db.collections.find_one({"_id": collection_id, "user_id": request.user_id}, {"_id": 1}):
        return Response({"error": "Collection not found or access denied", "code": "COLLECTION_NOT_FOUND", "schema_version": SEARCH_SCHEMA_VERSION}, status=404)

    mode = request.data.get("mode", "semantic")
    if mode not in ("local", "semantic"):
        return Response({"error": "mode must be local or semantic"}, status=400)
    if mode == "local":
        filters = {"user_id": request.user_id,
                   "file_metadata.original_name": {"$regex": re.escape(query_text), "$options": "i"}}
        if collection_id:
            filters["collection_id"] = collection_id
        if file_type:
            filters["media_type"] = file_type
        items = db.media_items.find(filters, {"_id": 1, "media_type": 1, "file_metadata": 1}).limit(limit)
        rows = [{"file_id": str(item["_id"]), "media_item_id": str(item["_id"]),
                 "original_filename": item.get("file_metadata", {}).get("original_name", ""),
                 "media_type": item.get("media_type"), "similarity_percentage": None}
                for item in items]
        return Response({"results": rows, "total_count": len(rows), "search_mode": "local",
                         "semantic_status": "not_requested", "search_time_ms": round((time.perf_counter() - request_started) * 1000, 2)})

    print(f"📡 [API] Search request from User {request.user_id}: '{query_text}'")

    # 3. Call the search brain
    constraints_ms = round((time.perf_counter() - request_started) * 1000, 2)
    try:
        search_results = execute_search(
            user_id=request.user_id,
            query_text=query_text,
            collection_id=collection_id,
            file_type=file_type,
            limit=limit
        )
    except Exception as exc:
        if isinstance(exc, QuotaError):
            return Response({"error": exc.category.replace("_", " "),
                             "code": exc.category.upper(),
                             "retry_after_seconds": exc.retry_seconds,
                             "schema_version": SEARCH_SCHEMA_VERSION},
                            status=429 if exc.category in ("rate_limit", "daily_budget") else 503)
        detail = str(exc).lower()
        if "429" in detail or "resource_exhausted" in detail or "rate limit" in detail:
            return Response({"error": "AI provider rate limit reached", "code": "RATE_LIMITED", "schema_version": SEARCH_SCHEMA_VERSION}, status=429)
        return Response({"error": "Search service is unavailable", "code": "SEARCH_UNAVAILABLE", "schema_version": SEARCH_SCHEMA_VERSION}, status=503)

    # 4. Handle internal errors
    if "error" in search_results:
        return Response({"error": search_results["error"], "code": "SEARCH_UNAVAILABLE", "schema_version": SEARCH_SCHEMA_VERSION}, status=503)

    search_results.setdefault("metrics", {}).setdefault("timings_ms", {})["request_constraints"] = constraints_ms
    search_results["metrics"]["api_wall_ms"] = round((time.perf_counter() - request_started) * 1000, 2)
    search_results["schema_version"] = SEARCH_SCHEMA_VERSION

    return Response(search_results, status=status.HTTP_200_OK)


@api_view(['GET'])
@token_required
def get_search_history(request):
    """
    Returns the user's recent search analytics.
    Sorted by most recent first.
    """
    # 1. Query MongoDB search_history collection
    # We fetch the last 20 searches for this specific user
    history = list(db.search_history.find(
        {"user_id": request.user_id},
        {"user_id": 0} # Don't need to send the user_id back to the user
    ).sort("searched_at", -1).limit(20))

    # 2. Use your existing serialize_doc helper to make it JSON-safe
    safe_history = [serialize_doc(h) for h in history]
    
    return Response(safe_history, status=status.HTTP_200_OK)

@api_view(['POST'])
@token_required
def retry_all_pending(request):
    """Re-triggers all PENDING items for the current user."""
    items = list(db.media_items.find(
        {"user_id": request.user_id, "processing_status": "PENDING"},
        {"_id": 1}
    ))
    ids = [str(item["_id"]) for item in items]
    if not ids:
        return Response({"message": "No pending items found."})
    start_background_embedding(ids)
    return Response({"message": f"Re-triggered {len(ids)} items."})
