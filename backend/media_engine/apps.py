from django.apps import AppConfig
import threading

class MediaEngineConfig(AppConfig):
    name = 'media_engine'

    def ready(self):
        """
        Runs once when Django starts.
        Starts the Watchdog AND the Intelligence Worker.
        """
        # 1. Start the Folder Watcher (Already there)
        threading.Thread(target=self.start_watchdog_on_boot, daemon=True).start()

        # 2. Start the Phase 6 Intelligence Worker
        # This thread listens to the queue 24/7
        from .description_service import description_worker, enqueue_pending_descriptions
        threading.Thread(target=description_worker, daemon=True).start()
        threading.Thread(target=enqueue_pending_descriptions, daemon=True).start()

    def start_watchdog_on_boot(self):
        # ... (Existing logic for Folder Watcher stays the same)
        from .database import db
        from .folder_watcher import start_watching
        
        settings = db.user_settings.find_one({"auto_index_enabled": True})
        if settings:
            start_watching(
                user_id=settings['user_id'],
                folder_path=settings['watched_folder_path'],
                collection_id=settings['target_collection_id']
            )
