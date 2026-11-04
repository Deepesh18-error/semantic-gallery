import tempfile
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .auth_service import create_token
from .catalog_worker import CatalogWorker, StaleFile
from .catalog_store import SQLiteJobScheduler, SQLiteCatalog
from .desktop_extraction import extract
from .local_retrieval import SQLiteFullText
from .models import CatalogFile, CatalogJob, CatalogSource, SourceObservation
from .source_sync import register_source, sync_source


class FolderSyncTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "library"
        self.root.mkdir()
        override = override_settings(MEDIA_VAULT=Path(self.temp.name) / "vault")
        override.enable()
        self.addCleanup(override.disable)
        self.source = register_source("alice", str(self.root))

    def scan_stable(self):
        sync_source(self.source.id, force=True)
        SourceObservation.objects.filter(source=self.source).update(stable_since=timezone.now() - timedelta(seconds=3))
        return sync_source(self.source.id, force=True)

    def publish(self):
        with patch("media_engine.desktop_embedding.embed_segment", return_value=[1] + [0] * 3071):
            worker = CatalogWorker()
            for _ in range(20):
                if not worker.run_one():
                    break

    def test_create_stable_restart_unchanged_rename_delete_reconnect(self):
        path = self.root / "notes.txt"
        path.write_text("AWS VPC private routes", encoding="utf-8")
        first = sync_source(self.source.id, force=True)
        self.assertEqual(first["unstable"], 1)
        self.assertEqual(CatalogFile.objects.count(), 0)
        # Persisted observation survives a new scanner invocation.
        self.assertEqual(self.scan_stable()["queued"], 1)
        file = CatalogFile.objects.get(source=self.source)
        file_id, version_id = file.id, file.current_version_id
        self.publish()
        self.assertEqual(self.scan_stable()["queued"], 0)
        renamed = self.root / "renamed.txt"
        path.rename(renamed)
        self.scan_stable()
        file.refresh_from_db()
        self.assertEqual(file.id, file_id)
        self.assertEqual(file.path, str(renamed))
        self.assertEqual(file.current_version_id, version_id)
        self.assertTrue(file.available)
        disconnected = self.root.with_name("disconnected")
        self.root.rename(disconnected)
        sync_source(self.source.id, force=True)
        file.refresh_from_db()
        self.assertFalse(file.available)
        disconnected.rename(self.root)
        self.scan_stable()
        file.refresh_from_db()
        self.assertTrue(file.available)
        renamed.unlink()
        sync_source(self.source.id, force=True)
        file.refresh_from_db()
        self.assertFalse(file.available)
        self.assertEqual(SQLiteFullText().search_evidence("AWS", user_id="alice"), [])

    def test_edit_keeps_published_evidence_until_atomic_publish(self):
        path = self.root / "notes.txt"
        path.write_text("AWS VPC", encoding="utf-8")
        self.scan_stable()
        self.publish()
        file = CatalogFile.objects.get(source=self.source)
        old_version = file.published_version_id
        path.write_text("waterfall hiking trail", encoding="utf-8")
        self.scan_stable()
        file.refresh_from_db()
        self.assertNotEqual(file.current_version_id, old_version)
        self.assertEqual(file.published_version_id, old_version)
        worker = CatalogWorker()
        with self.assertRaises(StaleFile):
            worker._ensure_current(CatalogJob.objects.get(file_version_id=old_version))
        for _ in range(4):
            self.assertTrue(worker.run_one())
        self.assertTrue(SQLiteFullText().search_evidence("AWS", user_id="alice"))
        self.assertEqual(SQLiteFullText().search_evidence("waterfall", user_id="alice"), [])
        self.publish()
        file.refresh_from_db()
        self.assertEqual(file.published_version_id, file.current_version_id)
        self.assertFalse(SQLiteFullText().search_evidence("AWS", user_id="alice"))
        self.assertTrue(SQLiteFullText().search_evidence("waterfall", user_id="alice"))

    def test_duplicate_copies_keep_paths_and_reuse_extraction(self):
        (self.root / "one").mkdir()
        (self.root / "two").mkdir()
        (self.root / "one" / "notes.txt").write_text("AWS VPC", encoding="utf-8")
        self.scan_stable()
        with patch("media_engine.catalog_worker.extract", wraps=extract) as extractor:
            self.publish()
            (self.root / "two" / "notes.txt").write_text("AWS VPC", encoding="utf-8")
            self.scan_stable()
            self.publish()
            self.assertEqual(extractor.call_count, 1)
        files = list(CatalogFile.objects.filter(source=self.source))
        self.assertEqual(len(files), 2)
        self.assertNotEqual(files[0].id, files[1].id)
        self.assertEqual(files[0].current_version.fingerprint, files[1].current_version.fingerprint)

    def test_exclusions_nested_roots_pause_and_scope(self):
        (self.root / "notes.txt").write_text("AWS", encoding="utf-8")
        (self.root / "partial.txt.tmp").write_text("unfinished", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "secret.txt").write_text("skip", encoding="utf-8")
        self.scan_stable()
        self.assertEqual(CatalogFile.objects.count(), 1)
        with self.assertRaises(ValueError):
            register_source("alice", str(self.root / ".git"))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + create_token("bob"))
        self.assertEqual(client.patch(f"/api/sources/{self.source.id}/", {"action": "pause"}, format="json").status_code, 404)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + create_token("alice"))
        self.assertEqual(client.patch(f"/api/sources/{self.source.id}/", {"action": "pause"}, format="json").status_code, 200)
        self.assertIsNone(SQLiteJobScheduler().claim("worker"))
        self.assertFalse(sync_source(self.source.id, force=True)["claimed"])
        client.patch(f"/api/sources/{self.source.id}/", {"action": "resume"}, format="json")
        self.assertIsNotNone(SQLiteJobScheduler().claim("worker"))

    def test_locked_file_and_changing_write_are_not_queued(self):
        path = self.root / "notes.txt"
        path.write_text("AWS", encoding="utf-8")
        sync_source(self.source.id, force=True)
        SourceObservation.objects.update(stable_since=timezone.now() - timedelta(seconds=3))
        with patch("media_engine.source_sync._hash", side_effect=PermissionError("locked")):
            result = sync_source(self.source.id, force=True)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(CatalogJob.objects.count(), 0)
        def changing_hash(file, renew):
            file.write_text("changed during scan", encoding="utf-8")
            return "discard"
        with patch("media_engine.source_sync._hash", side_effect=changing_hash):
            result = sync_source(self.source.id, force=True)
        self.assertEqual(result["unstable"], 1)
        self.assertEqual(CatalogJob.objects.count(), 0)

    def test_periodic_hash_catches_content_change_with_preserved_stat(self):
        path = self.root / "notes.txt"
        path.write_text("AWS VPC", encoding="utf-8")
        self.scan_stable()
        self.publish()
        original = path.stat()
        path.write_text("NEW VPC", encoding="utf-8")
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        SourceObservation.objects.update(stable_since=timezone.now() - timedelta(seconds=301))
        CatalogSource.objects.filter(pk=self.source.pk).update(next_scan_at=timezone.now())
        result = sync_source(self.source.id)
        self.assertEqual(result["queued"], 1)

    def test_manual_reindex_stages_a_new_version(self):
        (self.root / "notes.txt").write_text("AWS VPC", encoding="utf-8")
        self.scan_stable()
        self.publish()
        file = CatalogFile.objects.get(source=self.source)
        published = file.published_version_id
        SQLiteCatalog().queue_reindex(file.id, "alice")
        file.refresh_from_db()
        self.assertNotEqual(file.current_version_id, published)
        self.assertEqual(file.published_version_id, published)
        self.assertTrue(SQLiteFullText().search_evidence("AWS", user_id="alice"))

    def test_symlink_file_is_excluded(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            (self.root / "link.txt").symlink_to(outside)
        except OSError:
            self.skipTest("This Windows account cannot create symlinks")
        self.scan_stable()
        self.assertEqual(CatalogFile.objects.count(), 0)

    def test_pending_image_keeps_previous_display_thumbnail(self):
        from PIL import Image
        path = self.root / "picture.jpg"
        Image.new("RGB", (32, 32), "red").save(path)
        with patch("pytesseract.image_to_data", return_value={"text": [], "conf": []}):
            self.scan_stable()
            self.publish()
            file = CatalogFile.objects.get(source=self.source)
            old_thumbnail = file.thumbnail_path
            Image.new("RGB", (48, 48), "blue").save(path)
            self.scan_stable()
            worker = CatalogWorker()
            for _ in range(3):
                worker.run_one()
            file.refresh_from_db()
            self.assertEqual(file.thumbnail_path, old_thumbnail)
            self.assertNotEqual(file.current_version.thumbnail_path, old_thumbnail)
            self.publish()
            file.refresh_from_db()
            self.assertEqual(file.thumbnail_path, file.current_version.thumbnail_path)

    def test_exclusion_stops_queued_work_and_reinclude_resumes_it(self):
        (self.root / "notes.txt").write_text("AWS VPC", encoding="utf-8")
        self.scan_stable()
        CatalogSource.objects.filter(pk=self.source.pk).update(exclusions=["*.txt"])
        worker = CatalogWorker()
        worker.run_one()
        self.assertEqual(CatalogJob.objects.get().error_category, "source_excluded")
        self.assertFalse(CatalogFile.objects.get().available)
        CatalogSource.objects.filter(pk=self.source.pk).update(exclusions=[])
        self.scan_stable()
        self.assertEqual(CatalogJob.objects.get().state, "queued")
        self.assertTrue(CatalogFile.objects.get().available)

    def test_expired_source_lease_recovers_without_duplicate_job(self):
        (self.root / "notes.txt").write_text("AWS", encoding="utf-8")
        CatalogSource.objects.filter(pk=self.source.pk).update(lease_owner="dead",
            lease_until=timezone.now() + timedelta(seconds=60))
        self.assertFalse(sync_source(self.source.id, force=True)["claimed"])
        CatalogSource.objects.filter(pk=self.source.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        self.scan_stable()
        self.scan_stable()
        self.assertEqual(CatalogJob.objects.count(), 1)

    def test_worker_defers_changed_linked_file_to_stability_scan(self):
        path = self.root / "notes.txt"
        path.write_text("AWS VPC", encoding="utf-8")
        self.scan_stable()
        file = CatalogFile.objects.get()
        original_version = file.current_version_id
        path.write_text("writing a different document", encoding="utf-8")
        CatalogWorker().run_one()
        file.refresh_from_db()
        self.assertEqual(file.current_version_id, original_version)
        self.assertEqual(file.processing_status, "WAITING_FOR_FILE")
        self.assertEqual(CatalogJob.objects.get().state, "cancelled")
        sync_source(self.source.id, force=True)
        self.assertEqual(CatalogFile.objects.get().current_version_id, original_version)
        self.scan_stable()
        self.assertNotEqual(CatalogFile.objects.get().current_version_id, original_version)
