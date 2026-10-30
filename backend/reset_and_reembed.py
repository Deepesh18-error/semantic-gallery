import os
import sys

# This must happen BEFORE any django/app imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['DJANGO_SETTINGS_MODULE'] = 'core.settings'

import django
django.setup()

# Now app imports are safe
from media_engine.database import db
from media_engine.pinecone_service import get_pinecone_index

def reset_all():
    index = get_pinecone_index()

    items = list(db.media_items.find(
        {"processing_status": {"$in": ["EMBEDDED", "PARTIALLY_EMBEDDED", "FAILED", "PROCESSING"]}},
        {"_id": 1, "vector_ids": 1, "media_type": 1, "file_metadata": 1}
    ))

    print(f"Found {len(items)} items to reset.")

    for item in items:
        media_id = item["_id"]
        vector_ids = item.get("vector_ids", [])
        media_type = item.get("media_type", "")
        filename = item.get("file_metadata", {}).get("original_name", "unknown")

        if vector_ids:
            try:
                index.delete(ids=vector_ids)
                print(f"  🗑️  Deleted {len(vector_ids)} vectors for: {filename}")
            except Exception as e:
                print(f"  ⚠️  Pinecone delete failed for {filename}: {e}")

        update_fields = {
            "processing_status": "PENDING",
            "vector_ids": [],
            "total_vectors": 0,
            "error_message": None,
            "embedding_started_at": None,
            "embedding_ended_at": None,
        }

        if media_type in ["IMAGE", "VIDEO", "AUDIO"]:
            update_fields["description_status"] = "PENDING"
            update_fields["ai_description"] = None
            update_fields["description_generated_at"] = None

        db.media_items.update_one({"_id": media_id}, {"$set": update_fields})
        print(f"  ✅ Reset: {filename}")

    print(f"\n✅ Done. {len(items)} items reset to PENDING.")

if __name__ == "__main__":
    reset_all()