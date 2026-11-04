import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from .ranking_policy import (DEFAULT_POLICY, accept_features, features_from_entry,
                             load_policy, score_features)


class RankingPolicyTests(SimpleTestCase):
    def entry(self):
        return {"channels": {
            "keyword": {"rank": 2, "passage_terms": 2, "heading_terms": 0,
                        "title_terms": 0, "query_terms": 2},
            "semantic:visual": {"rank": 3, "raw_score": 0.7},
            "semantic:ocr_text": {"rank": 4, "raw_score": 0.68}}}

    def test_passage_coverage_and_independent_support(self):
        features = features_from_entry(self.entry(), filename="diagram.jpg", query="AWS VPC")
        self.assertEqual(features["passage_coverage"], 1)
        self.assertTrue(features["independent_support"])
        boosted = deepcopy(DEFAULT_POLICY)
        boosted["weights"]["independent_support"] = 1
        self.assertGreater(score_features(features, boosted), score_features(features, DEFAULT_POLICY))
        self.assertTrue(accept_features(features, DEFAULT_POLICY))

    def test_constraints_and_semantic_floor_reject(self):
        entry = {"channels": {"semantic:visual": {"rank": 1, "raw_score": 0.2}}}
        features = features_from_entry(entry, filename="unrelated.jpg", query="burger")
        self.assertFalse(accept_features(features, DEFAULT_POLICY))
        features["constraints_ok"] = False
        self.assertFalse(accept_features(features, DEFAULT_POLICY))

    def test_policy_file_is_validated_and_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            policy = deepcopy(DEFAULT_POLICY)
            policy["semantic_floor"] = 0.72
            path.write_text(json.dumps(policy), encoding="utf-8")
            with patch.dict("os.environ", {"SEMANTIC_GALLERY_RANK_POLICY_FILE": str(path)}):
                self.assertEqual(load_policy()["semantic_floor"], 0.72)
            policy["weights"]["semantic"] = -1
            path.write_text(json.dumps(policy), encoding="utf-8")
            with patch.dict("os.environ", {"SEMANTIC_GALLERY_RANK_POLICY_FILE": str(path)}):
                with self.assertRaises(ValueError):
                    load_policy()
