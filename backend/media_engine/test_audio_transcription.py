import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .audio_transcription import _merge_words, _passages, extract_audio
from .auth_service import create_token
from .catalog_store import SQLiteCatalog, SQLiteJobScheduler
from .catalog_worker import CatalogWorker
from .desktop_extraction import ExtractionError
from .local_retrieval import SQLiteFullText
from .models import CatalogFile, CatalogJob, CatalogSegment


class AudioTranscriptionTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = override_settings(MEDIA_VAULT=self.root / "vault")
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.audio = self.root / "long_interview.wav"
        self.audio.write_bytes(b"fixture audio bytes")
        self.file = SimpleNamespace(path=str(self.audio), source_type="AUDIO",
            original_name=self.audio.name, id="audio", current_version_id=1)

    def _ffmpeg(self, command):
        if command[0] == "ffprobe":
            return json.dumps({"format": {"duration": "185"},
                "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}]}).encode()
        Path(command[-1]).write_bytes(b"wav")
        return b""

    def test_long_recording_phrase_has_absolute_playback_time(self):
        def transcribe(_model, _wav, offset, _duration):
            if offset < 100:
                return []
            return [(offset + 4, offset + 4.5, "AWS"),
                    (offset + 4.5, offset + 5, "VPC"),
                    (offset + 5, offset + 5.5, "private subnet")]
        with patch("media_engine.audio_transcription._run", side_effect=self._ffmpeg), \
             patch("media_engine.audio_transcription._model", return_value=object()), \
             patch("media_engine.audio_transcription._transcribe_chunk", side_effect=transcribe):
            parts = extract_audio(self.file)
        self.assertEqual(parts.coverage["duration_seconds"], 185)
        self.assertEqual(parts.coverage["audio_codec"], "pcm_s16le")
        self.assertFalse(parts.coverage["partial"])
        self.assertTrue(any("AWS VPC private subnet" in part["text"] and
                            part["start_seconds"] >= 120 for part in parts))
        self.assertTrue(any("AWS VPC private subnet" in part["text"] and
                            abs(part["start_seconds"] - 122) < 18 for part in parts))
        self.assertFalse(list((self.root / "vault").rglob("*.wav")))

    def test_boundary_duplicate_removed_but_repeated_speech_kept(self):
        chunks = [(0, [(58.2, 58.5, "hello"), (58.7, 59, "hello"),
                       (60, 60.3, "rocket")]),
                  (58, [(58.25, 58.55, "hello"), (60.04, 60.34, "rocket"),
                        (61, 61.3, "launch")])]
        merged = _merge_words(chunks)
        self.assertEqual([word[2] for word in merged], ["hello", "hello", "rocket", "launch"])

    def test_passage_boundary_keeps_crossing_phrase(self):
        words = [(float(i), float(i) + .3, str(i)) for i in range(100)]
        passages = _passages(words, "recording.wav")
        self.assertGreater(len(passages), 1)
        self.assertTrue(any("16 17 18 19" in part["text"] for part in passages))
        self.assertTrue(all(part["end_seconds"] > part["start_seconds"] for part in passages))

    def test_partial_chunk_reports_failed_interval(self):
        def transcribe(_model, _wav, offset, _duration):
            if offset > 100:
                raise ExtractionError("transcription_failed", "decode failed")
            return [(offset + 3, offset + 4, "technical vocabulary")]
        with patch("media_engine.audio_transcription._run", side_effect=self._ffmpeg), \
             patch("media_engine.audio_transcription._model", return_value=object()), \
             patch("media_engine.audio_transcription._transcribe_chunk", side_effect=transcribe):
            parts = extract_audio(self.file)
        self.assertTrue(parts.coverage["partial"])
        self.assertEqual(len(parts.coverage["failed_intervals"]), 2)
        self.assertTrue(parts)

    def test_missing_backend_is_not_silently_indexed(self):
        with patch("media_engine.audio_transcription._run", side_effect=self._ffmpeg), \
             patch("media_engine.audio_transcription._model",
                   side_effect=ExtractionError("transcriber_unavailable", "no model")):
            with self.assertRaises(ExtractionError) as error:
                extract_audio(self.file)
        self.assertEqual(error.exception.category, "transcriber_unavailable")

    def test_worker_indexes_transcript_and_publishes_partial_coverage(self):
        store = SQLiteCatalog()
        store.put_source(source_id="source", user_id="alice", kind="managed_import", root_path=self.root)
        file = store.put_file(file_id="audio", user_id="alice", source_id="source",
            path=self.audio, source_type="AUDIO", mime_type="audio/wav", fingerprint="")
        job_id = SQLiteJobScheduler().enqueue(file.current_version_id, "discover")
        def transcribe(_model, _wav, offset, _duration):
            if offset > 100:
                raise ExtractionError("transcription_failed", "noisy interval")
            return [(offset + 4, offset + 5, "AWS VPC private subnet")]
        with patch("media_engine.audio_transcription._run", side_effect=self._ffmpeg), \
             patch("media_engine.audio_transcription._model", return_value=object()), \
             patch("media_engine.audio_transcription._transcribe_chunk", side_effect=transcribe), \
             patch("media_engine.desktop_embedding.embed_segment", return_value=[1] + [0] * 3071):
            worker = CatalogWorker()
            for _ in range(6):
                self.assertTrue(worker.run_one())
        file = CatalogFile.objects.get(pk="audio")
        self.assertEqual(file.processing_status, "PARTIAL")
        self.assertEqual(file.published_version_id, file.current_version_id)
        self.assertEqual(CatalogJob.objects.get(pk=job_id).state, "done")
        segment = CatalogSegment.objects.filter(file_version=file.current_version,
                                                representation="transcript").first()
        self.assertIsNotNone(segment)
        self.assertEqual(segment.start_seconds, 4)
        self.assertEqual(SQLiteFullText().search_evidence("AWS VPC", user_id="alice")[0]["file_id"], "audio")

    def test_authenticated_audio_stream_supports_deep_byte_ranges(self):
        store = SQLiteCatalog()
        store.put_source(source_id="linked", user_id="alice", kind="linked_folder", root_path=self.root)
        store.put_file(file_id="audio", user_id="alice", source_id="linked", path=self.audio,
                       source_type="AUDIO", mime_type="audio/wav", fingerprint="")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {create_token('alice')}")
        link = client.get("/api/media/file/audio/playback-link/")
        self.assertEqual(link.status_code, 200)
        from urllib.parse import urlsplit
        location = urlsplit(link.data["url"])
        response = client.get(location.path + "?" + location.query, HTTP_RANGE="bytes=8-12")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], "bytes 8-12/19")
        self.assertEqual(b"".join(response.streaming_content), self.audio.read_bytes()[8:13])
        forbidden = client.get(location.path.replace("/audio/", "/other/") + "?" + location.query)
        self.assertEqual(forbidden.status_code, 403)
