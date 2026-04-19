import time
from .gemini_service import get_query_embedding
from .pinecone_service import get_pinecone_index
from .database import db
import threading
from datetime import datetime

def _log_search_history_async(user_id, query_text, results_count, result_media_ids):
    """
    Worker function for Stage F. Runs in a daemon thread 
    to prevent slowing down the search response.
    """
    try:
        history_doc = {
            "user_id": str(user_id),
            "query_text": query_text,
            "results_count": results_count,
            "result_media_ids": result_media_ids,
            "searched_at": datetime.utcnow()
        }
        db.search_history.insert_one(history_doc)
    except Exception as e:
        print(f"⚠️ [HISTORY] Failed to log search: {e}")
        
       
def execute_search(user_id, query_text, collection_id=None, file_type=None, limit=10):
    """
    Stages: A (Embed), B (Pinecone), C (Dedup), D (Mongo), E (Assembly)
    """
    search_start = time.time()
    print(f"\n🔍 [SEARCH PIPELINE] Starting search for: '{query_text}'")

    #  STAGE A: EMBED THE QUERY 
    # This is our only external API call
    stage_a_start = time.time()
    query_vector = get_query_embedding(query_text)
    stage_a_end = time.time()

    if not query_vector:
        return {"error": "Failed to generate query embedding"}

    #  STAGE B: QUERY PINECONE 
    # We ask for Top 50 to allow for document/video deduplication later
    stage_b_start = time.time()
    try:
        index = get_pinecone_index()
        
        # 1. Build the Secure Metadata Filter
        # Crucial: user_id ensures users only see their own data
        pinecone_filter = {
            "user_id": str(user_id)
        }
        
        # 2. Add Optional Filters (Scoping search)
        if collection_id:
            pinecone_filter["collection_id"] = str(collection_id)
        
        if file_type and file_type != "ALL":
            pinecone_filter["file_type"] = file_type

        # 3. Execute Pinecone Search
        pinecone_response = index.query(
            vector=query_vector,
            filter=pinecone_filter,
            top_k=50, # Ask for 50 raw vectors
            include_metadata=True
        )
        
        raw_results = pinecone_response['matches']
        stage_b_end = time.time()
        
        print(f"🌲 [SEARCH PIPELINE] Pinecone found {len(raw_results)} raw vector matches.")

    except Exception as e:
        print(f"❌ [SEARCH PIPELINE] Pinecone stage failed: {e}")
        return {"error": "Vector database query failed"}

    #  SUMMARY LOGS 
    print(f"  ⏱️  Stage A (Gemini):   {stage_a_end - stage_a_start:.3f}s")
    print(f"  ⏱️  Stage B (Pinecone): {stage_b_end - stage_b_start:.3f}s")

    
    # --- STAGE C: DEDUPLICATION (The In-Memory Loop) ---
    stage_c_start = time.time()
    
    # This map will store: { "media_item_id": { "score": 0.91, "metadata": {...} } }
    unique_media_map = {}

    for match in raw_results:
        metadata = match['metadata']
        m_id = metadata['media_item_id']
        score = match['score']

        # Logic: If we haven't seen this file yet, OR this chunk has a higher score
        # than the chunk we previously found for this file, keep this one.
        if m_id not in unique_media_map or score > unique_media_map[m_id]['score']:
            unique_media_map[m_id] = {
                "score": score,
                "pinecone_metadata": metadata # This contains chunk_text or start_time
            }

    stage_c_end = time.time()
    unique_ids = list(unique_media_map.keys())
    print(f"🎯 [SEARCH PIPELINE] Deduplication: {len(raw_results)} vectors -> {len(unique_ids)} unique files.")

    # --- STAGE D: MONGODB ENRICHMENT (The Single Trip) ---
    stage_d_start = time.time()
    
    # 1. Fire ONE single query to MongoDB using the $in operator
    # This fetches all full metadata for the unique IDs in one go.
    mongo_items = list(db.media_items.find(
        {"_id": {"$in": unique_ids}},
        # Only fetch what the frontend needs to keep the payload small
        {
            "user_id": 0, # Security: don't send this back
            "vector_ids": 0, 
            "file_path": 0 # Security: don't send internal server paths
        }
    ))

    # 2. Merge the math scores (Pinecone) with the human data (Mongo)
    merged_results = []
    for item in mongo_items:
        m_id = str(item['_id'])
        sim_data = unique_media_map.get(m_id)
        
        if sim_data:
            # Add the similarity data to the Mongo object
            item['similarity_score'] = sim_data['score']
            
            # Attach the specific chunk/segment data that caused the match
            # This is used for the "Snippet Preview" or "Video Timestamp"
            item['winning_metadata'] = sim_data['pinecone_metadata']
            merged_results.append(item)

    stage_d_end = time.time()
    print(f"💾 [SEARCH PIPELINE] MongoDB enriched {len(merged_results)} items.")

    # --- SUMMARY LOGS UPDATED ---
    print(f"  ⏱️  Stage C (Dedup):   {(stage_c_end - stage_c_start) * 1000:.2f}ms")
    print(f"  ⏱️  Stage D (Mongo):   {(stage_d_end - stage_d_start) * 1000:.2f}ms")
    
    stage_e_start = time.time()
    
    # 1. Sort by score descending (Highest match first)
    merged_results.sort(key=lambda x: x['similarity_score'], reverse=True)

    final_results = []
    # Ensure limit doesn't exceed 20 as per methodology
    max_limit = min(limit, 20)

    for item in merged_results:
        score = item['similarity_score']

        # 2. Filter Noise (The 30% Threshold)
        # If the semantic similarity is below 0.30, it's usually irrelevant
        if score < 0.30:
            continue

        win_meta = item.get('winning_metadata', {})
        
        # 3. Build Clean Result Object for Frontend
        assembled = {
            "media_item_id": str(item['_id']),
            "similarity_percentage": round(score * 100), # 0.91 -> 91
            "media_type": item['media_type'],
            "original_filename": item['file_metadata'].get('original_name'),
            "collection_id": str(item['collection_id']),
            # Convert datetime to string for JSON serialization
            "created_at": item['created_at'].isoformat() if isinstance(item['created_at'], datetime) else item['created_at'],
            "file_size_bytes": item['file_metadata'].get('size_bytes')
        }

        # 4. Type-Specific Enrichment
        if item['media_type'] == 'DOCUMENT':
            # Extract the snippet from the chunk that matched
            chunk_text = win_meta.get('chunk_text', "")
            assembled["matched_chunk_preview"] = chunk_text[:300] # Truncate to 300 chars

        elif item['media_type'] in ['VIDEO', 'AUDIO']:
            # Provide the deep-link timestamps
            assembled["matched_segment_start_time"] = win_meta.get('start_time', 0.0)
            assembled["matched_segment_end_time"] = win_meta.get('end_time', 0.0)

        final_results.append(assembled)

        # 5. Respect the Limit
        if len(final_results) >= max_limit:
            break

    stage_e_end = time.time()

    # --- STAGE F: SEARCH HISTORY (Async, Non-Blocking) ---
    # We fire this and immediately return the results to the user.
    history_thread = threading.Thread(
        target=_log_search_history_async,
        args=(
            user_id, 
            query_text, 
            len(final_results), 
            [r['media_item_id'] for r in final_results]
        ),
        daemon=True
    )
    history_thread.start()

    # FINAL RETURN
    total_time_ms = int((time.time() - search_start) * 1000)
    print(f"🏁 [SEARCH PIPELINE] Search Complete. Returned {len(final_results)} items in {total_time_ms}ms.")
    
    return {
        "query": query_text,
        "results": final_results,
        "total_count": len(final_results),
        "search_time_ms": total_time_ms
    }