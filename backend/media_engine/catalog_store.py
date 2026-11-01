"""SQLite catalog operations. All changes use Django's transaction and migrations."""
from pathlib import Path
from uuid import uuid4

from django.db import IntegrityError, transaction
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
                      "source_start": location.get("source_start"),
                      "source_end": location.get("source_end"),
                      "heading": location.get("heading", ""),
                      "title": location.get("title", ""),
                      "ocr_confidence": location.get("ocr_confidence"),
                      "start_seconds": location.get("start_seconds"),
                      "end_seconds": location.get("end_seconds")})
        return segment

    def set_setting(self, user_id, key, value):
        return CatalogSetting.objects.update_or_create(user_id=user_id, key=key, defaults={"value": value})[0]

    def record_usage(self, user_id, provider, operation, *, calls=1, input_units=0, output_units=0, estimated_spend_usd=0):
        row, _ = CatalogUsage.objects.get_or_create(user_id=user_id, provider=provider,
                                                    operation=operation, day=timezone.localdate())
        CatalogUsage.objects.filter(pk=row.pk).update(calls=F("calls") + calls,
            input_units=F("input_units") + input_units, output_units=F("output_units") + output_units,
            estimated_spend_usd=F("estimated_spend_usd") + estimated_spend_usd)


class SQLiteJobScheduler:
    def enqueue(self, file_version_id, stage, *, priority=50):
        existing = CatalogJob.objects.filter(file_version_id=file_version_id,
            state__in=["queued", "running", "paused"]).first()
        if existing:
            return existing.id
        try:
            return CatalogJob.objects.create(id=str(uuid4()), file_version_id=file_version_id,
                                             stage=stage, priority=priority).id
        except IntegrityError:
            return CatalogJob.objects.get(file_version_id=file_version_id,
                state__in=["queued", "running", "paused"]).id

    def claim(self, owner, lease_seconds=300):
        from datetime import timedelta
        now = timezone.now()
        candidates = CatalogJob.objects.filter(
            Q(state="queued", next_retry_at__isnull=True) |
            Q(state="queued", next_retry_at__lte=now) |
            Q(state="running", lease_until__lte=now),
            pause_requested=False, cancel_requested=False,
            attempts__lt=F("max_attempts")).order_by("priority", "created_at")[:16]
        lease_until = now + timedelta(seconds=lease_seconds)
        for candidate in candidates:
            with transaction.atomic():
                updated = CatalogJob.objects.filter(pk=candidate.pk).filter(
                    Q(state="queued", next_retry_at__isnull=True) |
                    Q(state="queued", next_retry_at__lte=now) |
                    Q(state="running", lease_until__lte=now),
                    pause_requested=False, cancel_requested=False,
                    attempts__lt=F("max_attempts")).update(
                        state="running", lease_owner=owner, lease_until=lease_until,
                        attempts=F("attempts") + 1)
            if updated:
                return CatalogJob.objects.get(pk=candidate.pk)
        return None

    def finish(self, job_id, owner, *, error="", retry_seconds=0):
        from datetime import timedelta
        updates = {"lease_owner": "", "lease_until": None, "error": error,
                   "state": "queued" if error and retry_seconds else "failed" if error else "done",
                   "next_retry_at": timezone.now() + timedelta(seconds=retry_seconds) if error and retry_seconds else None}
        if CatalogJob.objects.filter(pk=job_id, lease_owner=owner, state="running").update(**updates) != 1:
            raise ValueError("Job lease is not owned by caller")

    def advance(self, job_id, owner, next_stage):
        if CatalogJob.objects.filter(pk=job_id, lease_owner=owner, state="running",
                                     pause_requested=False, cancel_requested=False).update(
            stage=next_stage, state="queued", attempts=0, progress_done=0, progress_total=0,
            lease_owner="", lease_until=None, next_retry_at=None,
            error="", error_category="") != 1:
            raise ValueError("Job was stopped or lease was lost")

    def fail(self, job_id, owner, *, category, message, retry_seconds=0):
        from datetime import timedelta
        job = CatalogJob.objects.get(pk=job_id)
        retry = retry_seconds > 0 and job.attempts < job.max_attempts
        state = "queued" if retry else "paused" if category in ("daily_budget", "credentials", "quota_unconfigured") else "unsupported" if category == "unsupported_format" else "failed"
        next_retry = timezone.now() + timedelta(seconds=retry_seconds) if retry else None
        if CatalogJob.objects.filter(pk=job_id, lease_owner=owner, state="running").update(
            state=state, error_category=category, error=message[:2000],
            next_retry_at=next_retry, lease_owner="", lease_until=None) != 1:
            raise ValueError("Job lease is not owned by caller")

    def control(self, job_id, user_id, action):
        with transaction.atomic():
            job = CatalogJob.objects.select_related("file_version__file").filter(
                pk=job_id, file_version__file__user_id=user_id).first()
            if not job:
                return None
            if action == "pause" and job.state in ("queued", "running"):
                job.pause_requested = True
                if job.state == "queued":
                    job.state = "paused"
            elif action == "resume" and job.state == "paused":
                job.pause_requested = False
                job.cancel_requested = False
                job.state = "queued"
                job.next_retry_at = None
                job.attempts = 0
            elif action == "cancel" and job.state in ("queued", "running", "paused"):
                job.cancel_requested = True
                if job.state != "running":
                    job.state = "cancelled"
            elif action == "retry" and job.state in ("failed", "cancelled", "unsupported"):
                job.state = "queued"
                job.attempts = 0
                job.next_retry_at = None
                job.cancel_requested = False
                job.pause_requested = False
            else:
                raise ValueError("Action is not valid for this job state")
            job.save()
            return job

    def stop_requested(self, job_id, owner):
        job = CatalogJob.objects.get(pk=job_id)
        if job.lease_owner != owner or job.state != "running":
            return "lost"
        if job.cancel_requested:
            CatalogJob.objects.filter(pk=job_id, lease_owner=owner).update(
                state="cancelled", lease_owner="", lease_until=None)
            return "cancelled"
        if job.pause_requested:
            CatalogJob.objects.filter(pk=job_id, lease_owner=owner).update(
                state="paused", lease_owner="", lease_until=None)
            return "paused"
        return None

    def recover_exhausted(self):
        now = timezone.now()
        return CatalogJob.objects.filter(
            Q(state="running", lease_until__lte=now) | Q(state="queued"),
            attempts__gte=F("max_attempts")).update(
            state="failed", error_category="retry_exhausted",
            error="Maximum attempts reached", lease_owner="", lease_until=None)

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
