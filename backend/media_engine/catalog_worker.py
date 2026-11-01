"""Durable desktop indexing pipeline; run from a dedicated management command."""
import hashlib
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from .catalog_store import SQLiteCatalog, SQLiteJobScheduler
from .desktop_extraction import ExtractionError, extract
from .models import CatalogFile, CatalogFileVersion, CatalogJob, CatalogSegment, CatalogVectorRecord
from .provider_quota import QuotaError, classify_provider_error, retry_after
from .utils import validate_file

log = logging.getLogger(__name__)
STAGES = ("discover", "validate", "extract", "keyword-index", "embed", "publish")
NEXT = dict(zip(STAGES, STAGES[1:]))


class StaleFile(RuntimeError):
    pass


class StoppedJob(RuntimeError):
    pass


class ResourceLimits:
    def __init__(self, cpu=2, disk=2, network=1):
        self.cpu = threading.BoundedSemaphore(cpu)
        self.disk = threading.BoundedSemaphore(disk)
        self.network = threading.BoundedSemaphore(network)


class CatalogWorker:
    def __init__(self, *, cpu=2, disk=2, network=1, lease_seconds=180):
        self.scheduler = SQLiteJobScheduler()
        self.catalog = SQLiteCatalog()
        self.resources = ResourceLimits(cpu, disk, network)
        self.lease_seconds = lease_seconds
        self.owner = f"{os.getpid()}:{uuid4()}"

    def _fingerprint(self, path):
        digest = hashlib.sha256()
        with self.resources.disk, Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _ensure_current(self, job, *, hash_file=False):
        version = CatalogFileVersion.objects.select_related("file__source").get(pk=job.file_version_id)
        file = version.file
        if file.current_version_id != version.id:
            raise StaleFile("A newer file version exists")
        path = Path(file.path)
        if not path.resolve().is_relative_to(Path(file.source.root_path).resolve()):
            raise ExtractionError("unsupported_format", "Source path escaped its linked folder")
        if not path.is_file():
            file.available = False
            file.processing_status = "MISSING"
            file.save(update_fields=["available", "processing_status"])
            raise ExtractionError("missing_file", "Source file is missing")
        stat = path.stat()
        if hash_file or stat.st_size != version.size_bytes or stat.st_mtime_ns != version.modified_ns:
            fingerprint = self._fingerprint(path)
            if version.fingerprint and fingerprint != version.fingerprint:
                self.catalog.put_file(file_id=file.id, user_id=file.user_id,
                    source_id=file.source_id, path=file.path, source_type=file.source_type,
                    mime_type=file.mime_type, fingerprint=fingerprint,
                    original_name=file.original_name, processing_status="PENDING")
                file.refresh_from_db()
                self.scheduler.enqueue(file.current_version_id, "discover")
                raise StaleFile("File content changed; new version queued")
            version.fingerprint = fingerprint
            version.size_bytes = stat.st_size
            version.modified_ns = stat.st_mtime_ns
            version.save(update_fields=["fingerprint", "size_bytes", "modified_ns"])
            file.size_bytes = stat.st_size
            file.modified_ns = stat.st_mtime_ns
            file.available = True
            file.save(update_fields=["size_bytes", "modified_ns", "available"])
        return version, file

    def _run_stage(self, job):
        version, file = self._ensure_current(job, hash_file=job.stage == "discover")
        if job.stage == "enrich":
            from .desktop_caption import generate_caption
            with self.resources.network:
                caption = generate_caption(file)
            self._ensure_current(job)
            file.ai_description = caption
            file.description_status = "DONE" if caption else "SKIPPED"
            file.save(update_fields=["ai_description", "description_status"])
            return
        if job.stage == "discover":
            return
        if job.stage == "validate":
            candidate = type("LocalFile", (), {"name": file.original_name, "size": file.size_bytes})()
            valid, message, source_type = validate_file(candidate)
            if not valid or source_type != file.source_type:
                raise ExtractionError("unsupported_format", message if not valid else "Source type mismatch")
            return
        if job.stage == "extract":
            before = Path(file.path).stat()
            with self.resources.cpu, self.resources.disk:
                parts = extract(file)
            after = Path(file.path).stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                # Metadata can change without changing the bytes. _ensure_current
                # queues a new version only when the content fingerprint differs.
                self._ensure_current(job, hash_file=True)
            if not parts:
                raise ExtractionError("extraction_failed", "No searchable content was extracted")
            with transaction.atomic():
                coverage = getattr(parts, "coverage", {})
                if coverage:
                    version.extraction_coverage = coverage
                    version.save(update_fields=["extraction_coverage"])
                keys = set()
                for part in parts:
                    segment = self.catalog.put_segment(file_version_id=version.id, **part)
                    keys.add(segment.id)
                CatalogSegment.objects.filter(file_version=version).exclude(id__in=keys).delete()
            return
        if job.stage == "keyword-index":
            # FTS5 triggers update atomically whenever a text segment is stored.
            if not CatalogSegment.objects.filter(file_version=version).exists():
                raise ExtractionError("extraction_failed", "No segments to index")
            return
        if job.stage == "embed":
            from .desktop_embedding import MODEL_CONFIG, embed_segment
            from .local_retrieval import SQLiteExactVectors
            segments = list(CatalogSegment.objects.filter(file_version=version).order_by("id"))
            self.scheduler.set_progress(job.id, self.owner, 0, len(segments))
            for index, segment in enumerate(segments):
                if self.scheduler.stop_requested(job.id, self.owner):
                    return "stopped"
                self._ensure_current(job)
                vector_id = f"{file.id}:v{version.generation}:{segment.representation}:{segment.ordinal}"
                if not CatalogVectorRecord.objects.filter(pk=vector_id, segment=segment,
                                                           model_config=MODEL_CONFIG, backend="sqlite_exact").exists():
                    with self.resources.network:
                        values = embed_segment(segment, file.user_id)
                    self._ensure_current(job)
                    if self.scheduler.stop_requested(job.id, self.owner):
                        return "stopped"
                    SQLiteExactVectors().upsert(vector_id=vector_id, segment_id=segment.id,
                        values=values, model_config=MODEL_CONFIG)
                self.scheduler.set_progress(job.id, self.owner, index + 1, len(segments))
            return
        if job.stage == "publish":
            version, file = self._ensure_current(job, hash_file=True)
            from .desktop_embedding import MODEL_CONFIG
            segments = CatalogSegment.objects.filter(file_version=version)
            if not segments.exists() or any(not CatalogVectorRecord.objects.filter(
                    segment=segment, backend="sqlite_exact", model_config=MODEL_CONFIG).exists()
                    for segment in segments):
                raise ExtractionError("extraction_failed", "Not all segments have vectors")
            with transaction.atomic():
                if CatalogJob.objects.filter(pk=job.id).filter(
                        Q(cancel_requested=True) | Q(pause_requested=True)).exists():
                    raise StoppedJob("Job stopped before publication")
                file.refresh_from_db()
                if file.current_version_id != version.id:
                    raise StaleFile("File changed before publication")
                file.processing_status = "PARTIAL" if version.extraction_coverage.get("failed_pages") else "EMBEDDED"
                file.error_message = ""
                file.save(update_fields=["processing_status", "error_message"])
            return
        raise ValueError(f"Unknown stage: {job.stage}")

    def _heartbeat(self, job_id, stop):
        close_old_connections()
        try:
            while not stop.wait(self.lease_seconds / 3):
                for attempt in range(3):
                    try:
                        self.scheduler.renew(job_id, self.owner, self.lease_seconds)
                        break
                    except ValueError:
                        return  # The job finished, stopped, or was reclaimed.
                    except Exception as error:
                        log.warning("Lease renewal failed for %s: %s", job_id, error)
                        close_old_connections()
                        if attempt == 2 or stop.wait(0.5 * (attempt + 1)):
                            return
        finally:
            close_old_connections()

    def run_one(self):
        close_old_connections()
        self.scheduler.recover_exhausted()
        job = self.scheduler.claim(self.owner, self.lease_seconds)
        if job is None:
            return False
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(job.id, stop), daemon=True)
        heartbeat.start()
        try:
            if self.scheduler.stop_requested(job.id, self.owner):
                return True
            file = job.file_version.file
            if job.stage != "enrich":
                file.processing_status = "PROCESSING"
                file.save(update_fields=["processing_status"])
            outcome = self._run_stage(job)
            if outcome == "stopped" or self.scheduler.stop_requested(job.id, self.owner):
                return True
            if job.stage in ("publish", "enrich"):
                self.scheduler.finish(job.id, self.owner)
                if job.stage == "publish" and os.getenv("SEMANTIC_GALLERY_ENABLE_CAPTIONS") == "1" \
                        and file.source_type in ("IMAGE", "VIDEO", "AUDIO"):
                    self.scheduler.enqueue(job.file_version_id, "enrich", priority=90)
            else:
                self.scheduler.advance(job.id, self.owner, NEXT[job.stage])
        except StaleFile as error:
            CatalogJob.objects.filter(pk=job.id, lease_owner=self.owner).update(
                state="cancelled", error_category="file_changed", error=str(error),
                lease_owner="", lease_until=None)
        except StoppedJob:
            self.scheduler.stop_requested(job.id, self.owner)
        except Exception as error:
            if self.scheduler.stop_requested(job.id, self.owner):
                return True
            category = error.category if isinstance(error, (ExtractionError, QuotaError)) else classify_provider_error(error)
            delay = 0
            if category in ("network", "rate_limit", "provider_error"):
                delay = min(3600, max(getattr(error, "retry_seconds", 0),
                    retry_after(error) if category == "rate_limit" else 0, 2 ** min(job.attempts, 8)))
            self.scheduler.fail(job.id, self.owner, category=category, message=str(error), retry_seconds=delay)
            if job.stage != "enrich" and category in ("unsupported_format", "extraction_failed", "missing_file", "credentials", "daily_budget", "quota_unconfigured", "input_too_large", "provider_rejected"):
                file = job.file_version.file
                file.processing_status = "PAUSED" if category in ("credentials", "daily_budget", "quota_unconfigured") else "UNSUPPORTED" if category == "unsupported_format" else "FAILED"
                file.error_message = f"{category}: {error}"
                file.save(update_fields=["processing_status", "error_message"])
            log.warning("Job %s stage %s: %s: %s", job.id, job.stage, category, error)
        finally:
            stop.set()
            heartbeat.join(timeout=2)
            close_old_connections()
        return True

    def run_forever(self, *, concurrency=2, poll_seconds=1):
        self.reconcile_enrichment()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while True:
                futures = [pool.submit(self.run_one) for _ in range(concurrency)]
                worked = False
                for future in futures:
                    try:
                        worked = future.result() or worked
                    except Exception as error:
                        log.exception("Worker loop error: %s", error)
                if not worked:
                    time.sleep(poll_seconds)

    def reconcile_enrichment(self):
        if os.getenv("SEMANTIC_GALLERY_ENABLE_CAPTIONS") != "1":
            return 0
        count = 0
        files = CatalogFile.objects.filter(processing_status="EMBEDDED", description_status="PENDING",
                                           source_type__in=["IMAGE", "VIDEO", "AUDIO"])
        for file in files.iterator():
            if file.current_version_id and not CatalogJob.objects.filter(
                    file_version_id=file.current_version_id, stage="enrich").exists():
                self.scheduler.enqueue(file.current_version_id, "enrich", priority=90)
                count += 1
        return count
