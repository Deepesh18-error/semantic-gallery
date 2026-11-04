import os
import threading
import logging
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from media_engine.models import CatalogFile, CatalogSource
from media_engine.source_sync import sync_source, _excluded
from media_engine.utils import SUPPORTED_TYPES


class Command(BaseCommand):
    help = "Run desktop folder reconciliation and filesystem hints in a dedicated process."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="One reconciliation pass; stable writes may need another pass")
        parser.add_argument("--no-watch", action="store_true", help="Use periodic scans only")

    def handle(self, *args, **options):
        if os.getenv("SEMANTIC_GALLERY_STORAGE", "legacy").lower() != "desktop":
            raise CommandError("Set SEMANTIC_GALLERY_STORAGE=desktop")
        wake = threading.Event()
        observer = None
        watches = {}
        if not options["no_watch"] and not options["once"]:
            try:
                from watchdog.events import FileSystemEventHandler
                from watchdog.observers import Observer

                class Hints(FileSystemEventHandler):
                    def __init__(self, source):
                        self.source_id = source.id
                        self.source = source

                    def on_any_event(self, event):
                        if event.event_type not in ("created", "modified", "moved", "deleted"):
                            return
                        path = Path(event.dest_path if event.event_type == "moved" else event.src_path)
                        if _excluded(path, Path(self.source.root_path), self.source):
                            return
                        if not event.is_directory and path.suffix.lower() not in {
                                extension for values in SUPPORTED_TYPES.values() for extension in values}:
                            return
                        close_old_connections()
                        try:
                            if not CatalogSource.objects.filter(pk=self.source_id, paused=False).exists():
                                return
                            CatalogSource.objects.filter(pk=self.source_id).update(next_scan_at=timezone.now())
                            if event.event_type == "deleted":
                                deleted = Path(event.src_path)
                                # Retain identities and versions; only availability changes.
                                rows = CatalogFile.objects.filter(source_id=self.source_id, available=True)
                                for row in rows.only("id", "path"):
                                    path = Path(row.path)
                                    if (path == deleted or (event.is_directory and path.is_relative_to(deleted))) and not path.exists():
                                        CatalogFile.objects.filter(pk=row.pk).update(available=False)
                            wake.set()
                        except Exception:
                            logging.getLogger(__name__).exception("Filesystem hint failed; periodic reconciliation will retry")
                        finally:
                            close_old_connections()

                observer = Observer()
                observer.start()
            except ImportError:
                self.stderr.write("watchdog is unavailable; periodic reconciliation remains active")
        startup = True
        try:
            while True:
                close_old_connections()
                sources = list(CatalogSource.objects.filter(kind="linked_folder", paused=False))
                active_ids = {source.id for source in sources}
                if observer:
                    for source_id in list(watches):
                        source = next((item for item in sources if item.id == source_id), None)
                        if source_id not in active_ids or not Path(source.root_path).is_dir():
                            observer.unschedule(watches.pop(source_id))
                for source in sources:
                    if observer and source.id not in watches and Path(source.root_path).is_dir():
                        try:
                            watches[source.id] = observer.schedule(Hints(source), source.root_path, recursive=True)
                        except OSError as error:
                            self.stderr.write(f"Filesystem hints unavailable: {error}")
                    try:
                        result = sync_source(source.id, force=startup or options["once"])
                        if options["once"]:
                            self.stdout.write(str({"source_id": source.id, **result}))
                    except Exception as error:
                        self.stderr.write(f"Source {source.id}: {error}")
                if options["once"]:
                    break
                startup = False
                wake.wait(1)
                wake.clear()
        except KeyboardInterrupt:
            self.stdout.write("Source synchronization stopped")
        finally:
            if observer:
                observer.stop()
                observer.join(timeout=5)
            close_old_connections()
