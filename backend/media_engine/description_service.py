import queue
import time
import os
import re
import io
from datetime import datetime
from google import genai
from google.genai import types
from .database import db
from .gemini_service import get_description_embedding
from .pinecone_service import get_pinecone_index
from .provider_quota import call_with_quota
from PIL import Image



# STEP 1: THE SERVICE FOUNDATION


description_queue = queue.Queue()


def start_description_generation(media_id):
    """
    ENTRY POINT: Called by the embedding engine after EMBEDDED status is set.
    Adds media_id to the queue and returns instantly — zero blocking.
    """
    print(f"🧬 [DESC SERVICE] Media {media_id} added to Intelligence Queue.")
    description_queue.put(media_id)


def enqueue_pending_descriptions():
    """
    Re-queues embedded media whose description job was interrupted or never started.
    This makes the async intelligence layer resumable across Django restarts.
    """
    items = list(db.media_items.find(
        {
            "processing_status": "EMBEDDED",
            "media_type": {"$in": ["IMAGE", "VIDEO", "AUDIO"]},
            "description_status": {"$in": ["PENDING", "GENERATING", None]}
        },
        {"_id": 1}
    ))

    if not items:
        return

    print(f"[DESC SERVICE] Re-queuing {len(items)} pending intelligence jobs.")
    ids = [item["_id"] for item in items]
    db.media_items.update_many(
        {"_id": {"$in": ids}},
        {"$set": {"description_status": "PENDING"}}
    )
    for pending_media_id in ids:
        description_queue.put(pending_media_id)


def description_worker():
    """
    THE SINGLE WORKER: Runs in a dedicated daemon thread started in apps.py.
    Processes items one by one with a physical rate limit.
    Never runs more than one description call at a time — zero quota collision.
    """
    print("🤖 [DESC WORKER] Intelligence worker thread started and listening...")

    while True:
        try:
            # Blocks here until an item is available — zero CPU waste when idle
            media_id = description_queue.get()

            _process_intelligence_task(media_id)

            # Mark task as done in the queue object
            description_queue.task_done()

            # RATE LIMITER: 1.5s between calls — max 40 descriptions/minute
            # Keeps us well under free tier limits and isolated from embedding quota
            time.sleep(1.5)

        except Exception as e:
            print(f"❌ [DESC WORKER] Worker Loop Error: {e}")
            time.sleep(5)  # Brief pause before resuming if something crashes



# STEP 2: THE INTELLIGENCE PIPELINE


def _process_intelligence_task(media_id):
    """
    FULL PIEPLINE: Logic, Sanitization, Vectorization, and Storage.
    """
    # 1. Fetch item from MongoDB
    item = db.media_items.find_one({"_id": media_id})
    if not item:
        print(f"⚠️ [DESC SERVICE] Media {media_id} not found in DB. Skipping.")
        return

    filename = item['file_metadata'].get('original_name', 'Unknown')
    user_id = str(item['user_id'])
    col_id = str(item['collection_id'])

    print(f"\n🧠 [DESC SERVICE] Starting Intelligence Pipeline for: {filename}")

    # Mark status as GENERATING so UI can show the "Analyzing" badge
    db.media_items.update_one(
        {"_id": media_id},
        {"$set": {"description_status": "GENERATING"}}
    )

    try:
        # --- STAGE 1: GENERATE TEXT (Gemini Flash) ---
        print(f"  💬 Stage 1: Calling Flash for description...")
        raw_desc = _call_gemini_flash(item)
        if not raw_desc:
            raise ValueError("Flash returned no description.")

        # --- STAGE 2: SANITIZE (Remove AI noise) ---
        print(f"  🧹 Stage 2: Sanitizing AI response...")
        final_desc = _sanitize_ai_response(raw_desc)
        if not final_desc:
            raise ValueError("Sanitization produced empty text.")

        # --- STAGE 3: VECTORIZE (Gemini Embedding 2) ---
        # We package the clean text exactly like a PDF chunk for your engine
        print(f"  📐 Stage 3: Vectorizing intelligence for {filename}...")
        desc_vector = get_description_embedding(final_desc)

        if not desc_vector:
            raise ValueError("Embedding engine returned no vector.")

        # --- STAGE 4: PINECONE UPSERT ---
        print(f"  🌲 Stage 4: Upserting description vector to Pinecone...")
        
        # Consistent ID format for Part 3
        v_id = f"{media_id}_desc" 

        pinecone_metadata = {
            "user_id": user_id,
            "collection_id": col_id,
            "media_item_id": media_id,
            "file_type": item['media_type'],
            "source_type": item['media_type'],
            "representation_type": "DESCRIPTION",
            "index_config": "gemini-embedding-2-preview:3072:v1",
            "original_filename": filename,
            "vector_type": "description",       # 🔑 The "Flag"
            "description_preview": final_desc[:300], # 📝 The Preview Snippet
            "chunk_text": final_desc[:1000]      # Compatibility for snippets
        }

        index = get_pinecone_index()
        print(f"🔍 [DESC DEBUG] Storing collection_id in Pinecone: '{col_id}' | type: {type(col_id)}")
        index.upsert(vectors=[(v_id, desc_vector, pinecone_metadata)])
        print(f"  ✅ Intelligence vector pushed to Pinecone: {v_id}")

        # --- STAGE 5: MONGODB ATOMIC SYNC (Step 4 Implementation) ---
        # We perform a "Dual Write" update
        db.media_items.update_one(
            {"_id": media_id},
            {
                "$set": {
                    "ai_description": final_desc,
                    "description_status": "DONE",
                    "description_generated_at": datetime.utcnow()
                },
                "$addToSet": {"vector_ids": v_id}
            }
        )
        print(f"✅ [DESC SERVICE] Intelligence Lifecycle COMPLETE for: {filename}")
        
    except Exception as e:
        print(f"❌ [DESC SERVICE] Intelligence Failed for {filename}: {str(e)}")
        db.media_items.update_one(
            {"_id": media_id},
            {"$set": {
                "description_status": "FAILED",
                "description_error": str(e)
            }}
        )


# STEP 3 (UTILITY): MEDIA RESIZER — Prepare input for Gemini


def _prepare_media_for_ai(file_path):
    """
    Standardizes image/thumbnail to max 1024px before sending to Flash-Lite.

    Why 1024px: High enough for Gemini to read logos, jersey text, and fine
    details. Small enough to upload in milliseconds. Industry sweet spot.

    Returns: Raw bytes (JPEG) ready for types.Part.from_bytes()
    Returns: None if file cannot be processed (caller handles gracefully)
    """
    try:
        with Image.open(file_path) as img:
            # Convert to RGB — removes alpha channel (PNG transparency)
            # and ensures consistent color space for JPEG output
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # thumbnail() respects aspect ratio — never distorts the image
            # If already smaller than 1024, this is a no-op (zero cost)
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            # Quality 85: industry standard for AI vision tasks
            # Sharp enough for detail recognition, compressed enough for speed
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()

    except Exception as e:
        print(f"⚠️ [RESIZER] Could not process {file_path}: {e}")
        return None



# STEP 4 (UTILITY): AI RESPONSE SANITIZER — Clean before vectorizing


def _sanitize_ai_response(raw_text):
    """
    Strips Markdown artifacts from Flash-Lite output before vectorization.

    Why this matters: Gemini often returns **bold text**, ## headers, or
    "Description: ..." prefixes. These Markdown symbols pollute the vector
    because Embedding 2 treats * and # as tokens, not formatting. Pure natural
    language produces cleaner, more accurate semantic vectors.

    Returns: Clean plain-text string ready for Embedding 2
    """
    if not raw_text:
        return ""

    # Remove Markdown formatting characters: **, ##, __, *, `, >
    clean = re.sub(r'[*#_`>]', '', raw_text)

    # Remove common AI response prefixes that add no semantic value
    # e.g. "Description: ", "Summary: ", "Analysis: "
    clean = re.sub(
        r'^(Description|Summary|Analysis|Content|Overview|Result):\s*',
        '',
        clean,
        flags=re.IGNORECASE
    )

    # Collapse all whitespace (newlines, tabs, multiple spaces) into single spaces
    # Vectors represent concepts — single-space prose embeds more accurately
    # than multi-line structured text
    clean = re.sub(r'\s+', ' ', clean)

    return clean.strip()



# STEP 5: THE FLASH-LITE CALLER — Per-media-type description logic


def _call_gemini_flash(item):
    """
    Calls Gemini 2.5 Flash-Lite to generate a rich semantic description.
    Handles IMAGE, VIDEO, and AUDIO with tailored prompts.
    Documents and TEXT return None — they already have chunk vectors.

    Returns: Raw description string (unsanitized — sanitization in caller)
    Returns: None if media type is unsupported or API call fails
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    m_type = item['media_type']
    content_payload = []

    if m_type == 'IMAGE':
        path = item['file_path']

        # Use _prepare_media_for_ai — resizes, converts, returns bytes
        img_bytes = _prepare_media_for_ai(path)
        if not img_bytes:
            print(f"⚠️ [FLASH] Could not prepare image at {path}. Skipping.")
            return None

        # Detect mime type from actual file extension (not hardcoded jpeg)
        ext = os.path.splitext(path)[1].lower()
        mime_map = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.webp': 'image/webp'
        }
        # After _prepare_media_for_ai, output is always JPEG
        # but we keep original mime for cases where resizer might be bypassed
        mime_type = mime_map.get(ext, 'image/jpeg')

        image_prompt = """Describe this image for a semantic search engine. Be extremely specific.

Cover ALL of the following that apply:
- People: count, gender, approximate age, clothing colors and type, what they are doing, facial expressions
- Food: exact food item names, brands if visible, how it is being consumed
- Sports: sport name, team colors/jerseys, specific action (batting, bowling, dancing, running), stadium or venue if visible
- Objects: every significant object, its color, brand, and context
- Setting: indoor/outdoor, location type (restaurant, stadium, street, home, park)
- Text/Logos: any readable text, brand names, signs, jersey numbers, team names
- Overall activity and mood

Write in plain descriptive sentences. Do NOT say 'the image shows' or 'I can see'. Just describe directly. Maximum 200 words."""

        content_payload = [
            types.Part.from_bytes(data=img_bytes, mime_type=mime_type),
            image_prompt
        ]

    elif m_type == 'VIDEO':
        thumb_path = item.get('file_metadata', {}).get('thumbnail_path')

        if not thumb_path or not os.path.exists(thumb_path):
            # Graceful fallback — thumbnail missing, use filename context
            filename = item['file_metadata']['original_name']
            print(f"⚠️ [FLASH] No thumbnail for video {filename}. Using filename fallback.")
            content_payload = [
                f"""Generate a semantic search description for a video file named '{filename}'.
Based on the filename, predict: the likely content, activity, setting, and any people or objects involved.
Write 3-4 specific descriptive sentences. Do NOT mention that this is AI-generated."""
            ]
        else:
            # Use _prepare_media_for_ai on the thumbnail — same resize logic
            thumb_bytes = _prepare_media_for_ai(thumb_path)
            if not thumb_bytes:
                filename = item['file_metadata']['original_name']
                print(f"⚠️ [FLASH] Could not prepare thumbnail for {filename}. Using filename fallback.")
                content_payload = [
                    f"""Generate a semantic search description for a video file named '{filename}'.
Based on the filename, predict: the likely content, activity, setting, and any people or objects involved.
Write 3-4 specific descriptive sentences. Do NOT mention that this is AI-generated."""
                ]
            else:
                video_prompt = """This is a key frame extracted from a video file.

Describe the video content based on this frame. Cover:
- People visible: clothing, actions, expressions
- Setting and environment
- Activity or event taking place
- Any text, logos, or brand names visible
- What type of video this likely is (tutorial, sports clip, personal video, lecture, etc.)

Write in plain descriptive sentences. Do NOT say 'the image shows'. Maximum 200 words."""

                content_payload = [
                    types.Part.from_bytes(data=thumb_bytes, mime_type="image/jpeg"),
                    video_prompt
                ]

    elif m_type == 'AUDIO':
        filename = item['file_metadata']['original_name']
        audio_prompt = f"""Generate a semantic search description for an audio file named '{filename}'.

Based on the filename predict and describe:
- The likely content and topic
- The format (lecture, voice note, podcast, song, interview, meeting recording)
- The likely speaker context or academic/personal subject
- Keywords someone would use to search for this file

Write 3-4 specific descriptive sentences. Do NOT mention that this is AI-generated or that you are predicting."""

        content_payload = [audio_prompt]

    else:
        # Documents and TEXT already have chunk vectors — no description needed
        return None

    # API Call to Gemini 2.5 Flash-Lite
    try:
        response = call_with_quota("gemini-2.5-flash-lite", "legacy_caption",
            max(1, len(str(content_payload)) // 4),
            lambda: client.models.generate_content(
                model="gemini-2.5-flash-lite", contents=content_payload),
            estimated_output_tokens=300)
        return response.text.strip()

    except Exception as e:
        print(f"❌ [FLASH] API call failed: {e}")
        return None
