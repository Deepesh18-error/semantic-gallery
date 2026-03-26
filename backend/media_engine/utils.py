import os

# --- THE BOUNCER LOGIC ---
SUPPORTED_TYPES = {
    'IMAGE': ['.jpg', '.jpeg', '.png', '.webp'],
    'VIDEO': ['.mp4', '.mov', '.avi'],
    'AUDIO': ['.mp3', '.wav', '.m4a'],
    'DOCUMENT': ['.pdf', '.docx', '.txt']
}

SIZE_LIMITS = {
    'IMAGE': 50 * 1024 * 1024,   # 50MB
    'VIDEO': 500 * 1024 * 1024,  # 500MB
    'AUDIO': 200 * 1024 * 1024,  # 200MB
    'DOCUMENT': 100 * 1024 * 1024 # 100MB
}

def validate_file(file_obj):
    # 1. Get extension
    ext = os.path.splitext(file_obj.name)[1].lower()
    
    # 2. Determine Media Type
    media_type = None
    for m_type, extensions in SUPPORTED_TYPES.items():
        if ext in extensions:
            media_type = m_type
            break
            
    if not media_type:
        return False, "File type not supported", None

    # 3. Check Size
    if file_obj.size > SIZE_LIMITS[media_type]:
        return False, f"File too large. Limit for {media_type} is {SIZE_LIMITS[media_type]/(1024*1024)}MB", None

    return True, "Success", media_type