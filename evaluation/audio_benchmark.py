"""Benchmark local transcription on reviewed recordings without publishing their contents."""
import argparse
import json
import os
import re
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


def tokens(value):
    return re.findall(r"\w+", value.casefold())


def word_error_rate(reference, hypothesis):
    expected, actual = tokens(reference), tokens(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for index, word in enumerate(expected, 1):
        current = [index]
        for column, candidate in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[column] + 1,
                               previous[column - 1] + (word != candidate)))
        previous = current
    return round(previous[-1] / len(expected), 4)


def joined_transcript(parts):
    joined = []
    for part in parts:
        incoming = tokens(part["text"])
        overlap = next((size for size in range(min(12, len(joined), len(incoming)), 0, -1)
                        if joined[-size:] == incoming[:size]), 0)
        joined.extend(incoming[overlap:])
    return " ".join(joined)


def run(manifest, *, model):
    os.environ["SEMANTIC_GALLERY_WHISPER_MODEL"] = model
    from django.conf import settings
    from media_engine.audio_transcription import extract_audio, transcription_config, _probe
    from media_engine.desktop_extraction import ExtractionError

    rows = []
    with tempfile.TemporaryDirectory() as vault:
        settings.MEDIA_VAULT = Path(vault)
        for index, case in enumerate(manifest):
            path = Path(case["path"]).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"Recording does not exist: {path}")
            file = SimpleNamespace(path=str(path), source_type="AUDIO", original_name=path.name,
                                   id=f"benchmark-{index}", current_version_id=1)
            started = time.perf_counter()
            duration = _probe(path)[0]
            try:
                parts = extract_audio(file)
            except ExtractionError as error:
                if error.category != "no_speech" or case["reference"].strip():
                    raise
                parts = []
            elapsed = time.perf_counter() - started
            transcript = joined_transcript(parts)
            phrase = tokens(case.get("expected_phrase", ""))
            expected_second = case.get("expected_second")
            matching = [part for part in parts if phrase and
                        all(token in tokens(part["text"]) for token in phrase)]
            rows.append({"id": case["id"], "category": case["category"],
                "wer": word_error_rate(case["reference"], transcript),
                "seconds": round(elapsed, 2),
                "realtime_factor": round(elapsed / duration, 3),
                "phrase_found": bool(matching) if phrase else None,
                "seek_error_seconds": (round(min(abs(part["start_seconds"] - expected_second)
                    for part in matching), 2) if matching and expected_second is not None else None),
                "partial": parts.coverage["partial"] if parts else False,
                "failed_intervals": len(parts.coverage["failed_intervals"]) if parts else 0})
    return {"backend": "faster-whisper", "model": model,
        "config": transcription_config(), "provider_cost_usd": 0,
        "mean_wer": round(statistics.mean(row["wer"] for row in rows), 4),
        "mean_realtime_factor": round(statistics.mean(row["realtime_factor"] for row in rows), 3),
        "cases": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--models", nargs="+", default=["base", "small"])
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    required = {"technical_vocabulary", "accent", "silence", "background_noise", "overlap"}
    if not isinstance(cases, list) or not required.issubset({case.get("category") for case in cases}):
        parser.error(f"Manifest needs reviewed cases for: {', '.join(sorted(required))}")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    import django
    django.setup()
    print(json.dumps([run(cases, model=model) for model in args.models], indent=2))


if __name__ == "__main__":
    main()
