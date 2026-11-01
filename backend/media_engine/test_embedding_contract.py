import math
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from .desktop_embedding import (DIMENSIONS, INDEX_GENERATION, MODEL_CONFIG,
                                InvalidEmbedding, _normalize, embed_query, embed_segment)
from .models import CatalogEmbeddingCache


class EmbeddingContractTests(TestCase):
    def test_rejects_bad_dimensions_nonfinite_and_zero(self):
        for values in ([1, 0], [0] * DIMENSIONS,
                       [math.nan] + [0] * (DIMENSIONS - 1),
                       [math.inf] + [0] * (DIMENSIONS - 1)):
            with self.assertRaises(InvalidEmbedding):
                _normalize(values)

    def test_query_cache_avoids_second_provider_call(self):
        response = SimpleNamespace(embeddings=[SimpleNamespace(values=[3] + [0] * (DIMENSIONS - 1))])
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}), \
             patch("media_engine.desktop_embedding.genai.Client"), \
             patch("media_engine.desktop_embedding.call_with_quota", return_value=response) as provider:
            first = embed_query("burger", "alice")
            second = embed_query("burger", "alice")
        self.assertEqual(first, second)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(CatalogEmbeddingCache.objects.count(), 1)
        self.assertEqual(CatalogEmbeddingCache.objects.get().model_config, MODEL_CONFIG)
        self.assertEqual(INDEX_GENERATION, 2)

    def test_independent_segments_are_independent_requests(self):
        response = SimpleNamespace(embeddings=[SimpleNamespace(values=[1] + [0] * (DIMENSIONS - 1))])
        file = SimpleNamespace(original_name="notes.txt")
        version = SimpleNamespace(file=file)
        first = SimpleNamespace(representation="text", title="notes.txt", heading="",
                                text="burger", file_version=version)
        second = SimpleNamespace(representation="text", title="notes.txt", heading="",
                                 text="waterfall", file_version=version)
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}), \
             patch("media_engine.desktop_embedding.genai.Client"), \
             patch("media_engine.desktop_embedding.call_with_quota", return_value=response) as provider:
            embed_segment(first, "alice")
            embed_segment(second, "alice")
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(CatalogEmbeddingCache.objects.count(), 2)
