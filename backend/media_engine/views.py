from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
import uuid
from datetime import datetime
from .database import db
from .auth_service import hash_password, verify_password, create_token



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
    email = data.get('email')
    password = data.get('password')


    # 1. Find the user in the database
    user = db.users.find_one({"email": email})

        # 2. Check if user exists AND password is correct
    if user and verify_password(password, user['password_hash']):
        # 3. Create the Digital Wristband
        token = create_token(user['_id'])
        
        return Response({
            "message": "Login Successful",
            "token": token,
            "user": {
                "name": user['full_name'],
                "email": user['email'],
                "id": user['_id']
            }
        })
    else:
        return Response({"error": "Invalid email or password"}, status=status.HTTP_401_BAD_UNAUTHORIZED)

