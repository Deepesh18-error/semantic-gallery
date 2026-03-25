import os
from pinecone import Pinecone



# PineCone Connection
def get_pinecone_client():
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("PINECONE_API_KEY is missing from .env file")
    
    # Connect to the cloud service
    pc = Pinecone(api_key=api_key)
    return pc

# Let's test the connection
try:
    pc = get_pinecone_client()
    print("✅ Successfully connected to Pinecone Cloud!")
except Exception as e:
    print(f"❌ Pinecone connection failed: {e}")