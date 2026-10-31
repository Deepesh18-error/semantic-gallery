import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from media_engine.catalog_worker import CatalogWorker


class Command(BaseCommand):
    help = "Run the durable SQLite indexing worker in its own process."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run at most one stage")
        parser.add_argument("--drain", action="store_true", help="Run ready stages until idle")
        parser.add_argument("--concurrency", type=int, default=2)
        parser.add_argument("--cpu", type=int, default=2)
        parser.add_argument("--disk", type=int, default=2)
        parser.add_argument("--network", type=int, default=1)

    def handle(self, *args, **options):
        if os.getenv("SEMANTIC_GALLERY_STORAGE", "legacy").lower() != "desktop":
            raise CommandError("Set SEMANTIC_GALLERY_STORAGE=desktop")
        if any(options[key] < 1 for key in ("concurrency", "cpu", "disk", "network")):
            raise CommandError("Concurrency limits must be positive")
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL")
        worker = CatalogWorker(cpu=options["cpu"], disk=options["disk"], network=options["network"])
        if options["once"]:
            worker.run_one()
        elif options["drain"]:
            while worker.run_one():
                pass
        else:
            self.stdout.write("Catalog worker started. Press Ctrl+C to stop.")
            try:
                worker.run_forever(concurrency=options["concurrency"])
            except KeyboardInterrupt:
                self.stdout.write("Catalog worker stopped.")
