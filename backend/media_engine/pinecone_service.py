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

def upsert_to_pinecone(vector, package, media_id, user_id, collection_id, filename, total_chunks=1):
    conn_start = time.time()
    index = get_pinecone_index()
    conn_end = time.time()
    print(f"    ⏱️  [PINECONE INTERNAL] Index connection: {conn_end - conn_start:.3f}s")

    m_data = package.get('metadata', {})
    p_type = package['type']

    # --- PHASE E: chunk position fields ---
    chunk_idx = int(m_data.get('chunk_index', 0))
    segment_idx = int(m_data.get('segment_index', 0))

    vector_id = str(media_id)
    if p_type == "TEXT":
        vector_id += f"_chunk_{chunk_idx}"
    elif p_type in ["VIDEO", "AUDIO"]:
        vector_id += f"_segment_{segment_idx}"

    metadata = {
        "user_id": str(user_id),
        "collection_id": str(collection_id),
        "media_item_id": str(media_id),
        "file_type": p_type,
        "original_filename": filename,
        # --- PHASE E: three new universal fields ---
        "vector_type": "content",           # vs "description" set by description_service
        "chunk_index": chunk_idx if p_type == "TEXT" else segment_idx,
        "total_chunks": int(total_chunks),
        "is_primary": (chunk_idx == 0) if p_type == "TEXT" else (segment_idx == 0),
    }

    if p_type == "TEXT":
        metadata["chunk_text"] = m_data.get('chunk_text', "")[:1000]
        metadata["page_number"] = m_data.get('page_number', 1)
    elif p_type in ["VIDEO", "AUDIO"]:
        metadata["start_time"] = float(m_data.get('start_time', 0.0))
        metadata["end_time"] = float(m_data.get('end_time', 0.0))

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
    Phase E: metadata dicts must now include chunk_index, total_chunks, is_primary, vector_type.
    These are injected by embedding_engine.py before calling this function.
    """
    index = get_pinecone_index()

    formatted_data = [
        (item['id'], item['vector'], item['metadata'])
        for item in vector_data_list
    ]

    upsert_start = time.time()
    try:
        index.upsert(vectors=formatted_data)
        print(f"🌲 [PINECONE] Batch Upsert of {len(formatted_data)} items SUCCESS ({time.time() - upsert_start:.3f}s)")
    except Exception as e:
        print(f"❌ [PINECONE] Batch Upsert FAILED: {str(e)}")
        raise e