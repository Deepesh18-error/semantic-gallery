# Phase 10: timestamped spoken audio

The desktop worker now probes audio duration, codec, sample rate, and channels with `ffprobe`. It converts each 60-second interval, with two seconds of left overlap, to mono 16 kHz WAV using `ffmpeg`. A local `faster-whisper` model transcribes with word timestamps and voice activity detection. Overlap words are deduplicated. Search passages span at most about 18 seconds and retain absolute playback start/end times, so playback starts close to a matched phrase. Only transcript text is embedded and indexed; native sound or music similarity is outside this phase. The WAV inputs are removed after each interval. Original recordings remain unchanged.

The default is multilingual `small` on CPU with int8 compute. It has no transcription API quota or per-minute provider charge, but it needs local model storage and substantial CPU time. The model may download on first use. Configure `SEMANTIC_GALLERY_WHISPER_MODEL`, `SEMANTIC_GALLERY_WHISPER_DEVICE`, `SEMANTIC_GALLERY_WHISPER_COMPUTE_TYPE`, and `SEMANTIC_GALLERY_TRANSCRIPT_LANGUAGE` before starting the dedicated worker. A changed model or language changes the extraction configuration, so queue a reindex to use it on existing recordings. The current worker keeps the published generation available until the new transcript and its text embeddings are ready.

Install the optional dependency into the backend environment:

```powershell
cd C:\learning\PROJECTS\multimodal_search\backend
.\venv\Scripts\python.exe -m pip install -r requirements-audio.txt
```

Install `ffmpeg` and `ffprobe` and put both executables on `PATH`. Check with `ffmpeg -version` and `ffprobe -version`. The worker reports `transcriber_unavailable` when the Python model is missing or cannot load; it reports an extraction error when the media probe/conversion tools are missing. The core backend and tests remain usable without the optional speech dependency.

The file version records `duration_seconds`, `audio_codec`, `sample_rate`, `channels`, transcription configuration, successful chunk count, and failed time intervals. If at least one interval produces speech and another fails, the version publishes as `PARTIAL`; search results show the missing coverage. A wholly silent file is marked `no_speech`, not given a fabricated vector. A wholly failed transcription is marked `transcription_failed`. The existing text embedding stage still consumes its configured Gemini quota; local transcription does not remove that quota requirement. Keyword text becomes available after extraction, while full publication waits for compatible vectors.

## Backend selection and measurement

This implementation selects `faster-whisper` as the initial local backend. Its documented CPU int8 mode, word timestamps, and VAD fit the Windows desktop path. OpenAI Whisper and whisper.cpp are alternative implementations to measure if the reference machine misses the indexing budget. The repository has no reviewed audio corpus or installed speech runtime on this machine, so **accuracy and throughput have not been measured yet**. The choice is provisional until the following benchmark passes on the actual release machine.

Prepare a private JSON manifest with at least one case in each category: `technical_vocabulary`, `accent`, `silence`, `background_noise`, `overlap`. Include a recording of at least ten minutes with a phrase near its end. Each entry has `id`, `category`, `path`, `reference` (human corrected transcript), and optionally `expected_phrase` and `expected_second`. Do not commit private recordings or manifests. Example entry:

```json
{"id":"meeting-technical","category":"technical_vocabulary","path":"C:/recordings/meeting.wav","reference":"...","expected_phrase":"AWS VPC private subnet","expected_second":742}
```

Run from the repository root:

```powershell
.\backend\venv\Scripts\python.exe evaluation\audio_benchmark.py C:\path\to\private-audio-manifest.json --models base small
```

The command reports per-case word error rate, phrase found, seek error in seconds, elapsed indexing time, realtime factor, partial coverage, and aggregate results. Compare both model sizes on the same recordings. Confirm that the deep phrase is found and playback starts near the human reviewed time. Choose the smallest model meeting the reviewed vocabulary and timestamp targets while leaving acceptable CPU/RAM headroom for search. The current `small` default is not a claim that the acceptance gate has passed.

## Playback

The frontend requests a short-lived, file-scoped playback URL using its normal authenticated API call. The native audio element then requests byte ranges from the stream endpoint, so it can seek to a deep timestamp without first downloading the full recording. The signed URL expires after one hour and remains limited to that user's available file. Actual browser playback on long user recordings still needs manual verification on the release machine.

Sources: [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [OpenAI Whisper](https://github.com/openai/whisper), [whisper.cpp](https://github.com/ggml-org/whisper.cpp).
