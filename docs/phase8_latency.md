# Phase 8 search experience

The browser requests local filename/keyword suggestions after 150 ms of idle typing. It sends semantic search only on Enter. Each query/filter change aborts the preceding HTTP request and invalidates its response sequence; a late response cannot replace a newer result. Aborting HTTP does not cancel a Gemini call already sent by the server. The status line distinguishes suggestions, semantic completion, unavailable semantic search, and missing semantic indexes.

Desktop mode reuses Django SQLite connections for 60 seconds. The exact vector implementation has no separate external index handle to initialize. Gemini clients and their HTTP connection pools are reused per worker thread; credential changes replace the client. Both generation 1 and generation 2 queries use the persistent embedding cache under separate configuration keys.

Result caching is process-local, bounded to 128 entries, and expires after 30 seconds. Keys include user, query, explicit filter state, collection, limit, search mode, active generation/configuration, ranking policy, and a durable catalog revision. Migration `0012` increments that revision for file, segment, vector, membership, collection, and source changes from any process. Requests during an index change are not cached under the old revision. Diagnostic evaluation requests bypass result caching. Search responses omit vector IDs, filesystem paths, collection/job queries, and large stored passages. Full details remain available through the existing file information endpoint.

Grids request actual generated thumbnails, and a missing thumbnail returns 404 instead of downloading an original image. Preview requests are aborted on unmount. The modal still opens the original on demand. The dedicated worker defaults to one concurrent stage, one CPU task, one disk task, and one network task; the existing quota scheduler reserves request/token headroom for interactive search. Higher worker concurrency is an explicit tuning choice and should be measured under load.

## Measurement

Start with `SEMANTIC_GALLERY_STORAGE=desktop`, run migrations, and keep the calibrated Phase 7 quality evaluation alongside latency runs. Use `evaluation/search_latency.py` with a JSON array of stable queries and `EVAL_AUTH_TOKEN`/`EVAL_COLLECTION_ID` in the environment:

```powershell
backend\venv\Scripts\python.exe evaluation\search_latency.py --queries evaluation\results\queries.json --mode local --scenario warm --repetitions 5 --output evaluation\results\local-warm.json
backend\venv\Scripts\python.exe evaluation\search_latency.py --queries evaluation\results\queries.json --mode semantic --scenario cached --repetitions 5 --output evaluation\results\semantic-cached.json
backend\venv\Scripts\python.exe evaluation\search_latency.py --queries evaluation\results\queries.json --mode local --scenario indexing --repetitions 5 --output evaluation\results\local-indexing.json
```

The indexing scenario requires a live indexing lease for every sample; cached samples must actually hit the result cache. Uncached cloud measurements require fresh known queries, semantic mode, one repetition, and an observed provider call. Cold samples require an operator restart of the server and one immediate request; record multiple separate cold starts before drawing percentile conclusions. Reports identify the reference machine and require at least 20 samples before marking a latency target passed. They measure HTTP/backend time, not browser paint or thumbnails.

Targets remain local p95 <300 ms, cached semantic p95 <500 ms, and uncached cloud p50 <1500 ms / p95 <3000 ms. Browser console `SEARCH_UI_METRICS` reports request round-trip, card visibility, and thumbnail fetch separately. Verify rapid typing, Enter, filter changes, route changes, and late responses in the browser while the worker indexes. There is no Electron overlay implementation in this repository yet, so overlay readiness <200 ms cannot be claimed by this phase. No full UI or live cloud latency target has been certified by unit tests.
