"""Local, timestamped spoken-audio extraction for the desktop worker."""
import json
import math
import os
import re
import threading
from pathlib import Path

from django.conf import settings

from .desktop_extraction import ExtractedSegments, ExtractionError, _run


CHUNK_SECONDS = 60
OVERLAP_SECONDS = 2
_model_lock = threading.Lock()
_models = {}


def transcription_config():
    backend = os.getenv("SEMANTIC_GALLERY_TRANSCRIBER", "faster-whisper")
    model = os.getenv("SEMANTIC_GALLERY_WHISPER_MODEL", "small")
    language = os.getenv("SEMANTIC_GALLERY_TRANSCRIPT_LANGUAGE", "auto")
    return f"audio-v1:{backend}:{model}:{language}:60s:2s:18s-passages:words"


def _model():
    backend = os.getenv("SEMANTIC_GALLERY_TRANSCRIBER", "faster-whisper")
    if backend != "faster-whisper":
        raise ExtractionError("unsupported_format", f"Unknown transcription backend: {backend}")
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise ExtractionError("transcriber_unavailable", "Install the optional faster-whisper dependency") from error
    name = os.getenv("SEMANTIC_GALLERY_WHISPER_MODEL", "small")
    device = os.getenv("SEMANTIC_GALLERY_WHISPER_DEVICE", "cpu")
    compute_type = os.getenv("SEMANTIC_GALLERY_WHISPER_COMPUTE_TYPE", "int8" if device == "cpu" else "float16")
    key = (name, device, compute_type)
    with _model_lock:
        if key not in _models:
            try:
                _models[key] = WhisperModel(name, device=device, compute_type=compute_type)
            except Exception as error:
                raise ExtractionError("transcriber_unavailable", f"Could not load transcription model: {error}") from error
        return _models[key]


def _probe(path):
    try:
        payload = json.loads(_run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                   "-of", "json", str(path)]))
        streams = [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"]
        duration = float(payload.get("format", {}).get("duration", 0))
    except (ValueError, TypeError, KeyError) as error:
        raise ExtractionError("extraction_failed", "Audio duration or codec could not be read") from error
    if not streams or not math.isfinite(duration) or duration <= 0:
        raise ExtractionError("unsupported_format", "No valid audio stream or duration")
    codec = streams[0].get("codec_name")
    if not codec:
        raise ExtractionError("unsupported_format", "Audio codec is unknown")
    return duration, codec, streams[0].get("sample_rate"), streams[0].get("channels")


def _transcribe_chunk(model, wav_path, offset, duration):
    language = os.getenv("SEMANTIC_GALLERY_TRANSCRIPT_LANGUAGE", "auto")
    try:
        segments, _ = model.transcribe(str(wav_path), word_timestamps=True, vad_filter=True,
            language=None if language == "auto" else language, condition_on_previous_text=False)
        words = []
        for segment in segments:
            if segment.words:
                for word in segment.words:
                    token = word.word.strip()
                    if token and math.isfinite(word.start) and math.isfinite(word.end):
                        words.append((max(0, offset + word.start),
                                      min(duration, offset + word.end), token))
            elif segment.text.strip() and math.isfinite(segment.start) and math.isfinite(segment.end):
                # Segment timing is less precise but still lets playback jump to the phrase.
                words.append((max(0, offset + segment.start),
                              min(duration, offset + segment.end), segment.text.strip()))
        return words
    except Exception as error:
        raise ExtractionError("transcription_failed", str(error)) from error


def _merge_words(chunks):
    merged = []
    for overlap_start, words in chunks:
        for start, end, text in words:
            if end <= start or not text.strip():
                continue
            normalized = re.sub(r"\W+", "", text).casefold()
            if overlap_start > 0 and start < overlap_start + OVERLAP_SECONDS + 0.5 and any(
                   re.sub(r"\W+", "", prior[2]).casefold() == normalized and
                   abs(prior[0] - start) <= 0.5
                   for prior in merged[-20:]):
                continue
            merged.append((start, end, text))
    merged.sort(key=lambda row: (row[0], row[1]))
    return merged


def _passages(words, title):
    passages = []
    current = []
    for word in words:
        if current and (word[1] - current[0][0] > 18 or
                        len(" ".join(item[2] for item in current)) + len(word[2]) > 1200):
            passages.append(current)
            current = [item for item in current[-8:] if word[0] - item[0] <= 5]
        current.append(word)
    if current:
        passages.append(current)
    return [{"ordinal": index, "representation": "transcript",
             "text": " ".join(word[2] for word in passage), "title": title,
             "start_seconds": round(passage[0][0], 2),
             "end_seconds": round(passage[-1][1], 2)}
            for index, passage in enumerate(passages)]


def extract_audio(file, *, progress=None):
    path = Path(file.path)
    duration, codec, sample_rate, channels = _probe(path)
    model = _model()  # Fail before producing partial coverage if the backend is unavailable.
    derivative = Path(settings.MEDIA_VAULT) / "derivatives" / file.id / str(file.current_version_id)
    derivative.mkdir(parents=True, exist_ok=True)
    chunks = []
    failures = []
    start = 0.0
    total = math.ceil(duration / CHUNK_SECONDS)
    if progress:
        progress(0, total)
    while start < duration:
        if progress:
            progress(len(chunks) + len(failures), total)
        end = min(duration, start + CHUNK_SECONDS)
        input_start = max(0, start - OVERLAP_SECONDS if start else 0)
        wav = derivative / f"transcribe-{len(chunks) + len(failures)}.wav"
        try:
            _run(["ffmpeg", "-v", "error", "-y", "-ss", str(input_start), "-i", str(path),
                  "-t", str(end - input_start), "-map", "0:a:0", "-ac", "1", "-ar", "16000",
                  "-c:a", "pcm_s16le", str(wav)])
            if not wav.is_file() or not wav.stat().st_size:
                raise ExtractionError("transcription_failed", "Audio conversion produced no data")
            chunks.append((input_start, _transcribe_chunk(model, wav, input_start, duration)))
        except ExtractionError as error:
            failures.append({"start_seconds": start, "end_seconds": end, "error": str(error)[:200]})
        finally:
            wav.unlink(missing_ok=True)
        start = end
        if progress:
            progress(len(chunks) + len(failures), total)
    words = _merge_words(chunks)
    parts = _passages(words, file.original_name)
    if not parts:
        category = "transcription_failed" if failures else "no_speech"
        raise ExtractionError(category, "No searchable speech was transcribed")
    return ExtractedSegments(parts, coverage={"duration_seconds": round(duration, 2),
        "audio_codec": codec, "sample_rate": sample_rate, "channels": channels,
        "transcription_backend": "faster-whisper",
        "transcription_config": transcription_config(),
        "transcribed_chunks": len(chunks), "failed_intervals": failures,
        "partial": bool(failures)})
