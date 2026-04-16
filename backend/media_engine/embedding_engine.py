import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from .database import db
from .preprocessor import process_media_item
from .gemini_service import get_embedding, EmbeddingFailedError
from .pinecone_service import upsert_batch_to_pinecone

# 🛡️ THE SAFETY GATE: Respect Gemini's 10 RPM limit
gemini_semaphore = threading.Semaphore(5)

def run_embedding_pipeline(media_id):
    item = db.media_items.find_one({"_id": media_id})
    if not item:
        print(f"❌ Engine: Media ID {media_id} not found.")
        return

    filename = item['file_metadata']['original_name']
    pipeline_start = time.time()
    
    print(f"\n{'='*60}")
    print(f"🚀 TURBO PIPELINE START: {filename}")
    print(f"⏱️  Start Time: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
    print(f"{'='*60}")

    try:
        db.media_items.update_one({"_id": media_id}, {"$set": {"processing_status": "PROCESSING"}})

        # STAGE 1: PREPROCESSING
        stage1_start = time.time()
        packages = process_media_item(media_id)
        stage1_end = time.time()
        
        if not packages:
            raise ValueError("Preprocessor returned no packages.")

        # STAGE 2: PARALLEL GEMINI
        stage2_start = time.time()
        all_worker_responses = []

        def gemini_worker(package):
            # 1. Identify index safely
            m_data = package.get('metadata', {})
            p_idx = m_data.get('chunk_index', 0) or m_data.get('segment_index', 0)
            p_type = package['type']
            
            try:
                with gemini_semaphore:
                    # 2. Network Call
                    vector = get_embedding(package)
                    
                    # 3. Build Unique ID (FIXED: using m_data defined above)
                    v_id = str(media_id)
                    if p_type == "TEXT": 
                        v_id += f"_chunk_{p_idx}"
                    else:
                        v_id += f"_segment_{p_idx}"

                    # 4. Build Metadata
                    pinecone_metadata = {
                        "user_id": str(item['user_id']),
                        "collection_id": str(item['collection_id']),
                        "media_item_id": str(media_id),
                        "file_type": p_type,
                        "original_filename": filename,
                    }
                    
                    if p_type == "TEXT":
                        pinecone_metadata["chunk_text"] = package['data'][:1000]
                    else:
                        pinecone_metadata["start_time"] = float(m_data.get('start_time', 0.0))
                        pinecone_metadata["end_time"] = float(m_data.get('end_time', 0.0))

                    return {"status": "SUCCESS", "data": {"id": v_id, "vector": vector, "metadata": pinecone_metadata}}
            
            except Exception as e:
                return {"status": "FAILED", "chunk_index": p_idx, "error": str(e)}

        with ThreadPoolExecutor(max_workers=5) as executor:
            all_worker_responses = list(executor.map(gemini_worker, packages))

        stage2_end = time.time()

        # STAGE 3: BATCH PINECONE
        stage3_start = time.time()
        successful_results = [r['data'] for r in all_worker_responses if r['status'] == "SUCCESS"]
        failed_chunks = [r['chunk_index'] for r in all_worker_responses if r['status'] == "FAILED"]

        if successful_results:
            upsert_batch_to_pinecone(successful_results)
            stage3_end = time.time()
        else:
            stage3_end = stage3_start

        # STAGE 4: MONGODB SYNC
        mongo_start = time.time()
        final_status = "EMBEDDED"
        error_msg = None
        
        if not successful_results:
            final_status = "FAILED"
            # Get the first error we found to show in UI
            error_msg = all_worker_responses[0].get('error', "AI calls failed.")
        elif failed_chunks:
            final_status = "PARTIALLY_EMBEDDED"
            error_msg = f"Incomplete: {len(failed_chunks)} chunks failed."

        db.media_items.update_one(
            {"_id": media_id},
            {"$set": {
                "processing_status": final_status,
                "total_vectors": len(successful_results),
                "vector_ids": [r['id'] for r in successful_results],
                "failed_chunks": failed_chunks,
                "error_message": error_msg,
                "embedding_ended_at": datetime.utcnow()
            }}
        )
        mongo_end = time.time()

        # RECAP LOGS
        total_time = time.time() - pipeline_start
        print(f"\n{'='*60}")
        print(f"✅ PIPELINE FINISHED: {filename}")
        print(f"{'='*60}")
        print(f"  📦 Stage 1 (Preprocess):   {stage1_end - stage1_start:.3f}s")
        print(f"  🤖 Stage 2 (Gemini Par):   {stage2_end - stage2_start:.3f}s")
        print(f"  🌲 Stage 3 (Pinecone Bat): {stage3_end - stage3_start:.3f}s")
        print(f"  💾 Stage 4 (Mongo Sync):   {mongo_end - mongo_start:.3f}s")
        print(f"  📊 Final Status:           {final_status}")
        print(f"  ⏱️  TOTAL LATENCY:         {total_time:.3f}s")
        print(f"{'='*60}\n")

    except Exception as e:
        _handle_failure(media_id, str(e))


def _handle_failure(media_id, error_msg):
    db.media_items.update_one(
        {"_id": media_id},
        {"$set": {
            "processing_status": "FAILED",
            "error_message": error_msg,
            "embedding_ended_at": datetime.utcnow()
        }}
    )