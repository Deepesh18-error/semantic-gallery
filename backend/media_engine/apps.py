from django.apps import AppConfig
import threading

class MediaEngineConfig(AppConfig):
    name = 'media_engine'

    def ready(self):
        """
        This runs once when Django starts up.
        We will check if the user has a 'Watched Folder' in DB and start it.
        """
        # We run this in a thread to not block the server startup
        threading.Thread(target=self.start_watchdog_on_boot, daemon=True).start()

    def start_watchdog_on_boot(self):
        from .database import db
        from .folder_watcher import start_watching
        
        # 1. Find a user who has 'auto_index' settings enabled
        # Note: In Phase 5/6 we will make this work for multiple users. 
        # For now, we take the first setting found.
        settings = db.user_settings.find_one({"auto_index_enabled": True})
        
        if settings:
            start_watching(
                user_id=settings['user_id'],
                folder_path=settings['watched_folder_path'],
                collection_id=settings['target_collection_id']
            )