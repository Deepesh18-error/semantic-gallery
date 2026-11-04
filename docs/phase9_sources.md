# Phase 9: linked folders and managed imports

Desktop mode now registers sources through Settings → Linked folders. Enter an absolute folder on the same computer as the backend, choose an optional collection, and supply optional exclusion globs. Each source has pause/resume and scan controls. Source registration persists before returning; its initial scan runs in a dedicated synchronization process. Browser uploads inside collections remain the manual-import path: bytes are copied into a temporary managed file, flushed, atomically renamed, and registered with the same durable indexing pipeline.

Run migrations after stopping older workers. A local pre-migration catalog backup was saved under ignored `.phase0_snapshot/phase9_catalog_before.sqlite3`. Migrations 0013–0014 preserve file IDs, backfill published versions, and restore FTS/cache-revision triggers around SQLite table rebuilds.

In each terminal, set `SEMANTIC_GALLERY_STORAGE=desktop`. Start the web server, the indexing worker, and folder synchronization as separate processes:

```powershell
$env:SEMANTIC_GALLERY_STORAGE='desktop'
$env:PYTHONIOENCODING='utf-8'
backend\venv\Scripts\python.exe backend\manage.py migrate
backend\venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8000
```

```powershell
backend\venv\Scripts\python.exe backend\manage.py run_catalog_worker
```

```powershell
backend\venv\Scripts\python.exe backend\manage.py run_source_sync
```

Keep the existing Gemini project/quota configuration in the worker environment. Scanning and filename retrieval do not call Gemini. `run_source_sync --once` performs one reconciliation pass; a newly observed file still requires a later stable observation. `--no-watch` uses periodic scans only. The synchronization process reconciles at startup and periodically; Django startup does not spawn background ingestion threads.

## Source contract

- `GET /api/sources/`: list the authenticated user's sources.
- `POST /api/sources/`: register `root_path`, optional `collection_id`, `recursive`, and `exclusions`.
- `PATCH /api/sources/{id}/`: update exclusion/recursive settings, the default collection, `settle_seconds` (1–60), or `scan_interval_seconds` (1–3600).
- The same source endpoint accepts `action: pause`, `resume`, or `scan`. A scan action schedules reconciliation; it does not perform a large disk scan inside the HTTP request.

The default observation interval is five seconds, with two seconds of stable size/mtime before hashing. Files are checked again after hashing, and changing writes are discarded. Unchanged metadata uses a fast path, with content hashes reverified at least every five minutes of active reconciliation and on scanner startup. Temporary downloads, lock files, application storage, common cache directories, symlinks, and Windows reparse points are excluded. Nested linked roots for the same user are rejected. Supported formats and size limits follow `media_engine.utils`.

Watchdog events accelerate scans and mark confirmed deletions unavailable. Periodic reconciliation repairs missed events. When filesystem hints are unavailable, availability changes are detected at the next scan; search and file serving also check the physical path. A disconnected root keeps catalog identities and versions, marks files unavailable, and retries periodically. Permission and locked-file errors preserve records and surface a source error rather than treating the records as deleted. A source pause stops scans and further worker claims; an in-flight stage stops at its next control boundary.

Moves preserve IDs when the filesystem identity and fingerprint identify one missing old path. Filesystems without identities use an unambiguous missing fingerprint match. Ambiguous moves or moves combined with content/type changes are treated conservatively as a new path and an unavailable old record. Identical duplicate copies at separate paths retain distinct file IDs. Complete extraction is reused for the same user, fingerprint, source type, and extraction configuration; derivative files are copied into the new version's storage. The existing embedding cache reuses identical formatted content and configuration.

## Publication and recovery

`current_version` identifies the latest staged work; `published_version` identifies the version search uses. Reindexing a published file creates a fresh staged generation. Search retains the previous published text/vectors and thumbnail until all required stages publish atomically. Old jobs check the current generation and lease before committing. Results identify stale evidence while an update is pending. Originals in a linked folder are not snapshotted: opening a changed file opens its current bytes, and the UI states that the search evidence is from the previous indexed version.

The lifecycle tests cover stable create, unchanged content, edit publication, rename identity, delete, disconnect/reconnect, exclusion/reinclude, pause/resume, source lease recovery, duplicate extraction reuse, preserved-metadata edits, and pending thumbnails. `evaluation/source_restart_smoke.py` verifies observation recovery and one queued job across separate processes. `evaluation/source_migration_smoke.py` verifies a populated older catalog survives migration with FTS, revision triggers, and SQLite integrity intact. Native symlink creation is skipped when the Windows account lacks that privilege; physical USB unplug and interactive browser behavior remain manual checks.
