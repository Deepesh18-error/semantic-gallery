"""Small live contract probe; one Gemini call per independent semantic object."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from media_engine.desktop_embedding import (DIMENSIONS, INDEX_GENERATION, MODEL,
                                            MODEL_CONFIG, embed_query, embed_segment)


class Command(BaseCommand):
    help = "Probe query, passage, and image embeddings with the configured provider quota."

    def add_arguments(self, parser):
        parser.add_argument("--image", type=Path, help="A representative local image")
        parser.add_argument("--user-id", default="embedding-probe")

    def handle(self, *args, **options):
        if os.getenv("SEMANTIC_GALLERY_STORAGE", "legacy").lower() != "desktop":
            raise CommandError("Set SEMANTIC_GALLERY_STORAGE=desktop")
        image_path = options["image"]
        if image_path and not image_path.is_file():
            raise CommandError("Image path does not exist")
        if image_path and image_path.suffix.lower() not in (".jpg", ".jpeg"):
            raise CommandError("The visual probe expects a JPEG image")
        query = embed_query("photo of a burger", options["user_id"])
        file = SimpleNamespace(original_name="food-notes.txt")
        version = SimpleNamespace(file=file)
        passage = SimpleNamespace(representation="text", title="food notes", heading="Lunch",
                                  text="A person is eating a burger at lunch.", file_version=version)
        text = embed_segment(passage, options["user_id"])
        values = {"query": query, "passage": text}
        if image_path:
            image = SimpleNamespace(representation="visual", derivative_path=str(image_path),
                                    start_seconds=None, end_seconds=None)
            values["image"] = embed_segment(image, options["user_id"])
        self.stdout.write(json.dumps({"model": MODEL, "model_config": MODEL_CONFIG,
            "generation": INDEX_GENERATION, "dimensions": DIMENSIONS,
            "objects": {name: len(vector) for name, vector in values.items()},
            "query_passage_cosine": sum(a * b for a, b in zip(query, text))}))
