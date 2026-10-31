from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from media_engine.models import CatalogAccount, CatalogCollection, CatalogFile, CatalogJob, CatalogVectorRecord, ProviderQuota


class Command(BaseCommand):
    help = "Check SQLite catalog integrity and summarize durable records. No MongoDB is used."

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA quick_check")
            result = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM catalog_segment_fts")
            fts_segments = cursor.fetchone()[0]
        if result != "ok":
            raise CommandError(f"SQLite integrity check failed: {result}")
        files = CatalogFile.objects.all()
        summary = {"integrity": result, "accounts": CatalogAccount.objects.count(),
                   "collections": CatalogCollection.objects.count(), "files": files.count(),
                   "missing_paths": sum(not Path(file.path).is_file() for file in files.iterator()),
                   "vectors": CatalogVectorRecord.objects.count(), "jobs": CatalogJob.objects.count(),
                   "queued_jobs": CatalogJob.objects.filter(state="queued").count(),
                   "paused_jobs": CatalogJob.objects.filter(state="paused").count(),
                   "quotas": ProviderQuota.objects.count(), "fts_segments": fts_segments}
        self.stdout.write(str(summary))
