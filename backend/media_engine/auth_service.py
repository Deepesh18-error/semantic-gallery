import bcrypt
import jwt
import datetime
import os
import uuid
from .database import db
from functools import wraps
from rest_framework.response import Response
from rest_framework import status



# 1. Password Scrambler (Hashing)
def hash_password(password):
    # Salt is like a "Secret Seasoning" that makes the scramble even harder to crack
    salt = bcrypt.gensalt()
    # Turn the string into bytes and scramble it
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


# 2. Scramble Checker (Verification)
def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


# 3. Wristband Maker (JWT Token)
def create_token(user_id):
    payload = {
        "user_id": str(user_id),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=1), # Valid for 24 hours
        "iat": datetime.datetime.utcnow() # Issued At time
    }
    # Sign the token with our Secret Key from .env
    token = jwt.encode(payload, os.getenv("JWT_SECRET"), algorithm="HS256")
    return token



# --- THE SECURITY GUARD ---
def token_required(f):
    @wraps(f)
    def decorated(request, *args, **kwargs):
        token = None
        # 1. Look for the "Authorization" header
        if 'Authorization' in request.headers:
            # Header looks like "Bearer <token>", so we split and take the 2nd part
            auth_header = request.headers['Authorization'].split(" ")
            if len(auth_header) == 2:
                token = auth_header[1]

        if not token:
            return Response({'error': 'Token is missing! Please login.'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            # 2. Try to decode the wristband
            data = jwt.decode(token, os.getenv("JWT_SECRET"), algorithms=["HS256"])
            # 3. Attach the user_id to the request so the View can use it
            request.user_id = data['user_id']
        except Exception as e:
            return Response({'error': 'Token is invalid or expired!'}, status=status.HTTP_401_UNAUTHORIZED)

        return f(request, *args, **kwargs)
    return decorated