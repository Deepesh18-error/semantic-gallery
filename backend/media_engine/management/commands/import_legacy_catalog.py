"""Copy Mongo metadata into SQLite without embedding or changing source files."""
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from media_engine.catalog_store import SQLiteCatalog, SQLiteJobScheduler
from media_engine.database import db
from media_engine.models import CatalogAccount, CatalogJob, CatalogVectorRecord


class Command(BaseCommand):
    help = "Import existing Mongo collections and media metadata into SQLite; preserves IDs and vector references."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--user-id", default=None)
        parser.add_argument("--input", help="Optional private JSON export containing users, collections, media_items")

    def handle(self, *args, **options):
        catalog = SQLiteCatalog()
        scheduler = SQLiteJobScheduler()
        if options["input"]:
            payload = json.loads(Path(options["input"]).read_text(encoding="utf-8"))
            collections = payload.get("collections", [])
            items = payload.get("media_items", [])
            users = payload.get("users", [])
            user_settings = payload.get("user_settings", [])
            if options["user_id"]:
                collections = [row for row in collections if str(row.get("user_id")) == options["user_id"]]
                items = [row for row in items if str(row.get("user_id")) == options["user_id"]]
                users = [row for row in users if str(row.get("_id")) == options["user_id"]]
                user_settings = [row for row in user_settings if str(row.get("user_id")) == options["user_id"]]
        else:
            if db is None:
                raise CommandError("MongoDB is disabled; provide --input or run with SEMANTIC_GALLERY_STORAGE=legacy")
            query = {"user_id": options["user_id"]} if options["user_id"] else {}
            try:
                collections = list(db.collections.find(query))
                items = list(db.media_items.find(query))
                users = list(db.users.find({"_id": options["user_id"]} if options["user_id"] else {}))
                user_settings = list(db.user_settings.find(query))
            except Exception as error:
                raise CommandError("Legacy MongoDB metadata is unavailable; retry when MongoDB is reachable or use --input") from error
        vault = Path(settings.MEDIA_VAULT).resolve()
        invalid = []
        for item in items:
            raw = item.get("file_path")
            if not raw:
                invalid.append(str(item.get("_id")))
                continue
            path = Path(raw).resolve()
            if not path.is_relative_to(vault):
                invalid.append(str(item.get("_id")))
        if invalid:
            raise CommandError(f"Refusing {len(invalid)} file paths outside the managed vault or missing paths: {invalid[:10]}")
        if options["dry_run"]:
            self.stdout.write(f"Validated {len(collections)} collections and {len(items)} files; no changes made.")
            return
        counts = {"users": 0, "collections": 0, "files": 0, "vector_references": 0,
                  "missing_files": 0, "settings": 0, "queued_jobs": 0}
        with transaction.atomic():
            for row in users:
                CatalogAccount.objects.update_or_create(id=str(row["_id"]), defaults={
                    "email": row["email"], "full_name": row.get("full_name", ""),
                    "password_hash": row["password_hash"]})
                counts["users"] += 1
            for row in user_settings:
                user_id = str(row["user_id"])
                if row.get("watched_folder_path"):
                    folder = Path(row["watched_folder_path"]).resolve()
                    catalog.put_source(source_id=str(uuid5(NAMESPACE_URL, f"linked:{user_id}:{folder}")),
                        user_id=user_id, kind="linked_folder", root_path=str(folder))
                for key, value in row.items():
                    if key not in ("_id", "user_id"):
                        catalog.set_setting(user_id, key, value)
                        counts["settings"] += 1
            for row in collections:
                catalog.put_collection(collection_id=str(row["_id"]), user_id=str(row["user_id"]),
                    name=row.get("name", "Untitled"), description=row.get("description", ""),
                    theme_color=row.get("theme_color", "#3498db"), icon_tag=row.get("icon_tag", "folder"))
                counts["collections"] += 1
            for row in items:
                user_id = str(row["user_id"])
                path = Path(row["file_path"]).resolve()
                source_id = str(uuid5(NAMESPACE_URL, f"managed:{user_id}:{vault}"))
                catalog.put_source(source_id=source_id, user_id=user_id, kind="managed_import", root_path=vault)
                collection_id = str(row.get("collection_id", "")) or None
                if collection_id and not any(str(c["_id"]) == collection_id and str(c["user_id"]) == user_id for c in collections):
                    collection_id = None
                metadata = row.get("file_metadata") or {}
                file = catalog.put_file(file_id=str(row["_id"]), user_id=user_id, source_id=source_id,
                    path=str(path), source_type=row.get("media_type", "TEXT"),
                    mime_type=metadata.get("mime_type", "application/octet-stream"),
                    fingerprint=row.get("file_version") or "", collection_id=collection_id,
                    original_name=metadata.get("original_name") or path.name,
                    size_bytes=metadata.get("size_bytes") or 0,
                    processing_status=row.get("processing_status", "PENDING"),
                    error_message=row.get("error_message") or "",
                    ai_description=row.get("ai_description") or "",
                    description_status=row.get("description_status") or "PENDING",
                    thumbnail_path=metadata.get("thumbnail_path") or "")
                counts["files"] += 1
                if not file.available:
                    counts["missing_files"] += 1
                if file.available and file.processing_status in ("PENDING", "PROCESSING"):
                    if not CatalogJob.objects.filter(file_version=file.current_version,
                                                     state__in=["queued", "running"]).exists():
                        scheduler.enqueue(file.current_version_id, "discover")
                        counts["queued_jobs"] += 1
                for ordinal, vector_id in enumerate(row.get("vector_ids") or []):
                    segment = catalog.put_segment(file_version_id=file.current_version_id, ordinal=ordinal,
                        representation="legacy_vector_reference")
                    _, created = CatalogVectorRecord.objects.get_or_create(id=str(vector_id), defaults={
                        "segment": segment, "backend": "pinecone", "model_config": "legacy:unknown",
                        "index_generation": 1, "dimensions": 0})
                    counts["vector_references"] += int(created)
        self.stdout.write(self.style.SUCCESS(str(counts)))
