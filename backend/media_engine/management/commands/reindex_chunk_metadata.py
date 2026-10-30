"""
Phase E/Phase B coverage report.

Usage from backend/:
  python manage.py reindex_chunk_metadata
  python manage.py reindex_chunk_metadata --reset-pending

This command does not re-embed media or rewrite existing Pinecone vectors.
It reports coverage and can requeue missing media descriptions.
"""

from django.core.management.base import BaseCommand

from media_engine.database import db


class Command(BaseCommand):
    help = (
        "Reports Phase E metadata and Phase B description coverage. "
        "Optionally requeues missing or failed descriptions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-pending",
            action="store_true",
            help=(
                "Reset embedded images, videos, and audio with missing or failed "
                "descriptions back to description_status=PENDING."
            ),
        )

    def handle(self, *args, **options):
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("Phase E / Phase B Coverage Report")
        self.stdout.write("=" * 60)

        total_embedded = db.media_items.count_documents({"processing_status": "EMBEDDED"})
        total_pending = db.media_items.count_documents({"processing_status": "PENDING"})
        total_processing = db.media_items.count_documents({"processing_status": "PROCESSING"})
        total_failed = db.media_items.count_documents({"processing_status": "FAILED"})

        self.stdout.write("")
        self.stdout.write("Overall media status:")
        self.stdout.write(f"  EMBEDDED:   {total_embedded}")
        self.stdout.write(f"  PENDING:    {total_pending}")
        self.stdout.write(f"  PROCESSING: {total_processing}")
        self.stdout.write(f"  FAILED:     {total_failed}")

        self.stdout.write("")
        self.stdout.write("Breakdown by media type:")
        self.stdout.write(f"  {'Type':<12} {'Embedded':<12} {'Descriptions':<16} {'Missing Desc'}")
        self.stdout.write(f"  {'-' * 52}")

        for media_type in ["IMAGE", "VIDEO", "AUDIO", "DOCUMENT", "TEXT"]:
            count = db.media_items.count_documents(
                {"processing_status": "EMBEDDED", "media_type": media_type}
            )
            if count == 0:
                continue

            if media_type in ["IMAGE", "VIDEO", "AUDIO"]:
                desc_done = db.media_items.count_documents(
                    {
                        "processing_status": "EMBEDDED",
                        "media_type": media_type,
                        "description_status": "DONE",
                    }
                )
                desc_missing = count - desc_done
                self.stdout.write(
                    f"  {media_type:<12} {count:<12} {desc_done:<16} {desc_missing}"
                )
            else:
                self.stdout.write(f"  {media_type:<12} {count:<12} {'N/A':<16} {'N/A'}")

        desc_done_total = db.media_items.count_documents(
            {
                "processing_status": "EMBEDDED",
                "media_type": {"$in": ["IMAGE", "VIDEO", "AUDIO"]},
                "description_status": "DONE",
            }
        )
        desc_pending_total = db.media_items.count_documents(
            {
                "processing_status": "EMBEDDED",
                "media_type": {"$in": ["IMAGE", "VIDEO", "AUDIO"]},
                "description_status": {"$in": ["PENDING", "GENERATING", None]},
            }
        )
        desc_failed_total = db.media_items.count_documents(
            {
                "processing_status": "EMBEDDED",
                "media_type": {"$in": ["IMAGE", "VIDEO", "AUDIO"]},
                "description_status": "FAILED",
            }
        )

        self.stdout.write("")
        self.stdout.write("Description health for IMAGE, VIDEO, and AUDIO:")
        self.stdout.write(f"  DONE:    {desc_done_total}")
        self.stdout.write(f"  PENDING: {desc_pending_total}")
        self.stdout.write(f"  FAILED:  {desc_failed_total}")

        if options["reset_pending"]:
            result = db.media_items.update_many(
                {
                    "processing_status": "EMBEDDED",
                    "media_type": {"$in": ["IMAGE", "VIDEO", "AUDIO"]},
                    "description_status": {"$in": ["PENDING", "GENERATING", "FAILED", None]},
                },
                {"$set": {"description_status": "PENDING", "description_error": None}},
            )
            self.stdout.write("")
            self.stdout.write(
                f"Reset {result.modified_count} item(s) to description_status=PENDING."
            )
            self.stdout.write("Restart Django to let the description worker pick them up.")
        elif desc_pending_total or desc_failed_total:
            self.stdout.write("")
            self.stdout.write("Tip: requeue missing or failed descriptions with:")
            self.stdout.write("  python manage.py reindex_chunk_metadata --reset-pending")

        self.stdout.write("")
        self.stdout.write("Phase E metadata note:")
        self.stdout.write(
            "  New uploads get vector_type, chunk_index, total_chunks, and is_primary."
        )
        self.stdout.write(
            "  Existing Pinecone vectors cannot receive those fields without re-upserting."
        )
        self.stdout.write(
            "  Search safely treats missing is_primary as primary for old vectors."
        )
        self.stdout.write("")
        self.stdout.write("Report complete.")
