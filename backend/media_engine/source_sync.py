"""Restartable folder reconciliation. Filesystem events are hints; scans are authoritative."""
import fnmatch
import hashlib
import mimetypes
import os
import stat as stat_module
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .catalog_store import SQLiteCatalog, SQLiteJobScheduler
from .models import CatalogCollection, CatalogFile, CatalogJob, CatalogSource, SourceObservation
from .utils import validate_file

SKIP_NAMES = {".git", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".cache"}
TEMP_SUFFIXES = {".tmp", ".part", ".crdownload", ".download", ".swp", ".lock"}


def is_link(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def identity(info):
    return f"{info.st_dev}:{info.st_ino}" if info.st_ino else ""


def _make_available(file):
    updates = {"available": True}
    missing = CatalogJob.objects.filter(file_version_id=file.current_version_id).filter(
        Q(state="failed", error_category__in=["missing_file", "source_excluded"]) |
        Q(state="cancelled", error_category="file_changed"))
    if missing.exists():
        missing.update(state="queued", stage="discover", attempts=0, next_retry_at=None, error="", error_category="",
                       cancel_requested=False, pause_requested=False)
        updates.update(processing_status="PENDING", error_message="")
    elif file.published_version_id == file.current_version_id and file.published_version_id:
        updates.update(processing_status="PARTIAL" if file.published_version.extraction_coverage.get("failed_pages")
                       else "EMBEDDED", error_message="")
    CatalogFile.objects.filter(pk=file.id).update(**updates)


def register_source(user_id, root_path, **options):
    root = Path(root_path).expanduser()
    if not root.is_absolute() or not root.is_dir() or is_link(root):
        raise ValueError("Choose an existing absolute folder, not a symlink or junction")
    root = root.resolve()
    vault = Path(settings.MEDIA_VAULT).resolve()
    if root.is_relative_to(vault):
        raise ValueError("The application's managed storage cannot be linked as a folder")
    for existing in CatalogSource.objects.filter(user_id=user_id, kind="linked_folder"):
        other = Path(existing.root_path)
        if root.is_relative_to(other) or other.is_relative_to(root):
            raise ValueError("This folder overlaps an existing linked source")
    collection_id = options.get("collection_id")
    if collection_id and not CatalogCollection.objects.filter(id=collection_id, user_id=user_id).exists():
        raise ValueError("Collection not found or access denied")
    exclusions = options.get("exclusions", [])
    if not isinstance(exclusions, list) or len(exclusions) > 100 or not all(
            isinstance(value, str) and 0 < len(value) <= 255 for value in exclusions):
        raise ValueError("exclusions must be a list of at most 100 glob patterns")
    recursive = options.get("recursive", True)
    if not isinstance(recursive, bool):
        raise ValueError("recursive must be a boolean")
    return CatalogSource.objects.create(id=str(uuid4()), user_id=user_id, kind="linked_folder",
        root_path=str(root), collection_id=collection_id, exclusions=exclusions, recursive=recursive,
        next_scan_at=timezone.now())


def _excluded(path, root, source):
    relative = path.relative_to(root).as_posix()
    vault = Path(settings.MEDIA_VAULT).resolve()
    if path.is_relative_to(vault) or path.name.casefold() in SKIP_NAMES:
        return True
    if path.name.startswith(("~$", ".~")) or path.suffix.casefold() in TEMP_SUFFIXES:
        return True
    return any(fnmatch.fnmatchcase(relative.casefold(), pattern.casefold()) or
               fnmatch.fnmatchcase(path.name.casefold(), pattern.casefold())
               for pattern in source.exclusions)


def _hash(path, renew):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            renew()
    return digest.hexdigest()


def sync_source(source_id, *, force=False):
    now = timezone.now()
    owner = str(uuid4())
    ready = CatalogSource.objects.filter(id=source_id, kind="linked_folder", paused=False).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lte=now))
    if not force:
        ready = ready.filter(Q(next_scan_at__isnull=True) | Q(next_scan_at__lte=now))
    if not ready.update(lease_owner=owner, lease_until=now + timedelta(seconds=180)):
        return {"claimed": False}
    source = CatalogSource.objects.get(pk=source_id)
    root = Path(source.root_path)
    summary = {"queued": 0, "unchanged": 0, "moved": 0, "unstable": 0, "errors": 0}
    last_renewed = now

    def renew():
        nonlocal last_renewed
        current = timezone.now()
        if (current - last_renewed).total_seconds() >= 10:
            if not CatalogSource.objects.filter(pk=source.id, lease_owner=owner, paused=False).update(
                    lease_until=current + timedelta(seconds=180)):
                raise InterruptedError("Source paused or scan lease lost")
            last_renewed = current

    try:
        # A disconnected drive keeps its catalog identities but is never searched.
        if not root.is_dir():
            CatalogFile.objects.filter(source=source).update(available=False)
            CatalogSource.objects.filter(pk=source.id).update(state="disconnected", error="Folder is unavailable")
            return summary
        if is_link(root):
            raise ValueError("Source root became a symlink or junction")
        root = root.resolve()
        seen = set()
        inaccessible = []
        existing = {file.path: file for file in CatalogFile.objects.filter(source=source).select_related("current_version", "published_version")}
        existing_normalized = {os.path.normcase(path): file for path, file in existing.items()}
        observations = {item.path: item for item in SourceObservation.objects.filter(source=source)}
        seen_observations = set()

        def walk_error(error):
            inaccessible.append(Path(error.filename or root))
            summary["errors"] += 1

        for folder, directories, names in os.walk(root, followlinks=False, onerror=walk_error):
            renew()
            if CatalogSource.objects.filter(pk=source.id, paused=True).exists():
                raise InterruptedError("Source paused")
            parent = Path(folder)
            kept = []
            for name in directories:
                child = parent / name
                try:
                    if source.recursive and not _excluded(child, root, source) and not is_link(child):
                        kept.append(name)
                except OSError:
                    inaccessible.append(child)
                    summary["errors"] += 1
            directories[:] = kept
            for name in names:
                path = parent / name
                key = str(path)
                if _excluded(path, root, source):
                    continue
                try:
                    if is_link(path):
                        continue
                    before = path.stat()
                    if not stat_module.S_ISREG(before.st_mode):
                        continue
                    valid, _, source_type = validate_file(SimpleNamespace(name=name, size=before.st_size))
                    if not valid:
                        continue
                    seen.add(key)
                    seen_observations.add(key)
                    file = existing.get(key) or existing_normalized.get(os.path.normcase(key))
                    file_id = identity(before)
                    observation = observations.get(key)
                    if file and file.current_version and file.current_version.size_bytes == before.st_size and \
                            file.current_version.modified_ns == before.st_mtime_ns and file.file_identity == file_id and \
                            file.processing_status != "WAITING_FOR_FILE" and \
                            observation and (timezone.now() - observation.stable_since).total_seconds() < 300 and not force:
                        if not file.available:
                            _make_available(file)
                        if file.path != key or file.original_name != name:
                            CatalogFile.objects.filter(pk=file.id).update(path=key, original_name=name)
                            file.path, file.original_name = key, name
                        summary["unchanged"] += 1
                        continue
                    observation, created = SourceObservation.objects.get_or_create(source=source, path=key,
                        defaults={"size_bytes": before.st_size, "modified_ns": before.st_mtime_ns,
                                  "file_identity": file_id, "stable_since": timezone.now()})
                    if file and file.current_version and (before.st_size, before.st_mtime_ns) != (
                            file.current_version.size_bytes, file.current_version.modified_ns):
                        CatalogFile.objects.filter(pk=file.id).update(processing_status="WAITING_FOR_FILE")
                        file.processing_status = "WAITING_FOR_FILE"
                    if not created and (observation.size_bytes, observation.modified_ns, observation.file_identity) != \
                            (before.st_size, before.st_mtime_ns, file_id):
                        observation.size_bytes, observation.modified_ns = before.st_size, before.st_mtime_ns
                        observation.file_identity, observation.stable_since = file_id, timezone.now()
                        observation.save()
                    if (timezone.now() - observation.stable_since).total_seconds() < source.settle_seconds:
                        summary["unstable"] += 1
                        continue
                    fingerprint = _hash(path, renew)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns, file_id) != (after.st_size, after.st_mtime_ns, identity(after)):
                        observation.stable_since = timezone.now()
                        observation.save(update_fields=["stable_since"])
                        summary["unstable"] += 1
                        continue
                    # A move preserves identity only when the old path is gone.
                    if file is None:
                        moved = [candidate for candidate in existing.values() if (not file_id or candidate.file_identity == file_id)
                                 and candidate.source_type == source_type
                                 and candidate.current_version and candidate.current_version.fingerprint == fingerprint
                                 and not Path(candidate.path).exists()]
                        if len(moved) == 1:
                            file = moved[0]
                            CatalogFile.objects.filter(pk=file.id).update(path=key, original_name=name, available=True)
                            file.path, file.original_name = key, name
                            summary["moved"] += 1
                    with transaction.atomic():
                        if not CatalogSource.objects.filter(pk=source.id, paused=False, lease_owner=owner,
                                                             lease_until__gt=timezone.now()).exists():
                            raise InterruptedError("Source paused or scan lease lost")
                        if file and file.current_version and file.current_version.fingerprint == fingerprint:
                            if not file.available or file.processing_status == "WAITING_FOR_FILE":
                                _make_available(file)
                            CatalogFile.objects.filter(pk=file.id).update(path=key, original_name=name,
                                available=True, size_bytes=after.st_size, modified_ns=after.st_mtime_ns,
                                file_identity=file_id)
                            file.current_version.size_bytes = after.st_size
                            file.current_version.modified_ns = after.st_mtime_ns
                            file.current_version.save(update_fields=["size_bytes", "modified_ns"])
                            summary["unchanged"] += 1
                        else:
                            item = SQLiteCatalog().put_file(file_id=file.id if file else str(uuid4()),
                                user_id=source.user_id, source_id=source.id, path=path, source_type=source_type,
                                mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                                fingerprint=fingerprint, original_name=name, collection_id=source.collection_id)
                            CatalogFile.objects.filter(pk=item.id).update(file_identity=file_id)
                            # Obsolete jobs cannot publish the replacement generation.
                            CatalogJob.objects.filter(file_version__file=item).exclude(file_version=item.current_version).filter(
                                state__in=["queued", "paused"]).update(state="cancelled", cancel_requested=True)
                            SQLiteJobScheduler().enqueue(item.current_version_id, "discover")
                            summary["queued"] += 1
                        observation.stable_since = timezone.now()
                        observation.save(update_fields=["stable_since"])
                except (OSError, PermissionError):
                    seen.add(key)  # Locked/inaccessible is not a deletion.
                    summary["errors"] += 1
        # Inaccessible subtrees are unavailable, not removed from the catalog.
        if not CatalogSource.objects.filter(pk=source.id, paused=False, lease_owner=owner,
                                             lease_until__gt=timezone.now()).exists():
            raise InterruptedError("Source paused or scan lease lost")
        for path, file in existing.items():
            if file.path not in seen or any(Path(file.path).is_relative_to(prefix) for prefix in inaccessible):
                if not Path(file.path).is_file() or file.path not in seen:
                    CatalogFile.objects.filter(pk=file.id).update(available=False)
        SourceObservation.objects.filter(source=source).exclude(path__in=seen_observations).delete()
        CatalogSource.objects.filter(pk=source.id, lease_owner=owner).update(
            state="error" if summary["errors"] else "active", error="Some files could not be read" if summary["errors"] else "")
        return summary
    except InterruptedError:
        return {**summary, "paused": True}
    except (OSError, ValueError) as error:
        CatalogSource.objects.filter(pk=source.id).update(state="error", error=str(error)[:1000])
        CatalogFile.objects.filter(source=source).update(available=False)
        return {**summary, "errors": summary["errors"] + 1}
    finally:
        CatalogSource.objects.filter(pk=source.id, lease_owner=owner).update(lease_owner="", lease_until=None,
            last_scan_at=timezone.now(), next_scan_at=timezone.now() + timedelta(seconds=source.scan_interval_seconds))
