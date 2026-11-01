import unittest

from compare_generations import compare


class CompareGenerationTests(unittest.TestCase):
    def report(self, generation, recall):
        return {"mode": "live", "collection_id": "collection",
                "index_generation": generation,
                "summary": {"mean_recall_at_5": recall, "round_trip_p95_ms": 100,
                            "queries": 30},
                "cases": [{"id": "aws", "expected": ["vpc.pdf"],
                           "recall_at_5": recall, "type_violation": False}]}

    def test_accepts_nonregression(self):
        self.assertTrue(compare(self.report(1, 0.5), self.report(2, 1))["promotion_ready"])

    def test_rejects_regression_and_wrong_generation(self):
        self.assertFalse(compare(self.report(1, 1), self.report(2, 0))["promotion_ready"])
        with self.assertRaises(ValueError):
            compare(self.report(1, 1), self.report(1, 1))
