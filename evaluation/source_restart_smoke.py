"""Persist a half-observed file, restart the scanner, and recover a stable import."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    backend = Path(__file__).resolve().parents[1] / "backend"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "library"
        root.mkdir()
        (root / "notes.txt").write_text("AWS VPC", encoding="utf-8")
        env = {**os.environ, "SEMANTIC_GALLERY_STORAGE": "desktop",
               "SEMANTIC_GALLERY_CATALOG_PATH": str(Path(temporary) / "catalog.sqlite3"),
               "DJANGO_SETTINGS_MODULE": "core.settings", "PYTHONIOENCODING": "utf-8"}
        subprocess.run([sys.executable, str(backend / "manage.py"), "migrate", "--noinput"],
                       env=env, check=True, capture_output=True)
        def run(code):
            result = subprocess.run([sys.executable, "-c", "import django; django.setup()\n" + code],
                env=env, cwd=backend, capture_output=True, text=True, check=True, timeout=60)
            return json.loads(result.stdout)
        first = run(f"""from media_engine.source_sync import register_source, sync_source
from media_engine.models import CatalogFile, SourceObservation
import json
source = register_source('alice', {str(root)!r})
sync_source(source.id, force=True)
print(json.dumps({{'files': CatalogFile.objects.count(), 'observations': SourceObservation.objects.count()}}))
""")
        assert first == {"files": 0, "observations": 1}, first
        second = run("""from datetime import timedelta
from django.utils import timezone
from media_engine.source_sync import sync_source
from media_engine.models import CatalogFile, CatalogSource, CatalogJob, SourceObservation
import json
SourceObservation.objects.update(stable_since=timezone.now()-timedelta(seconds=3))
sync_source(CatalogSource.objects.get().id, force=True)
print(json.dumps({'files': CatalogFile.objects.count(), 'jobs': CatalogJob.objects.count()}))
""")
        assert second == {"files": 1, "jobs": 1}, second
        print(json.dumps({"restart_recovered_observation": True, "durable_job_count": 1}))


if __name__ == "__main__":
    main()
