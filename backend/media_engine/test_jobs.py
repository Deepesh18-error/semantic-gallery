import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from unittest.mock import MagicMock

from django.test import TestCase, override_settings
from django.utils import timezone

from .catalog_store import SQLiteCatalog, SQLiteJobScheduler
from .catalog_worker import CatalogWorker
from .local_retrieval import SQLiteFullText
from .models import CatalogFile, CatalogJob, CatalogVectorRecord, ProviderCall, ProviderQuota
from .provider_quota import QuotaError, call_with_quota, reserve


class DurablePipelineTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(MEDIA_VAULT=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.root = Path(self.temp.name)
        self.store = SQLiteCatalog()
        self.store.put_source(source_id="source", user_id="alice", kind="linked_folder", root_path=self.root)
        self.path = self.root / "notes.txt"
        self.path.write_text("burger recipe with fries", encoding="utf-8")
        self.file = self.store.put_file(file_id="file", user_id="alice", source_id="source",
            path=self.path, source_type="TEXT", mime_type="text/plain", fingerprint=self._digest())
        self.job_id = SQLiteJobScheduler().enqueue(self.file.current_version_id, "discover")

    def _digest(self):
        import hashlib
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_stages_resume_and_publish_once(self):
        worker = CatalogWorker()
        with patch("media_engine.desktop_embedding.embed_segment", return_value=[1] + [0] * 3071) as embed:
            for stage in ("validate", "extract", "keyword-index", "embed", "publish", "publish"):
                self.assertTrue(worker.run_one(), f"Expected {stage}: {CatalogJob.objects.get(pk=self.job_id).__dict__}")
                self.assertEqual(CatalogJob.objects.get(pk=self.job_id).stage, stage)
                if stage == "embed":
                    self.assertEqual(len(SQLiteFullText().search("burger", user_id="alice")), 1)
        self.assertEqual(CatalogJob.objects.get(pk=self.job_id).state, "done")
        self.assertEqual(CatalogFile.objects.get(pk="file").processing_status, "EMBEDDED")
        self.assertEqual(CatalogVectorRecord.objects.count(), 1)
        self.assertEqual(embed.call_count, 1)
        self.assertFalse(worker.run_one())

    def test_expired_lease_is_reclaimed(self):
        CatalogJob.objects.filter(pk=self.job_id).update(state="running", attempts=1,
            lease_owner="dead", lease_until=timezone.now() - timedelta(seconds=5))
        self.assertTrue(CatalogWorker().run_one())
        job = CatalogJob.objects.get(pk=self.job_id)
        self.assertEqual(job.stage, "validate")
        self.assertEqual(job.state, "queued")

    def test_changed_file_cancels_old_version_and_queues_new(self):
        self.path.write_text("waterfall notes", encoding="utf-8")
        self.assertTrue(CatalogWorker().run_one())
        self.assertEqual(CatalogJob.objects.get(pk=self.job_id).error_category, "file_changed")
        file = CatalogFile.objects.get(pk="file")
        self.assertEqual(file.current_version.generation, 2)
        self.assertTrue(CatalogJob.objects.filter(file_version=file.current_version, stage="discover", state="queued").exists())

    def test_pause_resume_cancel_and_retry(self):
        scheduler = SQLiteJobScheduler()
        self.assertIsNone(scheduler.control(self.job_id, "bob", "pause"))
        scheduler.control(self.job_id, "alice", "pause")
        self.assertFalse(CatalogWorker().run_one())
        scheduler.control(self.job_id, "alice", "resume")
        self.assertTrue(CatalogWorker().run_one())
        scheduler.control(self.job_id, "alice", "cancel")
        self.assertFalse(CatalogWorker().run_one())
        scheduler.control(self.job_id, "alice", "retry")
        self.assertEqual(CatalogJob.objects.get(pk=self.job_id).state, "queued")

    def test_permanent_unsupported_does_not_repeat(self):
        CatalogFile.objects.filter(pk="file").update(original_name="notes.xyz")
        worker = CatalogWorker()
        self.assertTrue(worker.run_one())
        self.assertTrue(worker.run_one())
        self.assertEqual(CatalogJob.objects.get(pk=self.job_id).state, "unsupported")
        self.assertFalse(worker.run_one())

    def test_network_backoff_stops_after_max_attempts(self):
        worker = CatalogWorker()
        for _ in range(4):
            self.assertTrue(worker.run_one())
        self.assertEqual(CatalogJob.objects.get(pk=self.job_id).stage, "embed")
        with patch("media_engine.desktop_embedding.embed_segment", side_effect=ConnectionError("connection reset")) as embed:
            for attempt in range(5):
                self.assertTrue(worker.run_one())
                job = CatalogJob.objects.get(pk=self.job_id)
                if attempt < 4:
                    self.assertEqual(job.state, "queued")
                    CatalogJob.objects.filter(pk=job.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
            self.assertEqual(embed.call_count, 5)
        self.assertEqual(CatalogJob.objects.get(pk=self.job_id).state, "failed")
        self.assertFalse(worker.run_one())

    def test_daily_quota_pauses_without_repeated_calls(self):
        worker = CatalogWorker()
        for _ in range(4):
            worker.run_one()
        with patch("media_engine.desktop_embedding.embed_segment", side_effect=QuotaError("daily_budget", 3600)) as embed:
            worker.run_one()
            self.assertFalse(worker.run_one())
            self.assertEqual(embed.call_count, 1)
        self.assertEqual(CatalogJob.objects.get(pk=self.job_id).state, "paused")
        self.assertEqual(CatalogFile.objects.get(pk="file").processing_status, "PAUSED")


class QuotaTests(TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GEMINI_PROJECT_ID": "project"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.quota = ProviderQuota.objects.create(project_id="project", model="embed", rpm=2,
            input_tpm=100, rpd=3, input_usd_per_million=1)

    def test_shared_reservations_and_search_headroom(self):
        reserve("embed", "embedding", 10)
        with self.assertRaises(QuotaError) as blocked:
            reserve("embed", "embedding", 10)
        self.assertEqual(blocked.exception.category, "rate_limit")
        reserve("embed", "query_embedding", 10, priority="interactive")
        self.assertEqual(ProviderCall.objects.count(), 2)

    def test_usage_and_daily_budget(self):
        class Usage:
            prompt_token_count = 12
            candidates_token_count = 3
        class Response:
            usage_metadata = Usage()
        call_with_quota("embed", "query_embedding", 10, lambda: Response(),
                        user_id="alice", priority="interactive")
        self.assertEqual(ProviderCall.objects.get().actual_input_tokens, 12)
        self.assertEqual(ProviderCall.objects.get().state, "completed")
        self.assertGreater(ProviderCall.objects.get().estimated_spend_usd, 0)

    def test_unconfigured_quota_blocks_before_network(self):
        with self.assertRaises(QuotaError) as error:
            reserve("unknown", "embedding", 10)
        self.assertEqual(error.exception.category, "quota_unconfigured")

    def test_daily_request_limit_pauses_provider_work(self):
        self.quota.rpm = 10
        self.quota.rpd = 2
        self.quota.save(update_fields=["rpm", "rpd"])
        reserve("embed", "embedding", 10, priority="interactive")
        reserve("embed", "embedding", 10, priority="interactive")
        with self.assertRaises(QuotaError) as error:
            reserve("embed", "embedding", 10, priority="interactive")
        self.assertEqual(error.exception.category, "daily_budget")
        self.assertGreater(error.exception.retry_seconds, 0)

    def test_provider_429_blocks_new_reservations(self):
        with self.assertRaises(RuntimeError):
            call_with_quota("embed", "embedding", 10,
                lambda: (_ for _ in ()).throw(RuntimeError("429 RESOURCE_EXHAUSTED retry in 2s")))
        with self.assertRaises(QuotaError) as error:
            reserve("embed", "embedding", 10, priority="interactive")
        self.assertEqual(error.exception.category, "rate_limit")


class EmbeddingRequestTests(TestCase):
    def test_stable_query_uses_prefix_without_legacy_task_type(self):
        from .desktop_embedding import DIMENSIONS, embed_query
        response = MagicMock()
        response.embeddings = [MagicMock(values=[1] + [0] * (DIMENSIONS - 1))]
        client = MagicMock()
        client.models.embed_content.return_value = response
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}), \
             patch("media_engine.desktop_embedding.genai.Client", return_value=client), \
             patch("media_engine.desktop_embedding.call_with_quota",
                   side_effect=lambda model, operation, estimate, fn, **kwargs: fn()):
            self.assertEqual(embed_query("burger", "alice"), [1] + [0] * (DIMENSIONS - 1))
        kwargs = client.models.embed_content.call_args.kwargs
        self.assertEqual(kwargs["contents"], "task: search result | query: burger")
        self.assertIsNone(kwargs["config"].task_type)
