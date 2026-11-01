import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from .desktop_extraction import _text_segments, extract


class ExtractionTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = override_settings(MEDIA_VAULT=self.root)
        self.vault.enable()
        self.addCleanup(self.vault.disable)

    def file(self, name, source_type):
        return SimpleNamespace(path=str(self.root / name), source_type=source_type,
            original_name=name, id="file", current_version_id=1,
            thumbnail_path="", save=lambda **kwargs: None)

    def test_long_pdf_retains_deep_passage_and_page(self):
        import fitz
        path = self.root / "vpc.pdf"
        with fitz.open() as doc:
            for index in range(12):
                page = doc.new_page()
                page.insert_text((72, 72), "AWS VPC private subnet route table" if index == 11
                                 else "Unrelated introduction material " * 3)
            doc.save(path)
        parts = extract(self.file("vpc.pdf", "DOCUMENT"))
        self.assertEqual(parts.coverage["total_pages"], 12)
        self.assertTrue(any("AWS VPC" in part["text"] and part["page_number"] == 12
                            for part in parts))
        self.assertTrue(all(part["source_end"] > part["source_start"] for part in parts))

    def test_short_image_ocr_is_kept_separate(self):
        from PIL import Image
        path = self.root / "diagram.jpg"
        Image.new("RGB", (1200, 800), "white").save(path)
        data = {"text": ["AWS", "VPC"], "conf": [95, 94]}
        with patch("pytesseract.image_to_data", return_value=data):
            parts = extract(self.file("diagram.jpg", "IMAGE"))
        self.assertEqual([part["representation"] for part in parts], ["visual", "ocr_text"])
        self.assertEqual(parts[1]["text"], "AWS VPC")
        self.assertTrue((self.root / "derivatives/file/1/thumbnail.jpg").exists())

    def test_scanned_pdf_page_is_searchable(self):
        import fitz
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (1400, 500), "white")
        draw = ImageDraw.Draw(image)
        draw.text((60, 60), "AWS VPC", fill="black", font=ImageFont.truetype("arial.ttf", 110))
        scan = self.root / "scan.png"
        image.save(scan)
        path = self.root / "scan.pdf"
        with fitz.open() as doc:
            page = doc.new_page(width=700, height=300)
            page.insert_image(page.rect, filename=str(scan))
            doc.save(path)
        parts = extract(self.file("scan.pdf", "DOCUMENT"))
        self.assertIn(1, parts.coverage["ocr_pages"])
        self.assertTrue(any("AWS VPC" in part["text"] and part["page_number"] == 1
                            for part in parts))

    def test_passages_keep_offsets(self):
        text = "Heading\n\n" + ("First paragraph with content. " * 80)
        parts = _text_segments(text, max_chars=400)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertEqual(text[part["source_start"]:part["source_end"]], part["text"])
