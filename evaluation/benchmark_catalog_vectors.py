"""Measure the production SQLiteExactVectors query against a temporary catalog."""
import json
import os
import sys
import tempfile
import time
from array import array
from pathlib import Path
from random import Random


def percentile(values, fraction):
    values = sorted(values)
    return round(values[min(len(values) - 1, int((len(values) - 1) * fraction))], 2)


def run(count=5000, dimensions=3072, queries=15):
    with tempfile.TemporaryDirectory() as root:
        os.environ["SEMANTIC_GALLERY_STORAGE"] = "desktop"
        os.environ["SEMANTIC_GALLERY_CATALOG_PATH"] = str(Path(root) / "catalog.sqlite3")
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import connection
        from media_engine.local_retrieval import SQLiteExactVectors
        call_command("migrate", verbosity=0)
        rng = Random(42)
        now = "2026-01-01 00:00:00"
        connection.set_autocommit(False)
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO media_engine_catalogsource (id,user_id,kind,root_path,state,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                           ["source", "alice", "linked_folder", root, "active", now])
            cursor.execute("INSERT INTO media_engine_catalogcollection (id,user_id,name,description,theme_color,icon_tag,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                           ["collection-0", "alice", "test", "", "#3498db", "folder", now])
            for idx in range(count):
                kind = ("IMAGE", "DOCUMENT", "AUDIO")[idx % 3]
                cursor.execute("""INSERT INTO media_engine_catalogfile
                    (id,user_id,source_id,path,original_name,source_type,mime_type,size_bytes,modified_ns,available,
                     processing_status,error_message,thumbnail_path,ai_description,description_status,current_version_id,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [f"file-{idx}", "alice", "source", str(Path(root) / f"{idx}.jpg"), f"{idx}.jpg",
                     kind, "image/jpeg", 1, 1, 1, "EMBEDDED", "", "", "", "DONE", None, now])
                cursor.execute("""INSERT INTO media_engine_catalogfileversion
                    (file_id,generation,fingerprint,size_bytes,modified_ns,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)""", [f"file-{idx}", 1, f"hash-{idx}", 1, 1, now])
                version_id = cursor.lastrowid
                cursor.execute("UPDATE media_engine_catalogfile SET current_version_id=%s WHERE id=%s", [version_id, f"file-{idx}"])
                cursor.execute("""INSERT INTO media_engine_catalogsegment
                    (file_version_id,ordinal,representation,text,derivative_path,page_number,start_seconds,end_seconds,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [version_id, 0, "visual", "", "", None, None, None, now])
                segment_id = cursor.lastrowid
                if idx % 4 == 0:
                    cursor.execute("INSERT INTO media_engine_catalogmembership (collection_id,file_id,created_at) VALUES (%s,%s,%s)",
                                   ["collection-0", f"file-{idx}", now])
                vector = array("f", (rng.uniform(-1, 1) for _ in range(dimensions)))
                norm = sum(x * x for x in vector) ** .5
                vector = array("f", (x / norm for x in vector))
                cursor.execute("""INSERT INTO media_engine_catalogvectorrecord
                    (id,segment_id,backend,model_config,index_generation,dimensions,vector_blob,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [f"vector-{idx}", segment_id, "sqlite_exact", "benchmark", 1, dimensions, vector.tobytes(), now])
        connection.commit()
        connection.set_autocommit(True)
        query = array("f", (rng.uniform(-1, 1) for _ in range(dimensions)))
        store = SQLiteExactVectors()
        filtered = []
        all_gallery = []
        for _ in range(queries):
            started = time.perf_counter()
            store.query(query, user_id="alice", model_config="benchmark",
                        collection_id="collection-0", source_type="IMAGE", limit=10)
            filtered.append((time.perf_counter() - started) * 1000)
        for _ in range(min(queries, 5)):
            started = time.perf_counter()
            store.query(query, user_id="alice", model_config="benchmark", limit=10)
            all_gallery.append((time.perf_counter() - started) * 1000)
        report = {"count": count, "dimensions": dimensions, "filtered_p50_ms": percentile(filtered, .5),
                  "filtered_p95_ms": percentile(filtered, .95),
                  "all_gallery_p95_ms": percentile(all_gallery, .95)}
        connection.close()
        return report


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
