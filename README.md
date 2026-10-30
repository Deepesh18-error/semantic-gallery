# Semantic Gallery

Current application: React/Vite frontend, Django REST backend, MongoDB metadata, Pinecone vectors, Google GenAI embeddings and descriptions, local media vault, FFmpeg media slicing, PyMuPDF PDF extraction, and optional Tesseract OCR. Electron is planned but is not yet present in this repository.

## Development setup

Use Python 3.13 with a virtual environment in `backend`, install `backend/requirements.txt`, and install the required native tools FFmpeg/FFprobe and Tesseract if processing video/audio and OCR images. Install frontend packages with `npm ci` in `frontend`. Configure `backend/.env` with `MONGO_CONNECTION_STRING`, `MONGO_DB_NAME`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `GOOGLE_API_KEY`, and `JWT_SECRET`; use `TESSERACT_PATH` where needed. Keep credentials out of Git.

Start MongoDB, run `python manage.py runserver` from `backend`, then run `npm run dev` from `frontend`. The frontend currently calls `http://127.0.0.1:8000/api`. The existing dependency and model combinations are a development baseline, not a validated release lockfile. The settings screen refers to endpoints that are not implemented yet.

## Phase 0 measurement

See [evaluation/README.md](evaluation/README.md) for offline reproduction, authenticated live evaluation, timing definitions, and test commands. The pre-Phase-0 working tree snapshot remains in `.phase0_snapshot/`; it is local and ignored by Git.

## Phase 1 search contract

`backend/media_engine/contracts.py` defines search schema version 1. Query text must be 1–500 characters; `limit` must be an integer from 1 to 20. The optional `file_type` is one of `IMAGE`, `VIDEO`, `AUDIO`, `DOCUMENT`, `TEXT`, or `ALL`. A collection scope must belong to the authenticated user. Invalid requests return a structured 400 response before embedding calls.

The source `media_type` is separate from the indexed `representation_type`. Old document vectors tagged `TEXT` remain searchable, but explicit type filters are verified against the source file record. Search results retain legacy fields for the React UI and add `schema_version`, `file_id`, `source_type`, `representation`, `evidence_source`, `location`, `availability`, `indexing_coverage`, `file_version`, and `index_config`. Older files may have null version/config/location fields until reindexed; these values are never invented.

New uploads and watched files receive a SHA-256 `file_version`. Newly written vectors include source and representation metadata. `PARTIALLY_EMBEDDED` files are searchable with coverage shown in the response and gallery. File bytes, metadata, status, retry, and delete endpoints scope lookups to the authenticated user.

## Commit workflow

The repository had preexisting modified and untracked implementation files before Phase 0. Review and commit that work separately if it is intended for the project. Then stage only Phase 0 files/hunks, inspect `git diff --cached`, run the checks, and commit with a message such as `Add Phase 0 search evaluation and timing`. Do not use `git add .` until the untracked files and local data have been reviewed.
