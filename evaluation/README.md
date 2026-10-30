# Phase 0 evaluation

The development and held-out labels in `cases.json` come from the supplied gallery PDF. `extension_cases` lists required future cases that cannot yet be scored because the exact source file and answer have not been confirmed. Keep held-out labels unchanged while tuning search.

Run `python evaluation/run.py --mode offline` to reproduce the documented food failure without credentials or network calls. This is a fixed historical fixture, not a live quality measurement.

For live runs, start MongoDB and Django, ensure the collection and vectors are accessible, then use an authenticated token and that collection's ID:

`python evaluation/run.py --mode live --collection-id COLLECTION_ID --token TOKEN --split both --output evaluation/results/baseline.json`

Credentials can instead be passed through `EVAL_AUTH_TOKEN` and `EVAL_COLLECTION_ID`. Never commit result files containing personal filenames or tokens. The runner sends one request per case per repetition. It does not upload, modify, or reindex media. `--repetitions 3` gives a basic warm-run distribution; record cold-start and indexing-load runs separately with clear filenames.

When local loopback HTTP is unavailable but the configured services are reachable, use `--mode direct --collection-id COLLECTION_ID --user-id USER_ID`. This calls the current search service in-process and does not measure Django HTTP handling or frontend rendering. Run it with the backend virtual environment. Never use this mode to claim end-to-end latency.

Live reports contain per-case rankings, recall at five, first relevant rank, type violations, server timings, provider call counts, and summary p50/p95 latency. Missing-answer cases require human review because the gallery PDF cannot prove that a query has no matching file. The runner does not silently count these cases as correct.

Backend logs emit `[SEARCH_METRICS]` and `[INDEX_METRICS]` JSON lines. Browser console emits `[SEARCH_UI_METRICS]` for request round trip, card visibility, and preview fetch. For an indexing-throughput sample, process a known number of files, collect the index lines, and divide completed files by elapsed wall time. The current backend has no durable bulk-job ID, so a cross-restart throughput run remains a Phase 3 task.

Use the same collection, index generation, case labels, machine, and network conditions when comparing search changes. Treat fewer than 20 timings as a smoke sample; do not infer a reliable p95 from seven queries.

The first direct baseline is in `baseline_summary.json`; detailed personal filenames stay in ignored `results/`. It reproduces the food failure. The 24 existing items do not contain paired indexing timestamps, so historical throughput cannot be reconstructed. `[INDEX_METRICS]` lines now make future controlled runs measurable. Browser timing must be collected with the frontend open; direct service runs cannot measure it.

Run checks with `python -m unittest discover -s evaluation -p test_run.py`, the backend's Python `compileall`, and `npm run build` in `frontend`. Live provider and database behavior requires the configured services.
