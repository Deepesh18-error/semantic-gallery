"""Verify one project quota is shared by concurrent independent processes."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    backend = Path(__file__).resolve().parents[1] / "backend"
    with tempfile.TemporaryDirectory() as folder:
        env = os.environ.copy()
        env.update({"SEMANTIC_GALLERY_STORAGE": "desktop", "GEMINI_PROJECT_ID": "smoke-project",
                    "SEMANTIC_GALLERY_CATALOG_PATH": str(Path(folder) / "catalog.sqlite3"),
                    "PYTHONIOENCODING": "utf-8"})
        subprocess.run([sys.executable, str(backend / "manage.py"), "migrate", "--noinput"],
                       env=env, check=True, capture_output=True)
        os.environ.update(env)
        sys.path.insert(0, str(backend))
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
        import django
        django.setup()
        from django.db import connections
        from media_engine.models import ProviderCall, ProviderQuota
        ProviderQuota.objects.create(project_id="smoke-project", model="smoke-model",
                                     rpm=1, input_tpm=100, rpd=10)
        connections.close_all()
        code = """import django; django.setup()
from media_engine.provider_quota import reserve, QuotaError
try:
    reserve('smoke-model', 'query_embedding', 10, priority='interactive')
    print('reserved')
except QuotaError as error:
    print(error.category)
"""
        processes = [subprocess.Popen([sys.executable, "-c", code], cwd=backend,
                                     env={**env, "DJANGO_SETTINGS_MODULE": "core.settings"},
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        outcomes = []
        for process in processes:
            out, err = process.communicate(timeout=30)
            if process.returncode:
                raise RuntimeError(err)
            outcomes.append(out.strip())
        assert outcomes.count("reserved") == 1, outcomes
        assert outcomes.count("rate_limit") == 3, outcomes
        assert ProviderCall.objects.count() == 1
        print({"reserved": 1, "rate_limited": 3, "durable_calls": ProviderCall.objects.count()})
        connections.close_all()


if __name__ == "__main__":
    main()
