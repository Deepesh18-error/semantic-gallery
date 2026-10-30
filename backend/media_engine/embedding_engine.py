import time
import threading
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from .database import db
from .preprocessor import process_media_item
from .gemini_service import get_embedding, get_ocr_text_embedding, EmbeddingFailedError
from .pinecone_service import upsert_batch_to_pinecone, get_pinecone_index
from .description_service import start_description_generation

# ðŸ›¡ï¸ THE SAFETY GATE: Respect Gemini's 10 RPM limit
gemini_semaphore = threading.Semaphore(5)

def run_embedding_pipeline(media_id):
    item = db.media_items.find_one({"_id": media_id})
    if not item:
        print(f"âŒ Engine: Media ID {media_id} not found.")
        return

    filename = item['file_metadata']['original_name']
    pipeline_start = time.time()
    
    print(f"\n{'='*60}")
    print(f"ðŸš€ TURBO PIPELINE START: {filename}")
    print(f"â±ï¸  Start Time: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
    print(f"{'='*60}")

    try:
        db.media_items.update_one({"_id": media_id}, {"$set": {"processing_status": "PROCESSING"}})

        # STAGE 1: PREPROCESSING
        stage1_start = time.time()
        packages = process_media_item(media_id)
        stage1_end = time.time()
        
        if not packages:
            raise ValueError("Preprocessor returned no packages.")

        for package in packages:
            package.setdefault('metadata', {})['original_filename'] = filename

        # STAGE 2: PARALLEL GEMINI
        stage2_start = time.time()
        all_worker_responses = []

        def gemini_worker(package):
            m_data = package.get('metadata', {})
            p_type = package['type']

            # --- PHASE E: position within the full package list ---
            # For TEXT (document chunks): use chunk_index
            # For VIDEO/AUDIO (segments): use segment_index
            # For IMAGE: always 0 (single package, single vector)
            if p_type == "TEXT":
                p_idx = int(m_data.get('chunk_index', 0))
            elif p_type in ["VIDEO", "AUDIO"]:
                p_idx = int(m_data.get('segment_index', 0))
            else:
                p_idx = 0

            total_chunks = len(packages)   # Total packages this media item produced

            try:
                with gemini_semaphore:
                    vector = get_embedding(package)

                    # Build Unique Vector ID
                    v_id = str(media_id)
                    if p_type == "TEXT":
                        v_id += f"_chunk_{p_idx}"
                    elif p_type in ["VIDEO", "AUDIO"]:
                        v_id += f"_segment_{p_idx}"
                    # IMAGE: no suffix â€” one vector per image, ID = media_id

                    # Build Metadata â€” Phase E fields added here
                    pinecone_metadata = {
                        "user_id": str(item['user_id']),
                        "collection_id": str(item['collection_id']),
                        "media_item_id": str(media_id),
                        "file_type": p_type,
                        "source_type": item['media_type'],
                        "representation_type": p_type,
                        "index_config": "gemini-embedding-2-preview:3072:v1",
                        "original_filename": filename,
                        # --- PHASE E: three new fields on every content vector ---
                        "vector_type": "content",
                        "chunk_index": p_idx,
                        "total_chunks": total_chunks,
                        "is_primary": (p_idx == 0),
                    }

                    if p_type == "TEXT":
                        pinecone_metadata["chunk_index"] = p_idx
                        pinecone_metadata["chunk_text"] = package['data'][:1000]
                    elif p_type in ["VIDEO", "AUDIO"]:
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

        # --- PHASE F: OCR SECOND VECTOR ---
        # For IMAGE packages that have ocr_text, create a separate text vector.
        # This runs AFTER the main batch upsert â€” it's additive, never blocking.
        # Each OCR vector is a separate Pinecone upsert (images rarely have text,
        # so this branch is skipped for the vast majority of images).
        ocr_results = []
        for package in packages:
            if package['type'] != 'IMAGE':
                continue

            ocr_text = package.get('ocr_text', '')
            if not ocr_text:
                continue

            try:
                print(f"  ðŸ“ [PHASE F] OCR text found for image â€” creating second vector...")
                ocr_vector = get_ocr_text_embedding(ocr_text)

                if ocr_vector is None:
                    print(f"  âš ï¸ [PHASE F] OCR embedding returned None. Skipping second vector.")
                    continue

                # OCR vector ID: media_id + "_ocr" suffix
                # Distinct from the main visual vector (media_id with no suffix)
                ocr_vector_id = f"{media_id}_ocr"

                ocr_metadata = {
                    "user_id": str(item['user_id']),
                    "collection_id": str(item['collection_id']),
                    "media_item_id": str(media_id),
                    "file_type": "IMAGE",
                    "source_type": item['media_type'],
                    "representation_type": "OCR_TEXT",
                    "index_config": "gemini-embedding-2-preview:3072:v1",
                    "original_filename": filename,
                    # Phase E fields
                    "vector_type": "ocr_text",   # Distinct from "content" and "description"
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "is_primary": True,
                    # OCR-specific
                    "chunk_text": ocr_text[:1000],  # Stored for search snippet display
                }

                # Single upsert for the OCR vector
                index = get_pinecone_index()
                index.upsert(vectors=[(ocr_vector_id, ocr_vector, ocr_metadata)])
                ocr_results.append({"id": ocr_vector_id})
                print(f"  âœ… [PHASE F] OCR vector upserted: {ocr_vector_id}")

            except Exception as e:
                # Never crash the main pipeline over an OCR vector failure
                print(f"  âš ï¸ [PHASE F] OCR vector creation failed (non-fatal): {str(e)}")
                continue

        if ocr_results:
            print(f"  ðŸ“ [PHASE F] Created {len(ocr_results)} OCR vector(s) for this upload.")

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

        stored_vector_ids = [r['id'] for r in successful_results] + [r['id'] for r in ocr_results]

        db.media_items.update_one(
            {"_id": media_id},
            {"$set": {
                "processing_status": final_status,
                "total_vectors": len(stored_vector_ids),
                "total_chunks": len(packages),
                "vector_ids": stored_vector_ids,
                "failed_chunks": failed_chunks,
                "error_message": error_msg,
                "embedding_ended_at": datetime.utcnow()
            }}
        )
        mongo_end = time.time()

        if final_status == "EMBEDDED" and item['media_type'] in ['IMAGE', 'VIDEO', 'AUDIO']:
            print(f"ðŸš€ [HOOK] Handing {filename} to Intelligence Layer...")
            db.media_items.update_one(
                {"_id": media_id, "description_status": {"$ne": "DONE"}},
                {"$set": {"description_status": "PENDING", "description_error": None}}
            )
            start_description_generation(media_id)
            
        # RECAP LOGS
        total_time = time.time() - pipeline_start
        print(f"\n{'='*60}")
        print(f"âœ… PIPELINE FINISHED: {filename}")
        print(f"{'='*60}")
        print(f"  ðŸ“¦ Stage 1 (Preprocess):   {stage1_end - stage1_start:.3f}s")
        print(f"  ðŸ¤– Stage 2 (Gemini Par):   {stage2_end - stage2_start:.3f}s")
        print(f"  ðŸŒ² Stage 3 (Pinecone Bat): {stage3_end - stage3_start:.3f}s")
        print(f"  ðŸ’¾ Stage 4 (Mongo Sync):   {mongo_end - mongo_start:.3f}s")
        print(f"  ðŸ“Š Final Status:           {final_status}")
        print(f"  â±ï¸  TOTAL LATENCY:         {total_time:.3f}s")
        print(f"{'='*60}\n")
        print("[INDEX_METRICS] " + json.dumps({
            "media_id": str(media_id), "status": final_status,
            "packages": len(packages), "vectors": len(stored_vector_ids),
            "preprocess_ms": round((stage1_end - stage1_start) * 1000, 2),
            "embedding_ms": round((stage2_end - stage2_start) * 1000, 2),
            "vector_upsert_ms": round((stage3_end - stage3_start) * 1000, 2),
            "total_ms": round(total_time * 1000, 2)
        }, sort_keys=True))

    except Exception as e:
        _handle_failure(media_id, str(e))


def _handle_failure(media_id, error_msg):
    print("[INDEX_METRICS] " + json.dumps({"media_id": str(media_id), "status": "FAILED", "error": error_msg}))
    db.media_items.update_one(
        {"_id": media_id},
        {"$set": {
            "processing_status": "FAILED",
            "error_message": error_msg,
            "embedding_ended_at": datetime.utcnow()
        }}
    )

