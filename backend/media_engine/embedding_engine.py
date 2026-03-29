import os
from datetime import datetime
from .database import db
from .preprocessor import process_media_item
from .gemini_service import get_embedding, EmbeddingFailedError
from .pinecone_service import upsert_to_pinecone

# --- THE ORCHESTRATOR ---

def run_embedding_pipeline(media_id):
    """
    The Master Logic for a single file.
    Takes a media_id, runs it through the 'Car Wash', 
    and updates MongoDB at every stage.
    """
    # 1. FETCH & INITIALIZE
    item = db.media_items.find_one({"_id": media_id})
    if not item:
        print(f"❌ Engine: Media ID {media_id} not found.")
        return

    print(f"🚀 Engine: Starting pipeline for {item['file_metadata']['original_name']}")

    try:
        # 2. UPDATE STATUS -> PROCESSING
        # This triggers the 'Pulsing Blue' badge on the React UI
        db.media_items.update_one(
            {"_id": media_id},
            {"$set": {
                "processing_status": "PROCESSING",
                "embedding_started_at": datetime.utcnow()
            }}
        )

        # 3. CALL THE BUTCHER (Preprocessing)
        # Returns a list of standardized 'Data Packages'
        packages = process_media_item(media_id)
        if not packages:
            raise ValueError("Preprocessor returned no data packages.")

        vector_ids_list = []
        total_count = len(packages)

        # 4. LOOP THROUGH THE PACKAGES (AI & Vector Storage)
        for package in packages:
            # A. CALL THE TRANSLATOR (Gemini API)
            vector = get_embedding(package)

            # B. CALL THE LIBRARIAN (Pinecone Upsert)
            # We pass all IDs and names so Pinecone can build the Metadata bridge
            v_id = upsert_to_pinecone(
                vector=vector,
                package=package,
                media_id=media_id,
                user_id=item['user_id'],
                collection_id=item['collection_id'],
                filename=item['file_metadata']['original_name']
            )
            
            # Store the Pinecone ID locally so we can delete it later if needed
            vector_ids_list.append(v_id)

        # 5. UPDATE STATUS -> EMBEDDED (The 'Finish Line')
        # This triggers the 'Green Checkmark' on the React UI
        db.media_items.update_one(
            {"_id": media_id},
            {"$set": {
                "processing_status": "EMBEDDED",
                "total_vectors": total_count,
                "vector_ids": vector_ids_list, # The 'Eraser' list
                "embedding_ended_at": datetime.utcnow()
            }}
        )
        print(f"✅ Engine: File {media_id} is now fully searchable.")

    except EmbeddingFailedError as e:
        # Catch AI-specific failures (Rate limits, etc.)
        _handle_failure(media_id, f"AI Error: {str(e)}")
    except Exception as e:
        # Catch any other logic failures (File system, DB, etc.)
        _handle_failure(media_id, f"System Error: {str(e)}")

def _handle_failure(media_id, error_msg):
    """Marks the file as FAILED and records why."""
    print(f"❌ Engine Failure for {media_id}: {error_msg}")
    db.media_items.update_one(
        {"_id": media_id},
        {"$set": {
            "processing_status": "FAILED",
            "error_message": error_msg,
            "embedding_ended_at": datetime.utcnow()
        }}
    )