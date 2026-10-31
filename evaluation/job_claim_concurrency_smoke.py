"""Four worker processes contend for one job; exactly one may claim it."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    backend = Path(__file__).resolve().parents[1] / "backend"
    with tempfile.TemporaryDirectory() as folder:
        env = {**os.environ, "SEMANTIC_GALLERY_STORAGE": "desktop",
               "SEMANTIC_GALLERY_CATALOG_PATH": str(Path(folder) / "catalog.sqlite3"),
               "DJANGO_SETTINGS_MODULE": "core.settings", "PYTHONIOENCODING": "utf-8"}
        subprocess.run([sys.executable, str(backend / "manage.py"), "migrate", "--noinput"],
                       env=env, check=True, capture_output=True)
        os.environ.update(env)
        sys.path.insert(0, str(backend))
        import django
        django.setup()
        from django.db import connections
        from media_engine.catalog_store import SQLiteCatalog, SQLiteJobScheduler
        root = Path(folder)
        path = root / "file.txt"
        path.write_text("burger")
        catalog = SQLiteCatalog()
        catalog.put_source(source_id="source", user_id="alice", kind="linked_folder", root_path=root)
        file = catalog.put_file(file_id="file", user_id="alice", source_id="source", path=path,
                                source_type="TEXT", mime_type="text/plain", fingerprint="hash")
        SQLiteJobScheduler().enqueue(file.current_version_id, "discover")
        connections.close_all()
        code = """import django; django.setup()
from media_engine.catalog_store import SQLiteJobScheduler
from uuid import uuid4
job = SQLiteJobScheduler().claim(str(uuid4()))
print(job.id if job else 'none')
"""
        processes = [subprocess.Popen([sys.executable, "-c", code], cwd=backend, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        outcomes = []
        for process in processes:
            out, err = process.communicate(timeout=30)
            if process.returncode:
                raise RuntimeError(err)
            outcomes.append(out.strip())
        assert sum(value != "none" for value in outcomes) == 1, outcomes
        print({"claimed": 1, "unclaimed": 3})
        connections.close_all()


if __name__ == "__main__":
    main()
