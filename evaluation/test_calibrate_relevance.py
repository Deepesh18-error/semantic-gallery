import unittest

from calibrate_relevance import calibrate


class CalibrationTests(unittest.TestCase):
    def test_separates_judged_positive_and_negative_candidates(self):
        report = {"mode": "live", "cases": [{"id": "food", "split": "development",
            "candidate_debug": [{"original_filename": f"good{i}.jpg", "semantic_cosine": 0.8 + i * 0.01,
                                 "filename_match": False} for i in range(3)] +
                               [{"original_filename": f"bad{i}.pdf", "semantic_cosine": 0.2 + i * 0.01,
                                 "filename_match": False} for i in range(3)]}]}
        labels = {"food": {"positive": [f"good{i}.jpg" for i in range(3)],
                           "negative": [f"bad{i}.pdf" for i in range(3)]}}
        result = calibrate(report, labels)
        self.assertEqual(result["balanced_accuracy"], 1)
        self.assertGreater(result["recommended_floor"], 0.22)
        self.assertLess(result["recommended_floor"], 0.8)
