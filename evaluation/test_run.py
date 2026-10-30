import unittest
import sys
from pathlib import Path
from run import offline, percentile, score_case, summarize
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from media_engine.search_metrics import SearchMetrics


class EvaluationTests(unittest.TestCase):
    def test_known_food_failure(self):
        self.assertEqual(offline()["known_relevant_rejected"], ["biryani.jpg", "cake.webp"])

    def test_metrics_and_ranking(self):
        case = {"id": "food", "query": "food items", "relevant_files": ["biryani.jpg", "cake.webp"]}
        response = {"results": [{"original_filename": "cake.webp"}, {"original_filename": "biryani.jpg"}],
                    "metrics": {"api_wall_ms": 100, "api_calls": {"embedding": 1}}}
        scored = score_case(case, response)
        self.assertEqual(scored["recall_at_5"], 1)
        self.assertEqual(scored["first_relevant_rank"], 1)
        self.assertEqual(summarize([scored])["api_calls"], {"embedding": 1})

    def test_type_violation(self):
        case = {"id": "pdf", "query": "pdf", "relevant_files": ["a.pdf"], "file_type": "DOCUMENT"}
        row = score_case(case, {"results": [{"original_filename": "a.pdf", "media_type": "IMAGE"}]})
        self.assertTrue(row["type_violation"])

    def test_percentile(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5], 95), 4.8)

    def test_request_metrics(self):
        metrics = SearchMetrics()
        metrics.call("embedding")
        metrics.call("embedding")
        self.assertEqual(metrics.snapshot()["api_calls"]["embedding"], 2)


if __name__ == "__main__":
    unittest.main()
