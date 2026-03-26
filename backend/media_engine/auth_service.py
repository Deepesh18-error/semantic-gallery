import bcrypt
import jwt
import datetime
import os
import uuid
from .database import db


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