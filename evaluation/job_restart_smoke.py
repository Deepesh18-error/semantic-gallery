"""Cross-process worker restart and expired-lease smoke test; no provider calls."""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    backend = Path(__file__).resolve().parents[1] / "backend"
    with tempfile.TemporaryDirectory() as folder:
        env = os.environ.copy()
        env.update({"SEMANTIC_GALLERY_STORAGE": "desktop",
                    "SEMANTIC_GALLERY_CATALOG_PATH": str(Path(folder) / "catalog.sqlite3"),
                    "GEMINI_PROJECT_ID": "smoke-no-quota",
                    "PYTHONIOENCODING": "utf-8"})
        def command(*args):
            return subprocess.run([sys.executable, str(backend / "manage.py"), *args],
                                  env=env, check=True, capture_output=True, text=True)
        command("migrate", "--noinput")
        os.environ.update(env)
        sys.path.insert(0, str(backend))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
        import django
        django.setup()
        from django.db import connections
        from django.utils import timezone
        from datetime import timedelta
        from media_engine.catalog_store import SQLiteCatalog, SQLiteJobScheduler
        from media_engine.models import CatalogFile, CatalogJob, CatalogSegment
        path = Path(folder) / "notes.txt"
        path.write_text("AWS VPC network routing", encoding="utf-8")
        catalog = SQLiteCatalog()
        catalog.put_source(source_id="source", user_id="alice", kind="linked_folder", root_path=folder)
        file = catalog.put_file(file_id="file", user_id="alice", source_id="source",
            path=path, source_type="TEXT", mime_type="text/plain",
            fingerprint=hashlib.sha256(path.read_bytes()).hexdigest())
        job_id = SQLiteJobScheduler().enqueue(file.current_version_id, "discover")
        connections.close_all()
        command("run_catalog_worker", "--once")
        job = CatalogJob.objects.get(pk=job_id)
        assert job.stage == "validate" and job.state == "queued"
        CatalogJob.objects.filter(pk=job_id).update(state="running", attempts=1,
            lease_owner="force-closed-process", lease_until=timezone.now() - timedelta(seconds=5))
        connections.close_all()
        command("run_catalog_worker", "--once")
        job.refresh_from_db()
        assert job.stage == "extract" and job.state == "queued"
        connections.close_all()
        command("run_catalog_worker", "--drain")
        job.refresh_from_db()
        file = CatalogFile.objects.get(pk="file")
        assert job.stage == "embed" and job.state == "paused"
        assert file.processing_status == "PAUSED"
        assert CatalogSegment.objects.filter(file_version=file.current_version).exists()
        print({"recovered_lease": True, "current_stage": job.stage,
               "state": job.state, "error_category": job.error_category,
               "segments": CatalogSegment.objects.filter(file_version=file.current_version).count()})
        connections.close_all()


if __name__ == "__main__":
    main()
