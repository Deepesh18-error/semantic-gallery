from pinecone import Pinecone, ServerlessSpec
import os
from django.conf import settings

#  THE LIBRARIAN 

def get_pinecone_index():
    """Initializes and returns the Pinecone Index connection."""
    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX_NAME")

    if not api_key or not index_name:
        raise ValueError("PINECONE_API_KEY or INDEX_NAME missing from .env")

    pc = Pinecone(api_key=api_key)
    return pc.Index(index_name)

def upsert_to_pinecone(vector, package, media_id, user_id, collection_id, filename):
    """
    Constructs the Record ID and Metadata, then pushes to Pinecone.
    """
    index = get_pinecone_index()
    m_data = package.get('metadata', {})
    p_type = package['type']

    # 1. VECTOR ID CONSTRUCTION (The Unique Key)
    # Intuition: Single files use the media_id. Sliced files add a suffix.
    vector_id = str(media_id)
    
    if p_type == "TEXT":
        vector_id += f"_chunk_{m_data.get('chunk_index', 0)}"
    elif p_type in ["VIDEO", "AUDIO"]:
        vector_id += f"_segment_{m_data.get('segment_index', 0)}"

    # 2. CORE METADATA (Common for ALL files)
    # THE BRIDGE: media_item_id connects Pinecone back to MongoDB.
    metadata = {
        "user_id": str(user_id),
        "collection_id": str(collection_id),
        "media_item_id": str(media_id),
        "file_type": p_type,
        "original_filename": filename,
    }

    # 3. TYPE-SPECIFIC METADATA (Enrichment)
    if p_type == "TEXT":
        # Crucial for search previews in the UI
        metadata["chunk_index"] = m_data.get('chunk_index', 0)
        metadata["chunk_text"] = m_data.get('chunk_text', "")[:1000] # Limit size
        metadata["page_number"] = m_data.get('page_number', 1)

    elif p_type in ["VIDEO", "AUDIO"]:
        # Crucial for the 'Jump to Moment' feature
        metadata["segment_index"] = m_data.get('segment_index', 0)
        metadata["start_time"] = float(m_data.get('start_time', 0.0))
        metadata["end_time"] = float(m_data.get('end_time', 0.0))

    # 4. PUSH TO THE CLOUD
    # Record structure: (id, vector, metadata)
    try:
        index.upsert(vectors=[(vector_id, vector, metadata)])
        print(f"🌲 Pinecone: Upserted {vector_id} successfully.")
        return vector_id
    except Exception as e:
        print(f"❌ Pinecone Error: {str(e)}")
        raise e