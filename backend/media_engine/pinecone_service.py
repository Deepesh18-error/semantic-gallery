import time
from datetime import datetime
from pinecone import Pinecone, ServerlessSpec
import os

def get_pinecone_index():
    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX_NAME")
    if not api_key or not index_name:
        raise ValueError("PINECONE_API_KEY or INDEX_NAME missing from .env")
    pc = Pinecone(api_key=api_key)
    return pc.Index(index_name)

def upsert_to_pinecone(vector, package, media_id, user_id, collection_id, filename):
    # ⏱️ INDEX CONNECTION TIME
    conn_start = time.time()
    index = get_pinecone_index()
    conn_end = time.time()
    print(f"    ⏱️  [PINECONE INTERNAL] Index connection: {conn_end - conn_start:.3f}s")

    m_data = package.get('metadata', {})
    p_type = package['type']

    vector_id = str(media_id)
    if p_type == "TEXT":
        vector_id += f"_chunk_{m_data.get('chunk_index', 0)}"
    elif p_type in ["VIDEO", "AUDIO"]:
        vector_id += f"_segment_{m_data.get('segment_index', 0)}"

    metadata = {
        "user_id": str(user_id),
        "collection_id": str(collection_id),
        "media_item_id": str(media_id),
        "file_type": p_type,
        "original_filename": filename,
    }

    if p_type == "TEXT":
        metadata["chunk_index"] = m_data.get('chunk_index', 0)
        metadata["chunk_text"] = m_data.get('chunk_text', "")[:1000]
        metadata["page_number"] = m_data.get('page_number', 1)
    elif p_type in ["VIDEO", "AUDIO"]:
        metadata["segment_index"] = m_data.get('segment_index', 0)
        metadata["start_time"] = float(m_data.get('start_time', 0.0))
        metadata["end_time"] = float(m_data.get('end_time', 0.0))

    # ⏱️ ACTUAL UPSERT NETWORK CALL
    upsert_start = time.time()
    print(f"    ⏱️  [PINECONE INTERNAL] Upsert request sent at: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
    
    try:
        index.upsert(vectors=[(vector_id, vector, metadata)])
        upsert_end = time.time()
        print(f"    ⏱️  [PINECONE INTERNAL] Upsert confirmed at: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
        print(f"    ⏱️  [PINECONE INTERNAL] Upsert network time: {upsert_end - upsert_start:.3f}s")
        print(f"    ⏱️  [PINECONE INTERNAL] Total (conn+upsert): {upsert_end - conn_start:.3f}s")
        return vector_id
    except Exception as e:
        print(f"    ❌ [PINECONE INTERNAL] Upsert FAILED: {str(e)}")
        raise e
    
def upsert_batch_to_pinecone(vector_data_list):
    """
    vector_data_list: List of dicts containing {id, vector, metadata}
    """
    index = get_pinecone_index()
    
    # Format for Pinecone: [(id, vector, metadata), ...]
    formatted_data = [
        (item['id'], item['vector'], item['metadata']) 
        for item in vector_data_list
    ]

    upsert_start = time.time()
    try:
        # One single network call for all chunks!
        index.upsert(vectors=formatted_data)
        print(f"🌲 [PINECONE] Batch Upsert of {len(formatted_data)} items SUCCESS ({time.time() - upsert_start:.3f}s)")
    except Exception as e:
        print(f"❌ [PINECONE] Batch Upsert FAILED: {str(e)}")
        raise e
    