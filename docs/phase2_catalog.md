# Phase 2: desktop catalog and storage

The SQLite catalog is selected with `SEMANTIC_GALLERY_STORAGE=desktop`. The old MongoDB route remains the default until the existing data is imported. Desktop mode does not open a MongoDB client or start the legacy watcher/description threads.

## Start or inspect the desktop catalog

From the repository root in PowerShell:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:SEMANTIC_GALLERY_STORAGE='desktop'
backend\venv\Scripts\python.exe backend\manage.py migrate
backend\venv\Scripts\python.exe backend\manage.py catalog_health
backend\venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8000
```

`SEMANTIC_GALLERY_CATALOG_PATH` can point to another SQLite file for an isolated run. Preserve the SQLite file and `backend/media_vault` together. SQLite stores metadata, small segment text, job state, and float32 vectors; original media and derivative files stay on disk. Migrations `0001` through `0003` create the schema and FTS5 index. `catalog_health` runs SQLite's integrity check and reports missing media paths after a fresh process start.

## Import existing data

The import copies Mongo users, collections, file metadata, versions, and Pinecone vector IDs into SQLite. IDs remain unchanged. File paths must resolve inside `backend/media_vault`; missing files are cataloged as unavailable. The import does not call Gemini, re-embed, change files, or delete Mongo/Pinecone data. It is idempotent for unchanged versions.

With the MongoDB service available, run in legacy mode so the command can read it:

```powershell
$env:SEMANTIC_GALLERY_STORAGE='legacy'
backend\venv\Scripts\python.exe backend\manage.py import_legacy_catalog --dry-run
backend\venv\Scripts\python.exe backend\manage.py import_legacy_catalog
```

If MongoDB is unavailable, use a private JSON export with `users`, `collections`, and `media_items` arrays and the same fields as the Mongo documents. Run `--dry-run --input C:\path\to\private-export.json` first, followed by `--input` without `--dry-run`. The export contains password hashes, so keep it outside the repository. After import, switch to desktop mode and run `catalog_health`.

Existing Pinecone IDs are references only. Their vector values are not fetched during metadata migration. Search can still compare against Pinecone when the provider and network are available. Existing PDF passages are not present in the SQLite FTS index unless they are re-extracted later; metadata migration itself does not trigger that work.

## Storage contracts and current behavior

`storage_interfaces.py` defines catalog, full-text, vector, embedding, extraction, and job scheduling boundaries. `SQLiteCatalog`, `SQLiteFullText`, `SQLiteExactVectors`, `SQLiteJobScheduler`, and `PineconeVectors` are implementations. File identity and file version are separate, with a new generation only when the fingerprint changes. Segment rows carry passage, page, frame, transcript, or derivative references. A vector row identifies its segment, model configuration, backend, and index generation. Job leases and retries survive restarts.

Desktop API routes support SQLite registration/login, collection creation/listing, managed upload, media listing and serving, search, status, retry queueing, and deletion. New uploads now receive a durable `discover` job consumed by the dedicated Phase 3 worker. See [Phase 3 setup](phase3_jobs_and_quota.md). Imported files retain their previous status and Pinecone references. Local full-text retrieval works for segments present in SQLite. The desktop search route uses a simpler ranker than the legacy search pipeline; keep the Phase 0 evaluation set as the accuracy comparison before changing the default route.

## Local vector selection gate

Run `backend\venv\Scripts\python.exe evaluation\vector_storage_experiment.py --count 5000 --queries 15 --output evaluation\vector_storage_results.json`. The committed result is from this Windows machine with deterministic 3072-dimensional vectors and a source-type plus collection filter. It compares SQLite exact cosine scanning with a flat memory-mapped exact scan. SQLite exact is selected because it meets the gate and has transactional upsert, filter, restart persistence, and deletion. The flat file experiment lacks safe deletion and is not selected. The gate is filtered p95 at most 250 ms and all-gallery p95 at most 750 ms for 5,000 vectors, plus lifecycle support. These timings exclude query embedding, Django request overhead, and provider calls. The memory number measures Python allocation during startup, not OS mapped pages or peak process RSS. Synthetic vectors match the stored dimensionality but do not establish ranking quality; rerun with real embeddings and release packaging before Phase 6.

`evaluation/benchmark_catalog_vectors.py` exercises the actual `SQLiteExactVectors.query` path against a temporary 5,000-file catalog. Its measured filtered p95 was 151.32 ms and all-gallery p95 was 653.04 ms on this machine. The sample is small and the all-gallery result has limited headroom, so repeat under indexing load and in the packaged Windows build before committing to the Phase 6 release engine.
