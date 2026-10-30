"""Repeatable, dependency-free local vector lifecycle comparison.

Uses deterministic 3072-dimensional vectors with the shape of current Gemini output.
These are synthetic vectors, not exported user embeddings. No API calls are made.
"""
import argparse
import json
from math import sumprod
import mmap
import os
import sqlite3
import statistics
import struct
import tempfile
import time
import tracemalloc
from array import array
from pathlib import Path
from random import Random


def percentile(values, fraction):
    values = sorted(values)
    return round(values[min(len(values) - 1, int((len(values) - 1) * fraction))], 2)


def score(query, blob):
    vector = array("f")
    vector.frombytes(blob)
    return sumprod(query, vector)


def run(count=1000, dimensions=3072, queries=7):
    rng = Random(42)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        db_path = root / "vectors.sqlite3"
        flat_path = root / "vectors.bin"
        sql = sqlite3.connect(db_path)
        sql.execute("CREATE TABLE vectors (id INTEGER PRIMARY KEY, type TEXT, collection TEXT, data BLOB)")
        sql.execute("CREATE INDEX filtered_vectors ON vectors(type, collection)")
        metadata = []
        with flat_path.open("wb") as flat:
            for idx in range(count):
                vec = array("f", [rng.uniform(-1, 1) for _ in range(dimensions)])
                norm = sum(x * x for x in vec) ** 0.5
                vec = array("f", (x / norm for x in vec))
                blob = vec.tobytes()
                kind = ("IMAGE", "DOCUMENT", "AUDIO")[idx % 3]
                collection = f"collection-{idx % 4}"
                sql.execute("INSERT INTO vectors VALUES (?, ?, ?, ?)", (idx, kind, collection, blob))
                flat.write(blob)
                metadata.append((kind, collection))
        sql.commit()
        sql.close()
        query = array("f", [rng.uniform(-1, 1) for _ in range(dimensions)])
        norm = sum(x * x for x in query) ** 0.5
        query = array("f", (x / norm for x in query))
        results = {}
        for engine in ("sqlite_exact", "flat_mmap"):
            tracemalloc.start()
            started = time.perf_counter()
            if engine == "sqlite_exact":
                handle = sqlite3.connect(db_path)
                handle.execute("PRAGMA query_only = ON")
            else:
                file = flat_path.open("rb")
                handle = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
            startup_ms = (time.perf_counter() - started) * 1000
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            latencies = []
            all_gallery_latencies = []
            expected_ids = None
            for _ in range(queries):
                started = time.perf_counter()
                if engine == "sqlite_exact":
                    rows = handle.execute("SELECT id, data FROM vectors WHERE type=? AND collection=?",
                                          ("IMAGE", "collection-0"))
                    top = sorted(((score(query, blob), idx) for idx, blob in rows), reverse=True)[:10]
                else:
                    width = dimensions * 4
                    top = sorted(((score(query, handle[idx * width:(idx + 1) * width]), idx)
                                  for idx, labels in enumerate(metadata)
                                  if labels == ("IMAGE", "collection-0")), reverse=True)[:10]
                latencies.append((time.perf_counter() - started) * 1000)
                if expected_ids is None:
                    expected_ids = [idx for _, idx in top]
                assert expected_ids == [idx for _, idx in top]
            for _ in range(min(queries, 5)):
                started = time.perf_counter()
                if engine == "sqlite_exact":
                    rows = handle.execute("SELECT id, data FROM vectors")
                    top = sorted(((score(query, blob), idx) for idx, blob in rows), reverse=True)[:10]
                else:
                    width = dimensions * 4
                    top = sorted(((score(query, handle[idx * width:(idx + 1) * width]), idx)
                                  for idx in range(count)), reverse=True)[:10]
                all_gallery_latencies.append((time.perf_counter() - started) * 1000)
            handle.close()
            if engine == "flat_mmap":
                file.close()
            results[engine] = {"startup_ms": round(startup_ms, 2),
                               "filtered_query_p50_ms": percentile(latencies, .5),
                               "filtered_query_p95_ms": percentile(latencies, .95),
                               "all_gallery_query_p95_ms": percentile(all_gallery_latencies, .95),
                               "python_peak_memory_bytes": peak,
                               "disk_bytes": (db_path if engine == "sqlite_exact" else flat_path).stat().st_size,
                               "top10_ids": expected_ids}
        assert results["sqlite_exact"]["top10_ids"] == results["flat_mmap"]["top10_ids"]
        sql = sqlite3.connect(db_path)
        sql.execute("DELETE FROM vectors WHERE id=?", (results["sqlite_exact"]["top10_ids"][0],))
        sql.commit()
        sql.close()
        reopened = sqlite3.connect(db_path)
        deleted = reopened.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == count - 1
        reopened.close()
        results["sqlite_exact"]["delete_persists_after_restart"] = deleted
        results["flat_mmap"]["delete_persists_after_restart"] = False
        return {"count": count, "dimensions": dimensions, "queries": queries,
                "filter": {"source_type": "IMAGE", "collection": "collection-0"},
                "engines": results,
                "selection_budget": {"filtered_p95_ms": 250, "all_gallery_p95_ms": 750,
                                     "required": ["filter", "persistence", "deletion", "Windows"]},
                "selected": "sqlite_exact" if results["sqlite_exact"]["filtered_query_p95_ms"] <= 250
                and results["sqlite_exact"]["all_gallery_query_p95_ms"] <= 750 and deleted else None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--dimensions", type=int, default=3072)
    parser.add_argument("--queries", type=int, default=7)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    report = run(args.count, args.dimensions, args.queries)
    payload = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
