import unittest
from copy import deepcopy

from ranking_gate import evaluate, tune, validate_held_out


def candidate(name, score):
    return {"original_filename": name, "semantic_cosine": score,
            "features": {"channel_ranks": {"semantic:visual": 1},
                         "semantic": {"semantic:visual": {"rank": 1, "cosine": score}},
                         "exact_filename": False, "exact_title": False,
                         "passage_coverage": 0.0, "heading_coverage": 0.0,
                         "independent_support": False, "constraints_ok": True}}


def fixture(split, no_answer_count=1, include_positive=True):
    report = {"mode": "live", "collection_id": "gallery", "index_generation": 2,
              "index_config": "test-config",
              "summary": {"queries": 90, "round_trip_p95_ms": 100}, "cases": []}
    labels = {"cases": {}}
    for category in ("known_file", "broad_category", "exact_text", "visual_scene"):
        name = category + ".jpg"
        report["cases"].append({"id": category, "split": split,
            "candidate_debug": [candidate(name, 0.8 if include_positive else 0.2),
                                candidate("wrong.jpg", 0.2)]})
        labels["cases"][category] = {"category": category, "positive": [name]}
    for index in range(no_answer_count):
        case_id = f"none-{index}"
        report["cases"].append({"id": case_id, "split": split,
                                "candidate_debug": [candidate("wrong.jpg", 0.05)]})
        labels["cases"][case_id] = {"category": "no_answer", "positive": [],
                                    "verified_no_answer": True}
    return report, labels


class RankingGateTests(unittest.TestCase):
    def test_agreed_ten_percent_target_is_default(self):
        from ranking_gate import DEFAULT_POLICY, FALSE_POSITIVE_TARGET
        self.assertEqual(FALSE_POSITIVE_TARGET, 0.10)
        report, labels = fixture("held_out", no_answer_count=35, include_positive=False)
        baseline, _ = fixture("held_out", no_answer_count=35, include_positive=False)
        policy = deepcopy(DEFAULT_POLICY)
        policy.update(index_generation=2, index_config="test-config", semantic_floor=0.1)
        result = validate_held_out(report, labels, policy, baseline=baseline, min_per_category=1)
        self.assertEqual(result["false_positive_target"], 0.10)
        self.assertTrue(result["completion_gate_passed"])

    def test_tuner_rejects_tiny_default_dataset(self):
        report, labels = fixture("development")
        with self.assertRaises(ValueError):
            tune(report, labels)

    def test_tunes_development_without_held_out(self):
        report, labels = fixture("development")
        report["cases"].append({"id": "held-out-sentinel", "split": "held_out",
                                "candidate_debug": None})
        result = tune(report, labels, min_per_category=1, false_positive_target=0)
        self.assertTrue(result["held_out_untouched"])
        self.assertEqual(result["development"]["macro_answer_quality"], 1)
        self.assertEqual(result["development"]["no_answer_false_positives"], 0)

    def test_holdout_gate_requires_enough_verified_no_answer_queries(self):
        report, labels = fixture("held_out", include_positive=False)
        baseline, _ = fixture("held_out", include_positive=False)
        from ranking_gate import DEFAULT_POLICY
        policy = deepcopy(DEFAULT_POLICY)
        policy["index_generation"] = 2
        policy["index_config"] = "test-config"
        policy["semantic_floor"] = 0.1
        result = validate_held_out(report, labels, policy, baseline=baseline,
                                   min_per_category=1, false_positive_target=0.05)
        self.assertFalse(result["completion_gate_passed"])
        report, labels = fixture("held_out", no_answer_count=75, include_positive=False)
        baseline, _ = fixture("held_out", no_answer_count=75, include_positive=False)
        result = validate_held_out(report, labels, policy, baseline=baseline,
                                   min_per_category=1, false_positive_target=0.05)
        self.assertTrue(result["completion_gate_passed"])

    def test_holdout_rejects_latency_regression_and_evidence_drift(self):
        from ranking_gate import DEFAULT_POLICY
        report, labels = fixture("held_out", no_answer_count=75, include_positive=False)
        baseline, _ = fixture("held_out", no_answer_count=75, include_positive=False)
        policy = deepcopy(DEFAULT_POLICY)
        policy.update(index_generation=2, index_config="test-config", semantic_floor=0.1)
        report["summary"]["round_trip_p95_ms"] = 150
        result = validate_held_out(report, labels, policy, baseline=baseline,
                                   min_per_category=1, false_positive_target=0.05)
        self.assertFalse(result["completion_gate_passed"])
        report["cases"][0]["candidate_debug"][0]["features"]["semantic"]["semantic:visual"]["cosine"] = 0.3
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            validate_held_out(report, labels, policy, baseline=baseline,
                              min_per_category=1, false_positive_target=0.05)
