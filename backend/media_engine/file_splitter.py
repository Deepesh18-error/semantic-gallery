import os
import subprocess
import uuid
from django.conf import settings

def get_video_duration(file_path):
    """Uses FFmpeg to get duration in seconds."""
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', file_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return float(result.stdout)

def extract_thumbnail(video_path, media_id):
    """Extracts a JPG thumbnail from the 1st second of video."""
    thumb_name = f"thumb_{media_id}.jpg"
    # We store thumbnails in the media_vault/thumbnails folder
    thumb_dir = os.path.join(settings.MEDIA_VAULT, "thumbnails")
    os.makedirs(thumb_dir, exist_ok=True)
    
    thumb_path = os.path.join(thumb_dir, thumb_name)
    
    cmd = [
        'ffmpeg', '-i', video_path, '-ss', '00:00:01.000', 
        '-vframes', '1', '-q:v', '2', thumb_path, '-y'
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return thumb_path

def split_media(file_path, media_id, is_video=True):
    """
    Splits media into 120s (video) or 80s (audio) segments with 10s overlap.
    Returns: List of dicts with {path, start, end}
    """
    duration = get_video_duration(file_path)
    limit = 120 if is_video else 80
    overlap = 10
    
    segments = []
    start = 0
    index = 0
    
    # Create a temp directory for slices
    temp_dir = os.path.join(settings.MEDIA_VAULT, "temp", media_id)
    os.makedirs(temp_dir, exist_ok=True)

    while start < duration:
        end = min(start + limit, duration)
        seg_name = f"seg_{index}_{media_id}.mp4" if is_video else f"seg_{index}_{media_id}.mp3"
        seg_path = os.path.join(temp_dir, seg_name)
        
        # FFmpeg command for precise slicing
        cmd = [
            'ffmpeg', '-i', file_path, '-ss', str(start), '-to', str(end),
            '-c', 'copy', seg_path, '-y'
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        segments.append({
            "path": seg_path,
            "start_time": start,
            "end_time": end,
            "index": index
        })
        
        if end >= duration: break
        start += (limit - overlap)
        index += 1

    return segments