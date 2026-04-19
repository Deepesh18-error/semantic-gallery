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
    with open(file_path, 'wb+') as destination:
        for chunk in file_obj.chunks():
            destination.write(chunk)

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
        #  NEW PHASE 3 TRACKING FIELDS 
        "processing_status": "PENDING", # PENDING -> PROCESSING -> EMBEDDED/FAILED
        "embedding_started_at": None,
        "embedding_ended_at": None,
        "total_vectors": 0,
        "vector_ids": [], # We will fill this with Pinecone IDs later
        "error_message": None,
        "created_at": datetime.utcnow()
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
    item = db.media_items.find_one({"_id": media_id})
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



@api_view(['DELETE'])
@token_required # 🚀 Keep your security!
def delete_media_item(request, media_id):
    # 1. Fetch metadata & verify ownership (Security first)
    media_item = db.media_items.find_one({
        "_id": media_id, 
        "user_id": request.user_id 
    })
    
    if not media_item:
        return Response({"error": "File not found or access denied"}, status=404)

    try:
        # 🚀 STEP 1: ERASE FROM PINECONE (The Librarian)
        # Delete all chunks/segments at once using the list we saved
        vector_ids = media_item.get('vector_ids', [])
        if vector_ids:
            try:
                index = get_pinecone_index() # Use your existing helper
                index.delete(ids=vector_ids)
                print(f"🗑️ Pinecone: Erased {len(vector_ids)} vectors.")
            except Exception as e:
                print(f"⚠️ Pinecone Deletion Warning: {e}")

        # 🚀 STEP 2: ERASE FROM DISK (The Physical Vault)
        # A. Delete the main file (Video/PDF/Image)
        file_path = media_item.get('file_path')
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            print(f"🗑️ Vault: Main file erased.")

        # B. Delete the Thumbnail (The missing logic from your version)
        # This cleans up the .jpg files we generate for videos
        thumb_path = media_item.get('file_metadata', {}).get('thumbnail_path')
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
            print("🗑️ Vault: Thumbnail erased.")

        # 🚀 STEP 3: ERASE FROM MONGODB (The Metadata)
        db.media_items.delete_one({"_id": media_id})
        print("🗑️ MongoDB: Metadata purged.")

        return Response({"success": "Item wiped from system"}, status=200)

    except Exception as e:
        print(f"❌ Delete Error: {str(e)}")
        return Response({"error": "Failed to complete full erase"}, status=500)
    


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
        {"_id": 1, "processing_status": 1, "error_message": 1, "total_vectors": 1}
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
            "vector_ids": 1, 
            "embedding_started_at": 1, 
            "embedding_ended_at": 1
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

    # A. DELETE FROM PINECONE FIRST (Cloud)
    vector_ids = item.get('vector_ids', [])
    if vector_ids:
        try:
            index = get_pinecone_index()
            index.delete(ids=vector_ids)
            print(f"🗑️ Deleted {len(vector_ids)} vectors from Pinecone.")
        except Exception as e:
            print(f"⚠️ Pinecone delete failed: {e}")

    # B. DELETE FROM VAULT (Physical)
    file_path = item['file_path']
    if os.path.exists(file_path):
        os.remove(file_path)

    # C. DELETE FROM MONGODB (Metadata)
    db.media_items.delete_one({"_id": media_id})

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
    data = request.data
    query_text = data.get('query', '').strip()
    
    # 1. Validation: Don't waste Gemini API credits on empty strings
    if not query_text:
        return Response({"error": "Search query cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)

    # 2. Extract optional filters
    collection_id = data.get('collection_id') # Can be None
    file_type = data.get('file_type')         # Can be None or "ALL"
    limit = data.get('limit', 10)             # Default to 10

    print(f"📡 [API] Search request from User {request.user_id}: '{query_text}'")

    # 3. Call the search brain
    search_results = execute_search(
        user_id=request.user_id,
        query_text=query_text,
        collection_id=collection_id,
        file_type=file_type,
        limit=limit
    )

    # 4. Handle internal errors
    if "error" in search_results:
        return Response(search_results, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
