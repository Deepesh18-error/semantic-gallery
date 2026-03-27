from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.decorators import parser_classes
import mimetypes
from django.conf import settings
import os
import uuid
from datetime import datetime
from .database import db
from .auth_service import hash_password, verify_password, create_token , token_required
from .utils import validate_file


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
        "file_path": file_path, # The address on disk
        "file_metadata": {
            "original_name": file_obj.name,
            "stored_name": stored_name,
            "mime_type": file_obj.content_type,
            "size_bytes": file_obj.size
        },
        "processing_status": "PENDING", # For Phase 3 AI
        "ai_data": {"description": None, "tags": []},
        "created_at": datetime.utcnow()
    }

    db.media_items.insert_one(media_doc)

    return Response({
        "message": "File uploaded and stored in vault!",
        "media_id": media_id,
        "status": "PENDING"
    }, status=201)
    
    
    
    
# --- 1. THE STREAMER (View File) ---
@api_view(['GET'])
@token_required
def serve_media_file(request, media_id):
    # 1. Find the metadata in MongoDB
    media_item = db.media_items.find_one({"_id": media_id, "user_id": request.user_id})
    
    if not media_item:
        return Response({"error": "File not found or access denied"}, status=404)

    file_path = media_item['file_path']

    # 2. Check if the file actually exists on the hard drive
    if not os.path.exists(file_path):
        return Response({"error": "Physical file missing from vault"}, status=404)

    # 3. Detect the "Flavor" (MIME type) of the file (e.g., image/jpeg)
    content_type, _ = mimetypes.guess_type(file_path)
    
    # 4. Stream the bytes to the browser
    # Intuition: This opens a "Live Pipe" between the vault and the user's screen
    response = FileResponse(open(file_path, 'rb'), content_type=content_type)
    return response


# --- 2. THE ERASER (Delete File) ---
@api_view(['DELETE'])
@token_required
def delete_media_item(request, media_id):
    # 1. Find the file first to get the path
    media_item = db.media_items.find_one({"_id": media_id, "user_id": request.user_id})
    
    if not media_item:
        return Response({"error": "File not found or access denied"}, status=404)

    file_path = media_item['file_path']

    # 2. DELETE FROM HARD DRIVE (The Physical Part)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"🗑️ Physically deleted: {file_path}")
    except Exception as e:
        # We log the error but continue to delete the DB record
        print(f"⚠️ Error deleting physical file: {e}")

    # 3. DELETE FROM MONGODB (The Metadata Part)
    db.media_items.delete_one({"_id": media_id})

    # Note: In Phase 4, we will add Step 4: Delete from Pinecone here!

    return Response({"message": "File and metadata deleted successfully"}, status=200)