# Phase 3: durable indexing worker and provider quota

Use `SEMANTIC_GALLERY_STORAGE=desktop`. The Django web process only writes a file, its catalog entry, and a `discover` job before returning success. It does not start indexing threads. Run `run_catalog_worker` as a separate process. Jobs advance through `discover → validate → extract → keyword-index → embed → publish`; optional captions are queued afterward at lower priority.

## Start

Set `GOOGLE_API_KEY` and `GEMINI_PROJECT_ID` in the backend environment. The project ID identifies the shared quota; using more API keys in the same project does not increase its limits. The active RPM, input TPM, and RPD must be copied from [Google AI Studio](https://aistudio.google.com/) for each model. Google's [rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits) says limits are project and model dependent, can vary with tier, and RPD resets at midnight Pacific time. This repository cannot read the user's active AI Studio account limits automatically. No RPM, TPM, RPD, or pricing values are guessed by the code. Provider calls remain paused until they are configured.

From the repository root in PowerShell, in **both** terminals:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:SEMANTIC_GALLERY_STORAGE='desktop'
$env:GEMINI_PROJECT_ID='YOUR_PROJECT_ID'
```

After reading the account's limits, configure the embedding model with the actual values. Configure the Flash model separately if optional captions or legacy intent/rerank calls are enabled. Prices and a daily spend cap are optional; enter the current values for the account's billing plan if spend estimates are needed.

```powershell
backend\venv\Scripts\python.exe backend\manage.py migrate
backend\venv\Scripts\python.exe backend\manage.py configure_provider_quota --model gemini-embedding-2 --rpm ACTUAL_RPM --input-tpm ACTUAL_INPUT_TPM --rpd ACTUAL_RPD
backend\venv\Scripts\python.exe backend\manage.py catalog_health
```

Start the web server in one terminal and the worker in another:

```powershell
backend\venv\Scripts\python.exe backend\manage.py runserver 127.0.0.1:8000
```

```powershell
backend\venv\Scripts\python.exe backend\manage.py run_catalog_worker --concurrency 2 --cpu 2 --disk 2 --network 1
```

`--once` executes one ready stage and exits. `--drain` runs ready stages until none are available. They are useful for diagnosis. Optional captions require `SEMANTIC_GALLERY_ENABLE_CAPTIONS=1` and a configured `gemini-3.5-flash-lite` quota; they never block publication of the file.

New desktop vectors use the stable [`gemini-embedding-2`](https://ai.google.dev/gemini-api/docs/models/gemini-embedding-2) model. The old Pinecone vectors from `gemini-embedding-2-preview` remain stored but are not mixed with stable-model scores. Existing files will need a separately controlled reindex before they can participate in local vector search; metadata migration alone does not call Gemini. The stable model's documented input ceiling is 8,192 tokens, with 120-second video and 180-second audio limits. This worker uses shorter adaptive media slices and local PDF text extraction rather than sending a whole PDF to the embedding endpoint.

For text retrieval, the stable model uses Google's documented `task: search result | query: ...` prefix and `title: ... | text: ...` document format. Its `task_type` request field is not supported. The exact format is included in the stored index configuration so future changes require a distinct generation.

To inspect a small reindex batch without calling Gemini, run `backend\venv\Scripts\python.exe backend\manage.py queue_model_reindex --limit 20`. Add `--enqueue` only when the quota is configured and the worker is running. The command queues at most the requested count and is safe to repeat. Do not queue the full existing gallery until the Phase 0 evaluation and throughput checks have been repeated with the stable model.

## Durability and control

Job state, progress, attempts, error category, lease owner, lease expiry, and next retry time are in SQLite. The worker renews a lease during long extraction or network calls. An expired lease can be claimed after a crash. Every stage can run again: discovery checks the fingerprint, extraction replaces deterministic segments, FTS5 follows catalog writes, embedding skips already stored vector IDs, and publishing checks that all current-version segments have vectors. Changed files create a new version and job; stale work cannot publish.

Collection cards show stage/progress and offer **Pause**, **Resume**, **Cancel**, and **Retry**. The same actions are available at `POST /api/media/jobs/{job_id}/{action}/` with the user's bearer token. `GET /api/provider/usage/` reports daily request counts, token counts, estimated spend, and configured model names. A cancellation during an API request takes effect when the request returns. Deleting a file with an active job first requests cancellation and returns HTTP 409; repeat the deletion after the job stops.

Temporary network failures retry with bounded exponential backoff. Rate-limit responses respect provider retry timing; daily limits and credential failures pause provider work. Unsupported formats and extraction failures stop without automatic retries. Manual retry resumes the failed stage. A maximum of five attempts prevents unbounded repeated requests. Reservations are written before every Gemini call; an interrupted call remains counted conservatively. If the process dies after a provider response but before its vector is saved, that one segment can be called again after lease recovery. The stable vector ID prevents duplicate stored results, and the durable reservation plus attempt cap prevent uncontrolled request bursts.

The quota scheduler serializes reservations in SQLite across web and worker processes. It enforces per-project, per-model rolling RPM and input TPM and Pacific-day RPD. Background work uses at most 75% of the minute limits and 90% of the daily request limit to leave room for searches. An account with a very small limit may not have a meaningful reserved search slot. Provider response usage replaces token estimates when supplied. Token and spend figures remain estimates when the provider does not return usage metadata.

The legacy MongoDB mode still contains its previous application-startup threads for compatibility. The Phase 3 lifecycle and pause/resume/cancel controls apply to the SQLite desktop mode. Existing gallery metadata must be imported using the Phase 2 procedure before switching that gallery to desktop mode.

`evaluation/job_restart_smoke.py` creates an isolated temporary catalog, runs separate worker processes, simulates a force-closed process by expiring its lease, and confirms the next process continues at `extract`. It then reaches `embed` and pauses because the temporary project has no quota configuration, without making a provider call. Run it with `backend\venv\Scripts\python.exe evaluation\job_restart_smoke.py`.

`evaluation/quota_concurrency_smoke.py` starts four independent processes against one temporary project/model quota of one RPM. The verified result is one durable reservation and three rate-limited calls; no process bypassed the shared limit.

`evaluation/job_claim_concurrency_smoke.py` starts four worker processes contending for one queued job. The verified result is one claim and three empty claims. Migration `0007` also collapses any duplicate active jobs left by the older scheduler before adding a database uniqueness constraint.
