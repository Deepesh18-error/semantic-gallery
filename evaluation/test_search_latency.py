import unittest

from search_latency import verify_sample


class LatencyScenarioTests(unittest.TestCase):
    def test_missing_index_cannot_pass_as_cached_semantic(self):
        with self.assertRaises(RuntimeError):
            verify_sample("semantic", "cached", {"semantic_status": "not_indexed",
                                                  "metrics": {"result_cache_hit": True}})

    def test_uncached_and_indexing_labels_require_observed_work(self):
        with self.assertRaises(RuntimeError):
            verify_sample("semantic", "uncached", {"semantic_status": "complete", "metrics": {}})
        with self.assertRaises(RuntimeError):
            verify_sample("local", "indexing", {"metrics": {"indexing_active": False}})
        verify_sample("local", "indexing", {"metrics": {"indexing_active": True}})
