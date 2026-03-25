import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# Intuition: We create a single "Connection" that we can use 
# across the whole app.
class MongoDB:
    def __init__(self):
        self.client = None
        self.db = None
        self.connect()

    def connect(self):
        try:
            # 1. Get the connection string from the Vault
            conn_str = os.getenv("MONGO_CONNECTION_STRING", "mongodb://localhost:27017/")
            db_name = os.getenv("MONGO_DB_NAME", "multimodal_db")

            # 2. Open the Direct Line
            self.client = MongoClient(conn_str)
            self.db = self.client[db_name]
            
            print(f"✅ Connected to MongoDB: {db_name}")
        except Exception as e:
            print(f"❌ MongoDB Connection Failed: {e}")

# Create one instance to use everywhere
mongo_db = MongoDB()
# Export the actual database object
db = mongo_db.db