"""Controlled reindex scheduling for old-model files; never calls the provider."""
import os

from django.core.management.base import BaseCommand, CommandError

from media_engine.catalog_store import SQLiteCatalog
from media_engine.desktop_embedding import INDEX_GENERATION, MODEL_CONFIG
from media_engine.models import CatalogFile, CatalogJob, CatalogSegment, CatalogVectorRecord


class Command(BaseCommand):
    help = "Preview or queue a bounded batch of files missing current-model vectors."

    def add_arguments(self, parser):
        parser.add_argument("--enqueue", action="store_true")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--user-id")
        parser.add_argument("--source-type")

    def handle(self, *args, **options):
        if os.getenv("SEMANTIC_GALLERY_STORAGE", "legacy").lower() != "desktop":
            raise CommandError("Set SEMANTIC_GALLERY_STORAGE=desktop")
        if options["limit"] < 1:
            raise CommandError("limit must be positive")
        files = CatalogFile.objects.filter(available=True, current_version__isnull=False)
        if options["user_id"]:
            files = files.filter(user_id=options["user_id"])
        if options["source_type"]:
            files = files.filter(source_type=options["source_type"])
        count = 0
        queued = 0
        for file in files.order_by("id").iterator():
            segments = CatalogSegment.objects.filter(file_version_id=file.current_version_id).count()
            vectors = CatalogVectorRecord.objects.filter(segment__file_version_id=file.current_version_id,
                backend="sqlite_exact", model_config=MODEL_CONFIG,
                index_generation=INDEX_GENERATION).count()
            if segments and vectors >= segments:
                continue
            if CatalogJob.objects.filter(file_version_id=file.current_version_id,
                                         state__in=["queued", "running", "paused"]).exists():
                continue
            count += 1
            if options["enqueue"]:
                SQLiteCatalog().queue_reindex(file.id, file.user_id)
                queued += 1
            if count >= options["limit"]:
                break
        self.stdout.write(f"Model {MODEL_CONFIG}: eligible={count}, queued={queued}, limit={options['limit']}")
