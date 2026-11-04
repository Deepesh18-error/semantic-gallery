"""Extract bounded, repeatable segments from local files without MongoDB."""
import subprocess
import os
import re
import codecs
from pathlib import Path

from django.conf import settings


class ExtractionError(RuntimeError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class ExtractedSegments(list):
    def __init__(self, values=(), *, coverage=None):
        super().__init__(values)
        self.coverage = coverage or {}


def _text_segments(text, *, page_number=None, max_chars=2800, overlap_chars=160, title=""):
    # Keep original offsets and split first at paragraph boundaries, then words.
    words = list(re.finditer(r"\S+", text))
    parts = []
    start = 0
    while start < len(words):
        end = start
        while end < len(words) and words[end].end() - words[start].start() <= max_chars:
            end += 1
        end = max(start + 1, end)
        if end < len(words):
            boundaries = [i for i in range(start + 1, end) if "\n\n" in text[words[i-1].end():words[i].start()]]
            if boundaries and words[boundaries[-1]].start() - words[start].start() >= max_chars // 2:
                end = boundaries[-1]
        begin_offset, end_offset = words[start].start(), words[end-1].end()
        passage = text[begin_offset:end_offset].strip()
        heading = next((line.strip() for line in passage.splitlines() if line.strip()), "")[:200]
        parts.append({"ordinal": len(parts), "representation": "text",
                      "text": passage, "page_number": page_number,
                      "source_start": begin_offset, "source_end": end_offset,
                      "heading": heading, "title": title})
        if end >= len(words):
            break
        next_start = end
        while next_start > start + 1 and words[end-1].end() - words[next_start-1].start() < overlap_chars:
            next_start -= 1
        start = min(end, next_start)
    return parts


def _text_budget():
    from .models import ProviderQuota
    project_id = os.getenv("GEMINI_PROJECT_ID")
    model = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2")
    quota = ProviderQuota.objects.filter(project_id=project_id, model=model, enabled=True).first() if project_id else None
    configured = int(os.getenv("SEMANTIC_GALLERY_PASSAGE_TOKENS", "600"))
    return min(3200, max(400, min(configured, 800) * 4), int(quota.input_tpm * 0.5 * 4)) if quota else min(3200, max(400, configured * 4))


def _run(command):
    try:
        result = subprocess.run(command, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExtractionError("unsupported_format", str(error)) from error
    if result.returncode:
        raise ExtractionError("extraction_failed", result.stderr.decode("utf-8", "replace")[-500:])
    return result.stdout


def _read_text(path):
    raw = path.read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError as error:
            raise ExtractionError("extraction_failed", "Text encoding could not be decoded") from error


def extract(file):
    path = Path(file.path)
    ext = path.suffix.lower()
    if not path.is_file():
        raise ExtractionError("missing_file", "Source file is missing")
    if file.source_type == "TEXT" or ext in (".txt", ".md"):
        return _text_segments(_read_text(path), max_chars=_text_budget(), title=file.original_name)
    if ext == ".pdf":
        try:
            import fitz
            result = []
            failed_pages = []
            ocr_pages = []
            budget = _text_budget()
            with fitz.open(path) as document:
                total_pages = len(document)
                for page_no, page in enumerate(document, 1):
                    text = page.get_text(sort=True)
                    method = "digital"
                    if len(text.strip()) < 40:
                        try:
                            import pytesseract
                            from PIL import Image
                            import io
                            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                            ocr_text = pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png"))))
                            if len(ocr_text.strip()) > len(text.strip()):
                                text = ocr_text
                                method = "ocr"
                                ocr_pages.append(page_no)
                        except Exception:
                            if not text.strip():
                                failed_pages.append(page_no)
                                continue
                    if not text.strip():
                        failed_pages.append(page_no)
                        continue
                    for part in _text_segments(text, page_number=page_no, max_chars=budget,
                                               title=file.original_name):
                        part["representation"] = "ocr_text" if method == "ocr" else "text"
                        part["ordinal"] = len(result)
                        result.append(part)
            if not result:
                raise ExtractionError("extraction_failed", "PDF contains no extractable text")
            return ExtractedSegments(result, coverage={"failed_pages": failed_pages,
                "ocr_pages": ocr_pages, "total_pages": total_pages})
        except ExtractionError:
            raise
        except Exception as error:
            raise ExtractionError("extraction_failed", str(error)) from error
    if ext == ".docx":
        try:
            from docx import Document
            document = Document(path)
            blocks = [p.text for p in document.paragraphs if p.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    blocks.append(" | ".join(cell.text.strip() for cell in row.cells))
            return _text_segments("\n\n".join(blocks), max_chars=_text_budget(), title=file.original_name)
        except Exception as error:
            raise ExtractionError("extraction_failed", str(error)) from error
    if file.source_type == "IMAGE":
        try:
            from PIL import Image, ImageOps
            with Image.open(path) as image:
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGB")
                derivative = Path(settings.MEDIA_VAULT) / "derivatives" / file.id / str(file.current_version_id)
                derivative.mkdir(parents=True, exist_ok=True)
                thumbnail = image.copy()
                thumbnail.thumbnail((320, 320))
                thumbnail.save(derivative / "thumbnail.jpg", "JPEG", quality=78)
                file.thumbnail_path = str(derivative / "thumbnail.jpg")
                if hasattr(file, "_meta"):
                    from .models import CatalogFileVersion
                    CatalogFileVersion.objects.filter(pk=file.current_version_id).update(thumbnail_path=file.thumbnail_path)
                    if not file.published_version_id:
                        file.save(update_fields=["thumbnail_path"])
                else:
                    file.save(update_fields=["thumbnail_path"])
                visual_image = image.copy()
                visual_image.thumbnail((1024, 1024))
                visual = derivative / "visual.jpg"
                visual_image.save(visual, "JPEG", quality=85)
                segments = [{"ordinal": 0, "representation": "visual", "derivative_path": str(visual)}]
                try:
                    import pytesseract
                    ocr_image = image.copy()
                    ocr_image.thumbnail((2400, 2400))
                    data = pytesseract.image_to_data(ocr_image, output_type=pytesseract.Output.DICT)
                    tokens = [(word.strip(), float(conf)) for word, conf in zip(data["text"], data["conf"])
                              if word.strip() and float(conf) >= 35]
                    if tokens:
                        confidence = sum(conf for _, conf in tokens) / len(tokens)
                        for part in _text_segments(" ".join(word for word, _ in tokens), max_chars=_text_budget(),
                                                   title=file.original_name):
                            part["representation"] = "ocr_text"
                            part["ordinal"] = len(segments)
                            part["ocr_confidence"] = confidence
                            segments.append(part)
                except Exception:
                    pass  # OCR is optional enrichment; visual indexing still proceeds.
                return segments
        except Exception as error:
            raise ExtractionError("extraction_failed", str(error)) from error
    if file.source_type == "AUDIO":
        from .audio_transcription import extract_audio
        return extract_audio(file)
    if file.source_type == "VIDEO":
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
            output = derivative / f"segment-{ordinal}.mp4"
            command = ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(path),
                       "-t", str(end - start)]
            command += ["-c:v", "libx264", "-b:v", "600k", "-c:a", "aac", "-b:a", "96k"]
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
