import os
import json
import tempfile
from array import array
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .catalog_store import SQLiteCatalog, SQLiteJobScheduler
from .local_retrieval import SQLiteExactVectors, SQLiteFullText
from .models import CatalogAccount, CatalogFile, CatalogJob, CatalogVectorRecord


class CatalogStorageTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = SQLiteCatalog()
        self.store.put_source(source_id="source", user_id="alice", kind="linked_folder", root_path=self.root)
        self.store.put_collection(collection_id="collection", user_id="alice", name="Food")
        self.path = self.root / "burger.txt"
        self.path.write_text("burger photo", encoding="utf-8")
        self.file = self.store.put_file(file_id="file", user_id="alice", source_id="source",
            path=self.path, source_type="TEXT", mime_type="text/plain", fingerprint="sha1",
            collection_id="collection")

    def test_version_and_path_scope(self):
        self.assertEqual(self.file.current_version.generation, 1)
        same = self.store.put_file(file_id="file", user_id="alice", source_id="source",
            path=self.path, source_type="TEXT", mime_type="text/plain", fingerprint="sha1")
        self.assertEqual(same.current_version.generation, 1)
        changed = self.store.put_file(file_id="file", user_id="alice", source_id="source",
            path=self.path, source_type="TEXT", mime_type="text/plain", fingerprint="sha2")
        self.assertEqual(changed.current_version.generation, 2)
        self.assertIsNone(self.store.get_file("file", "bob"))
        outside = self.root.parent / "outside.txt"
        with self.assertRaises(ValueError):
            self.store.put_file(file_id="outside", user_id="alice", source_id="source",
                path=outside, source_type="TEXT", mime_type="text/plain")

    def test_full_text_vector_and_delete(self):
        segment = self.store.put_segment(file_version_id=self.file.current_version_id,
            ordinal=0, representation="text", text="burger with fries")
        self.assertEqual(SQLiteFullText().search("burger", user_id="alice")[0]["segment_id"], segment.id)
        self.assertEqual(SQLiteFullText().search("burger", user_id="bob"), [])
        vectors = SQLiteExactVectors()
        vectors.upsert(vector_id="v1", segment_id=segment.id, values=[1, 0, 0], model_config="test")
        self.assertEqual(vectors.query([1, 0, 0], user_id="alice", model_config="test")[0]["file_id"], "file")
        self.assertEqual(vectors.query([1, 0, 0], user_id="bob", model_config="test"), [])
        self.assertEqual(vectors.query([1, 0, 0], user_id="alice", model_config="test", source_type="IMAGE"), [])
        self.assertEqual(vectors.delete("v1"), 1)
        self.assertEqual(vectors.query([1, 0, 0], user_id="alice", model_config="test"), [])

    def test_job_lease_and_retry(self):
        scheduler = SQLiteJobScheduler()
        job_id = scheduler.enqueue(self.file.current_version_id, "extract")
        claimed = scheduler.claim("worker-a")
        self.assertEqual(claimed.id, job_id)
        scheduler.set_progress(job_id, "worker-a", 1, 3)
        scheduler.renew(job_id, "worker-a")
        self.assertEqual(CatalogJob.objects.get(pk=job_id).progress_done, 1)
        self.assertIsNone(scheduler.claim("worker-b"))
        with self.assertRaises(ValueError):
            scheduler.finish(job_id, "worker-b")
        scheduler.finish(job_id, "worker-a", error="temporary", retry_seconds=0)
        self.assertEqual(CatalogJob.objects.get(pk=job_id).state, "failed")


class DesktopAPITests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(MEDIA_VAULT=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.client = APIClient()

    def test_register_collection_upload_and_restart_read(self):
        if os.getenv("SEMANTIC_GALLERY_STORAGE") != "desktop":
            self.skipTest("desktop URL mode only")
        response = self.client.post("/api/register/", {"email": "a@example.com", "password": "secret", "full_name": "A"})
        self.assertEqual(response.status_code, 201)
        response = self.client.post("/api/login/", {"email": "a@example.com", "password": "secret"})
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["token"])
        response = self.client.post("/api/collections/create/", {"name": "Food"})
        self.assertEqual(response.status_code, 201)
        collection_id = response.data["collection"]["_id"]
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile("burger.txt", b"burger and fries", content_type="text/plain")
        response = self.client.post("/api/media/upload/", {"collection_id": collection_id, "file": upload})
        self.assertEqual(response.status_code, 201)
        file_id = response.data["_id"]
        self.assertEqual(CatalogJob.objects.filter(file_version__file_id=file_id).count(), 1)
        self.assertEqual(self.client.get(f"/api/collections/{collection_id}/media/").data[0]["_id"], file_id)
        file_response = self.client.get(f"/api/media/file/{file_id}/")
        self.assertEqual(file_response.status_code, 200)
        file_response.close()
        with patch("media_engine.gemini_service.get_query_embedding", side_effect=RuntimeError("offline")):
            response = self.client.post("/api/search/", {"query": "burger"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])

    def test_stable_query_does_not_mix_preview_pinecone_vectors(self):
        if os.getenv("SEMANTIC_GALLERY_STORAGE") != "desktop":
            self.skipTest("desktop URL mode only")
        from .models import CatalogSegment
        from .auth_service import create_token
        from .catalog_store import SQLiteCatalog
        root = Path(self.temp.name)
        path = root / "old.jpg"
        path.write_bytes(b"old")
        store = SQLiteCatalog()
        store.put_source(source_id="source", user_id="alice", kind="linked_folder", root_path=root)
        file = store.put_file(file_id="old", user_id="alice", source_id="source", path=path,
                              source_type="IMAGE", mime_type="image/jpeg", fingerprint="oldhash")
        segment = CatalogSegment.objects.create(file_version=file.current_version, ordinal=0,
                                                representation="legacy_vector_reference")
        CatalogVectorRecord.objects.create(id="old-vector", segment=segment, backend="pinecone",
                                           model_config="gemini-embedding-2-preview:3072:v1", dimensions=3072)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + create_token("alice"))
        with patch("media_engine.desktop_embedding.embed_query") as embed:
            response = self.client.post("/api/search/", {"query": "burger"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [])
        embed.assert_not_called()


class LegacyImportTests(TestCase):
    def test_preserves_ids_and_vector_references_without_embedding(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_VAULT=Path(folder)):
            path = Path(folder) / "file.pdf"
            path.write_bytes(b"%PDF-1.4 test")
            legacy = MagicMock()
            legacy.users.find.return_value = [{"_id": "alice", "email": "a@example.com",
                                               "full_name": "Alice", "password_hash": "hash"}]
            legacy.collections.find.return_value = [{"_id": "collection", "user_id": "alice", "name": "Docs"}]
            legacy.media_items.find.return_value = [{"_id": "file", "user_id": "alice",
                "collection_id": "collection", "media_type": "DOCUMENT", "file_path": str(path),
                "file_version": "fingerprint", "processing_status": "EMBEDDED",
                "file_metadata": {"original_name": "file.pdf", "mime_type": "application/pdf"},
                "vector_ids": ["v1", "v2"]}]
            with patch("media_engine.management.commands.import_legacy_catalog.db", legacy):
                call_command("import_legacy_catalog")
                call_command("import_legacy_catalog")
            self.assertTrue(CatalogAccount.objects.filter(pk="alice").exists())
            self.assertEqual(CatalogFile.objects.get(pk="file").current_version.generation, 1)
            self.assertEqual(CatalogVectorRecord.objects.filter(backend="pinecone").count(), 2)

    def test_private_json_import_without_mongo(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_VAULT=Path(folder)):
            path = Path(folder) / "photo.jpg"
            path.write_bytes(b"image")
            export = Path(folder) / "export.json"
            export.write_text(json.dumps({"users": [], "collections": [], "media_items": [
                {"_id": "photo", "user_id": "alice", "media_type": "IMAGE",
                 "file_path": str(path), "file_version": "fingerprint", "vector_ids": []}
            ]}), encoding="utf-8")
            call_command("import_legacy_catalog", "--input", str(export), "--dry-run")
            self.assertFalse(CatalogFile.objects.filter(pk="photo").exists())
            call_command("import_legacy_catalog", "--input", str(export))
            self.assertTrue(CatalogFile.objects.filter(pk="photo").exists())
