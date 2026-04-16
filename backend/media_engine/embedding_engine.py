import time
from datetime import datetime
from .database import db
from .preprocessor import process_media_item
from .gemini_service import get_embedding, EmbeddingFailedError
from .pinecone_service import upsert_to_pinecone

def run_embedding_pipeline(media_id):
    item = db.media_items.find_one({"_id": media_id})
    if not item:
        print(f"❌ Engine: Media ID {media_id} not found.")
        return

    filename = item['file_metadata']['original_name']
    
    # ⏱️ MASTER TIMER START
    pipeline_start = time.time()
    print(f"\n{'='*60}")
    print(f"⏱️  PIPELINE START: {filename}")
    print(f"⏱️  Start Time: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
    print(f"{'='*60}")

    try:
        db.media_items.update_one(
            {"_id": media_id},
            {"$set": {
                "processing_status": "PROCESSING",
                "embedding_started_at": datetime.utcnow()
            }}
        )

        # ⏱️ STAGE 1: PREPROCESSING
        stage1_start = time.time()
        print(f"\n📦 [STAGE 1] Preprocessing started...")
        
        packages = process_media_item(media_id)
        
        stage1_end = time.time()
        print(f"📦 [STAGE 1] Preprocessing DONE")
        print(f"📦 [STAGE 1] Packages generated: {len(packages)}")
        print(f"📦 [STAGE 1] Time taken: {stage1_end - stage1_start:.3f}s")

        if not packages:
            raise ValueError("Preprocessor returned no data packages.")

        vector_ids_list = []
        total_count = len(packages)

        # ⏱️ STAGE 2+3: GEMINI + PINECONE per package
        for idx, package in enumerate(packages):
            print(f"\n  --- Package {idx + 1}/{total_count} | Type: {package['type']} ---")

            # ⏱️ GEMINI CALL
            gemini_start = time.time()
            print(f"  🤖 [GEMINI]   Request sent at: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
            
            vector = get_embedding(package)
            
            gemini_end = time.time()
            print(f"  🤖 [GEMINI]   Response received at: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
            print(f"  🤖 [GEMINI]   Time taken: {gemini_end - gemini_start:.3f}s")
            print(f"  🤖 [GEMINI]   Vector dimensions: {len(vector)}")

            # ⏱️ PINECONE UPSERT
            pinecone_start = time.time()
            print(f"  🌲 [PINECONE] Upsert started at: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
            
            v_id = upsert_to_pinecone(
                vector=vector,
                package=package,
                media_id=media_id,
                user_id=item['user_id'],
                collection_id=item['collection_id'],
                filename=filename
            )
            
            pinecone_end = time.time()
            print(f"  🌲 [PINECONE] Upsert done at: {datetime.utcnow().strftime('%H:%M:%S.%f')}")
            print(f"  🌲 [PINECONE] Time taken: {pinecone_end - pinecone_start:.3f}s")
            print(f"  🌲 [PINECONE] Vector ID: {v_id}")

            vector_ids_list.append(v_id)

        # ⏱️ STAGE 4: MONGODB STATUS UPDATE
        mongo_start = time.time()
        print(f"\n💾 [MONGODB]  Status update started...")
        
        db.media_items.update_one(
            {"_id": media_id},
            {"$set": {
                "processing_status": "EMBEDDED",
                "total_vectors": total_count,
                "vector_ids": vector_ids_list,
                "embedding_ended_at": datetime.utcnow()
            }}
        )
        
        mongo_end = time.time()
        print(f"💾 [MONGODB]  Status update done")
        print(f"💾 [MONGODB]  Time taken: {mongo_end - mongo_start:.3f}s")

        # ⏱️ MASTER TIMER END
        pipeline_end = time.time()
        total_time = pipeline_end - pipeline_start
        
        print(f"\n{'='*60}")
        print(f"✅ PIPELINE COMPLETE: {filename}")
        print(f"{'='*60}")
        print(f"  📦 Preprocessing:    {stage1_end - stage1_start:.3f}s")
        
        # Per-package breakdown
        gemini_total = sum([0])  # We'll add this below
        pinecone_total = sum([0])
        
        print(f"  🤖 Gemini API:       (see per-package logs above)")
        print(f"  🌲 Pinecone:         (see per-package logs above)")
        print(f"  💾 MongoDB Update:   {mongo_end - mongo_start:.3f}s")
        print(f"  ⏱️  TOTAL TIME:       {total_time:.3f}s ({total_time/60:.2f} min)")
        print(f"{'='*60}\n")

    except EmbeddingFailedError as e:
        pipeline_end = time.time()
        print(f"❌ PIPELINE FAILED at {time.time() - pipeline_start:.3f}s: AI Error: {str(e)}")
        _handle_failure(media_id, f"AI Error: {str(e)}")
    except Exception as e:
        pipeline_end = time.time()
        print(f"❌ PIPELINE FAILED at {time.time() - pipeline_start:.3f}s: System Error: {str(e)}")
        _handle_failure(media_id, f"System Error: {str(e)}")


def _handle_failure(media_id, error_msg):
    print(f"❌ Engine Failure for {media_id}: {error_msg}")
    db.media_items.update_one(
        {"_id": media_id},
        {"$set": {
            "processing_status": "FAILED",
            "error_message": error_msg,
            "embedding_ended_at": datetime.utcnow()
        }}
    )