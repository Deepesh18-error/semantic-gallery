"""Verify populated Phase 8 catalog survives the Phase 9 SQLite rebuild."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    backend = Path(__file__).resolve().parents[1] / "backend"
    with tempfile.TemporaryDirectory() as temporary:
        env = {**os.environ, "SEMANTIC_GALLERY_STORAGE": "desktop",
               "SEMANTIC_GALLERY_CATALOG_PATH": str(Path(temporary) / "catalog.sqlite3"),
               "DJANGO_SETTINGS_MODULE": "core.settings", "PYTHONIOENCODING": "utf-8"}
        def migrate(target):
            subprocess.run([sys.executable, str(backend / "manage.py"), "migrate", "media_engine", target, "--noinput"],
                           env=env, check=True, capture_output=True)
        def run(code):
            subprocess.run([sys.executable, "-c", "import django; django.setup()\n" + code],
                           cwd=backend, env=env, check=True, capture_output=True, text=True, timeout=60)
        migrate("0012")
        run("""from django.db import connection
from django.db.migrations.executor import MigrationExecutor
apps = MigrationExecutor(connection).loader.project_state([('media_engine','0012_search_revision')]).apps
Source = apps.get_model('media_engine','CatalogSource')
File = apps.get_model('media_engine','CatalogFile')
Version = apps.get_model('media_engine','CatalogFileVersion')
Segment = apps.get_model('media_engine','CatalogSegment')
source = Source.objects.create(id='source', user_id='alice', kind='linked_folder', root_path='C:/example')
file = File.objects.create(id='file', user_id='alice', source=source, path='C:/example/file.txt',
                          original_name='file.txt', source_type='TEXT', mime_type='text/plain', processing_status='EMBEDDED')
version = Version.objects.create(file=file, generation=1, fingerprint='hash', size_bytes=7)
file.current_version=version; file.save()
Segment.objects.create(file_version=version, ordinal=0, representation='text', text='AWS VPC')
""")
        migrate("0014")
        run("""from django.db import connection
from media_engine.models import CatalogFile, CatalogSource
from media_engine.local_retrieval import SQLiteFullText
from media_engine.search_cache import revision
file = CatalogFile.objects.get(pk='file')
assert file.published_version_id == file.current_version_id
assert len(SQLiteFullText().search_evidence('AWS VPC', user_id='alice')) == 1
before = revision()
file.original_name = 'renamed.txt'; file.save(update_fields=['original_name'])
assert revision() > before
before = revision()
CatalogSource.objects.filter(pk='source').update(lease_owner='scan-heartbeat')
assert revision() == before
with connection.cursor() as cursor:
    cursor.execute('PRAGMA foreign_key_check'); assert cursor.fetchall() == []
    cursor.execute('PRAGMA integrity_check'); assert cursor.fetchone()[0] == 'ok'
""")
        print({'populated_catalog_preserved': True, 'fts_preserved': True, 'revision_triggers_restored': True})


if __name__ == '__main__':
    main()
