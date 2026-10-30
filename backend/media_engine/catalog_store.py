"""SQLite catalog operations. All changes use Django's transaction and migrations."""
from pathlib import Path
from uuid import uuid4

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import (CatalogCollection, CatalogFile, CatalogFileVersion, CatalogJob,
                     CatalogMembership, CatalogSegment, CatalogSetting, CatalogSource, CatalogUsage)


class SQLiteCatalog:
    def put_source(self, *, source_id, user_id, kind, root_path):
        if kind not in ("linked_folder", "managed_import"):
            raise ValueError("Unknown source kind")
        path = str(Path(root_path).resolve())
        existing = CatalogSource.objects.filter(pk=source_id).first()
        if existing and existing.user_id != user_id:
            raise ValueError("Source belongs to another user")
        source, _ = CatalogSource.objects.update_or_create(
            id=source_id, defaults={"user_id": user_id, "kind": kind, "root_path": path})
        return source

    def put_collection(self, *, collection_id, user_id, name, **metadata):
        existing = CatalogCollection.objects.filter(pk=collection_id).first()
        if existing and existing.user_id != user_id:
            raise ValueError("Collection belongs to another user")
        collection, _ = CatalogCollection.objects.update_or_create(
            id=collection_id, defaults={"user_id": user_id, "name": name,
                                    "description": metadata.get("description", ""),
                                    "theme_color": metadata.get("theme_color", "#3498db"),
                                    "icon_tag": metadata.get("icon_tag", "folder")})
        return collection

    @transaction.atomic
    def put_file(self, *, file_id, user_id, source_id, path, source_type, mime_type,
                 fingerprint="", collection_id=None, **metadata):
        source = CatalogSource.objects.get(id=source_id, user_id=user_id)
        existing = CatalogFile.objects.filter(pk=file_id).first()
        if existing and existing.user_id != user_id:
            raise ValueError("File belongs to another user")
        resolved = Path(path).resolve()
        root = Path(source.root_path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("File path is outside its source")
        stat = resolved.stat() if resolved.is_file() else None
        file, _ = CatalogFile.objects.update_or_create(
            id=file_id, defaults={"user_id": user_id, "source": source, "path": str(resolved),
                                  "original_name": metadata.get("original_name", resolved.name),
                                  "source_type": source_type, "mime_type": mime_type,
                                  "size_bytes": stat.st_size if stat else metadata.get("size_bytes", 0),
                                  "modified_ns": stat.st_mtime_ns if stat else None,
                                  "available": stat is not None,
                                  "processing_status": metadata.get("processing_status", "PENDING"),
                                  "error_message": metadata.get("error_message", ""),
                                  "thumbnail_path": metadata.get("thumbnail_path", ""),
                                  "ai_description": metadata.get("ai_description", ""),
                                  "description_status": metadata.get("description_status", "PENDING")})
        current = file.current_version
        if current is None or (fingerprint and current.fingerprint != fingerprint) or (
                not fingerprint and current.modified_ns != file.modified_ns):
            version = CatalogFileVersion.objects.create(
                file=file, generation=(current.generation + 1 if current else 1),
                fingerprint=fingerprint, size_bytes=file.size_bytes, modified_ns=file.modified_ns)
            file.current_version = version
            file.save(update_fields=["current_version"])
        if collection_id:
            collection = CatalogCollection.objects.get(id=collection_id, user_id=user_id)
            CatalogMembership.objects.get_or_create(file=file, collection=collection)
        return file

    def get_file(self, file_id, user_id):
        return CatalogFile.objects.filter(id=file_id, user_id=user_id).select_related("current_version").first()

    def list_files(self, user_id, collection_id=None):
        files = CatalogFile.objects.filter(user_id=user_id).select_related("current_version")
        if collection_id:
            files = files.filter(collections__id=collection_id, collections__user_id=user_id)
        return files.order_by("-created_at")

    def put_segment(self, *, file_version_id, ordinal, representation, text="", derivative_path="", **location):
        segment, _ = CatalogSegment.objects.update_or_create(
            file_version_id=file_version_id, ordinal=ordinal, representation=representation,
            defaults={"text": text, "derivative_path": derivative_path,
                      "page_number": location.get("page_number"),
                      "start_seconds": location.get("start_seconds"),
                      "end_seconds": location.get("end_seconds")})
        return segment

    def set_setting(self, user_id, key, value):
        return CatalogSetting.objects.update_or_create(user_id=user_id, key=key, defaults={"value": value})[0]

    def record_usage(self, user_id, provider, operation, *, calls=1, input_units=0, output_units=0):
        row, _ = CatalogUsage.objects.get_or_create(user_id=user_id, provider=provider,
                                                    operation=operation, day=timezone.localdate())
        CatalogUsage.objects.filter(pk=row.pk).update(calls=F("calls") + calls,
            input_units=F("input_units") + input_units, output_units=F("output_units") + output_units)


class SQLiteJobScheduler:
    def enqueue(self, file_version_id, stage):
        job = CatalogJob.objects.create(id=str(uuid4()), file_version_id=file_version_id, stage=stage)
        return job.id

    @transaction.atomic
    def claim(self, owner, lease_seconds=300):
        from datetime import timedelta
        now = timezone.now()
        candidate = CatalogJob.objects.filter(
            Q(state="queued", next_retry_at__isnull=True) |
            Q(state="queued", next_retry_at__lte=now) |
            Q(state="running", lease_until__lte=now)).order_by("created_at").first()
        if candidate is None:
            return None
        lease_until = now + timedelta(seconds=lease_seconds)
        updated = CatalogJob.objects.filter(pk=candidate.pk).filter(
            Q(state="queued") | Q(state="running", lease_until__lte=now)).update(
                state="running", lease_owner=owner, lease_until=lease_until, attempts=F("attempts") + 1)
        return CatalogJob.objects.get(pk=candidate.pk) if updated else None

    def finish(self, job_id, owner, *, error="", retry_seconds=0):
        from datetime import timedelta
        updates = {"lease_owner": "", "lease_until": None, "error": error,
                   "state": "queued" if error and retry_seconds else "failed" if error else "done",
                   "next_retry_at": timezone.now() + timedelta(seconds=retry_seconds) if error and retry_seconds else None}
        if CatalogJob.objects.filter(pk=job_id, lease_owner=owner, state="running").update(**updates) != 1:
            raise ValueError("Job lease is not owned by caller")

    def set_progress(self, job_id, owner, done, total):
        if done < 0 or total < done:
            raise ValueError("Invalid progress")
        if CatalogJob.objects.filter(pk=job_id, lease_owner=owner, state="running",
                                     lease_until__gt=timezone.now()).update(
                                         progress_done=done, progress_total=total) != 1:
            raise ValueError("Job lease is not owned by caller or has expired")

    def renew(self, job_id, owner, lease_seconds=300):
        from datetime import timedelta
        if CatalogJob.objects.filter(pk=job_id, lease_owner=owner, state="running",
                                     lease_until__gt=timezone.now()).update(
                                         lease_until=timezone.now() + timedelta(seconds=lease_seconds)) != 1:
            raise ValueError("Job lease is not owned by caller or has expired")
