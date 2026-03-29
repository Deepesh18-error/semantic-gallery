import os
import time
import shutil
import uuid
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from django.conf import settings
from .database import db
from .utils import validate_file
from .embedding_tasks import start_background_embedding

# --- THE SENTRY (Watcher Logic) ---

class AutoIndexHandler(FileSystemEventHandler):
    """
    Listens for 'Create' events in the watched folder.
    """
    def __init__(self, user_id, collection_id):
        self.user_id = user_id
        self.collection_id = collection_id

    def on_created(self, event):
        # Ignore folders, we only want files
        if event.is_directory:
            return
        
        file_path = event.src_path
        print(f"👀 Watcher: New file detected -> {os.path.basename(file_path)}")
        
        # 1. THE DEBOUNCE (Wait 2 seconds)
        # Reason: Large files take time to copy. We wait so we don't read a 'half-written' file.
        time.sleep(2)
        
        self.process_new_file(file_path)

    def process_new_file(self, local_path):
        try:
            filename = os.path.basename(local_path)
            
            # 2. VALIDATE THE FILE
            # We use a 'Fake' class to mimic the Django request.FILES object for our utils
            class MockFile:
                def __init__(self, path):
                    self.name = os.path.basename(path)
                    self.size = os.path.getsize(path)
            
            mock_file = MockFile(local_path)
            is_valid, message, media_type = validate_file(mock_file)
            
            if not is_valid:
                print(f"❌ Watcher: {filename} rejected. Reason: {message}")
                return

            # 3. MOVE TO VAULT (Storage Logic)
            media_id = str(uuid.uuid4())
            ext = os.path.splitext(filename)[1].lower()
            stored_name = f"{media_id}{ext}"
            
            upload_dir = os.path.join(settings.MEDIA_VAULT, self.user_id, self.collection_id)
            os.makedirs(upload_dir, exist_ok=True)
            
            vault_path = os.path.join(upload_dir, stored_name)
            
            # We COPY instead of move so the user keeps their original file
            shutil.copy2(local_path, vault_path)

            # 4. RECORD IN MONGODB
            media_doc = {
                "_id": media_id,
                "user_id": self.user_id,
                "collection_id": self.collection_id,
                "media_type": media_type,
                "file_path": vault_path,
                "file_metadata": {
                    "original_name": filename,
                    "stored_name": stored_name,
                    "size_bytes": os.path.getsize(local_path)
                },
                "processing_status": "PENDING",
                "created_at": datetime.utcnow()
            }
            db.media_items.insert_one(media_doc)

            # 5. TRIGGER AI PIPELINE
            print(f"🚀 Watcher: Triggering AI for {filename}...")
            start_background_embedding([media_id])

        except Exception as e:
            print(f"❌ Watcher Error: {str(e)}")

# --- THE OBSERVER MANAGER ---
# We keep this global so we can stop/start it easily
current_observer = None

def start_watching(user_id, folder_path, collection_id):
    global current_observer
    
    # If an observer is already running, stop it first
    if current_observer:
        current_observer.stop()
        current_observer.join()

    if not os.path.exists(folder_path):
        print(f"⚠️ Watcher: Path {folder_path} does not exist. Cannot start.")
        return

    # Start a new observer
    event_handler = AutoIndexHandler(user_id, collection_id)
    current_observer = Observer()
    current_observer.schedule(event_handler, folder_path, recursive=False)
    current_observer.start()
    print(f"✅ Watcher: Now monitoring {folder_path}")