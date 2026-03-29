import os
import base64
import io
import fitz  # PyMuPDF
from docx import Document as DocxDocument
from PIL import Image
from .database import db
from .file_splitter import split_media, extract_thumbnail

def process_media_item(media_id):
    item = db.media_items.find_one({"_id": media_id})
    if not item: return []

    m_type = item['media_type']
    path = item['file_path']

    if m_type == 'IMAGE':
        return _process_image(path)
    elif m_type == 'DOCUMENT':
        return _process_document(path)
    elif m_type == 'VIDEO':
        # Extract thumb first for the UI
        extract_thumbnail(path, media_id)
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