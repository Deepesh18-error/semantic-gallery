"""Extract bounded, repeatable segments from local files without MongoDB."""
import subprocess
import os
from pathlib import Path

from django.conf import settings


class ExtractionError(RuntimeError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def _text_segments(text, *, page_number=None, max_chars=4000):
    words = text.split()
    parts = []
    batch = []
    length = 0
    for word in words:
        if batch and length + len(word) + 1 > max_chars:
            parts.append({"ordinal": len(parts), "representation": "text",
                          "text": " ".join(batch), "page_number": page_number})
            batch = []
            length = 0
        batch.append(word)
        length += len(word) + 1
    if batch:
        parts.append({"ordinal": len(parts), "representation": "text",
                      "text": " ".join(batch), "page_number": page_number})
    return parts


def _text_budget():
    from .models import ProviderQuota
    project_id = os.getenv("GEMINI_PROJECT_ID")
    model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2")
    quota = ProviderQuota.objects.filter(project_id=project_id, model=model, enabled=True).first() if project_id else None
    return min(4000, max(400, int(quota.input_tpm * 0.5 * 4))) if quota else 4000


def _run(command):
    try:
        result = subprocess.run(command, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExtractionError("unsupported_format", str(error)) from error
    if result.returncode:
        raise ExtractionError("extraction_failed", result.stderr.decode("utf-8", "replace")[-500:])
    return result.stdout


def extract(file):
    path = Path(file.path)
    ext = path.suffix.lower()
    if not path.is_file():
        raise ExtractionError("missing_file", "Source file is missing")
    if file.source_type == "TEXT" or ext in (".txt", ".md"):
        return _text_segments(path.read_text(encoding="utf-8", errors="replace"), max_chars=_text_budget())
    if ext == ".pdf":
        try:
            import fitz
            result = []
            budget = _text_budget()
            with fitz.open(path) as document:
                for page_no, page in enumerate(document, 1):
                    for part in _text_segments(page.get_text(), page_number=page_no, max_chars=budget):
                        part["ordinal"] = len(result)
                        result.append(part)
            if not result:
                raise ExtractionError("extraction_failed", "PDF contains no extractable text")
            return result
        except ExtractionError:
            raise
        except Exception as error:
            raise ExtractionError("extraction_failed", str(error)) from error
    if ext == ".docx":
        try:
            from docx import Document
            document = Document(path)
            return _text_segments(" ".join(p.text for p in document.paragraphs if p.text.strip()),
                                  max_chars=_text_budget())
        except Exception as error:
            raise ExtractionError("extraction_failed", str(error)) from error
    if file.source_type == "IMAGE":
        try:
            from PIL import Image
            with Image.open(path) as image:
                image.load()
                image = image.convert("RGB")
                image.thumbnail((1024, 1024))
                derivative = Path(settings.MEDIA_VAULT) / "derivatives" / file.id / str(file.current_version_id)
                derivative.mkdir(parents=True, exist_ok=True)
                visual = derivative / "visual.jpg"
                image.save(visual, "JPEG", quality=85)
                segments = [{"ordinal": 0, "representation": "visual", "derivative_path": str(visual)}]
                try:
                    import pytesseract
                    words = pytesseract.image_to_string(image).split()
                    if len(words) >= 15:
                        for part in _text_segments(" ".join(words), max_chars=_text_budget()):
                            part["representation"] = "ocr_text"
                            segments.append(part)
                except Exception:
                    pass  # OCR is optional enrichment; visual indexing still proceeds.
                return segments
        except Exception as error:
            raise ExtractionError("extraction_failed", str(error)) from error
    if file.source_type in ("VIDEO", "AUDIO"):
        raw = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
        try:
            duration = float(raw.strip())
        except ValueError as error:
            raise ExtractionError("extraction_failed", "Cannot read media duration") from error
        if duration <= 0:
            raise ExtractionError("extraction_failed", "Media has no duration")
        derivative = Path(settings.MEDIA_VAULT) / "derivatives" / file.id / str(file.current_version_id)
        derivative.mkdir(parents=True, exist_ok=True)
        segments = []
        target_seconds = min(60, max(5, int((8 * 1024 * 1024) / max(1, path.stat().st_size / duration))))
        start = 0.0
        while start < duration:
            end = min(start + target_seconds, duration)
            ordinal = len(segments)
            output = derivative / (f"segment-{ordinal}.mp4" if file.source_type == "VIDEO" else f"segment-{ordinal}.mp3")
            command = ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(path),
                       "-t", str(end - start)]
            command += (["-c:v", "libx264", "-b:v", "600k", "-c:a", "aac", "-b:a", "96k"]
                        if file.source_type == "VIDEO" else ["-vn", "-c:a", "libmp3lame", "-b:a", "64k"])
            _run(command + [str(output)])
            if not output.is_file() or not output.stat().st_size:
                raise ExtractionError("extraction_failed", "Empty media segment")
            if output.stat().st_size > 15 * 1024 * 1024:
                raise ExtractionError("extraction_failed", "Media segment exceeded inline provider payload budget")
            segments.append({"ordinal": ordinal, "representation": file.source_type.lower(),
                             "derivative_path": str(output), "start_seconds": start,
                             "end_seconds": end})
            start = end
        return segments
    raise ExtractionError("unsupported_format", f"Unsupported source type: {file.source_type}")
