import os
import base64
import io
import fitz  # PyMuPDF
from docx import Document as DocxDocument
from PIL import Image
from .database import db
from .file_splitter import split_media, extract_thumbnail
import subprocess
from django.conf import settings

def generate_video_thumbnail(video_path, media_id):
    # Save to a dedicated thumbnails directory — NOT next to the video
    thumb_dir = os.path.join(settings.MEDIA_VAULT, "thumbnails")
    os.makedirs(thumb_dir, exist_ok=True)
    
    thumb_path = os.path.join(thumb_dir, f"{media_id}.jpg")

    # FIX: Use -ss 0 to grab the very first frame — avoids failures on short videos
    # FIX: Use forward slashes for FFmpeg even on Windows
    video_path_ffmpeg = video_path.replace('\\', '/')
    thumb_path_ffmpeg = thumb_path.replace('\\', '/')
    
    cmd = f'ffmpeg -y -ss 0 -i "{video_path_ffmpeg}" -vframes 1 -q:v 2 "{thumb_path_ffmpeg}"'

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)

        if result.returncode == 0 and os.path.exists(thumb_path):
            print(f"✅ Thumbnail Success: {thumb_path}")
            return thumb_path
        else:
            # Log the actual stderr so you can see WHY it failed
            print(f"❌ FFmpeg Failed (returncode={result.returncode})")
            print(f"❌ FFmpeg stderr: {result.stderr}")
            print(f"❌ FFmpeg stdout: {result.stdout}")
            return None
    except Exception as e:
        print(f"❌ Subprocess Crash: {str(e)}")
        return None
    
    
    
    
def process_media_item(media_id):
    item = db.media_items.find_one({"_id": media_id})
    if not item: return []

    m_type = item['media_type']
    path = item['file_path']

    if m_type == 'IMAGE':
        return _process_image(path)
    elif m_type == 'VIDEO':
        # Generate thumbnail FIRST
        thumb_path = generate_video_thumbnail(path, media_id)
        
        # Save to DB immediately so the UI can find it
        if thumb_path:
            db.media_items.update_one(
                {"_id": media_id},
                {"$set": {"file_metadata.thumbnail_path": thumb_path}}
            )
        print(f"🖼️ Preprocessor: Saved thumbnail path to DB for {media_id}")
        return _process_multimedia(path, media_id, is_video=True)
    
    elif m_type == 'AUDIO':
        return _process_multimedia(path, media_id, is_video=False)
    
    return []


def _process_image(path):
    with Image.open(path) as img:
        if img.mode != 'RGB': img = img.convert('RGB')
        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85)
        b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    
    return [{"type": "IMAGE", "data": b64, "metadata": {"chunk_index": 0}}]

def _process_document(path):
    text_content = ""
    ext = os.path.splitext(path)[1].lower()
    
    # 1. Extraction Logic
    if ext == '.pdf':
        doc = fitz.open(path)
        text_content = " ".join([page.get_text() for page in doc])
    elif ext == '.docx':
        doc = DocxDocument(path)
        text_content = " ".join([p.text for p in doc.paragraphs])
    else: # .txt
        with open(path, 'r', encoding='utf-8') as f:
            text_content = f.read()

    # 2. Sliding Window Chunking (500 tokens / 50 overlap)
    words = text_content.split()
    chunk_size = 500
    overlap = 50
    chunks = []
    
    for i in range(0, len(words), chunk_size - overlap):
        chunk_words = words[i:i + chunk_size]
        chunk_text = " ".join(chunk_words)
        chunks.append({
            "type": "TEXT",
            "data": chunk_text,
            "metadata": {
                "chunk_index": len(chunks),
                "chunk_text": chunk_text[:200] # For search preview
            }
        })
        if i + chunk_size >= len(words): break
        
    return chunks

def _process_multimedia(path, media_id, is_video):
    segments = split_media(path, media_id, is_video)
    packages = []
    
    for seg in segments:
        packages.append({
            "type": "VIDEO" if is_video else "AUDIO",
            "file_path": seg['path'], # Gemini reads from file path for A/V
            "metadata": {
                "segment_index": seg['index'],
                "start_time": seg['start_time'],
                "end_time": seg['end_time']
            }
        })
    return packages