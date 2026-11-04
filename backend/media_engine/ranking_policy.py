"""Versioned, replayable file-level ranking and rejection policy.

This module has no Django dependency so the same decisions can be replayed by the
development tuner and held-out validator without making provider calls.
"""
import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path

POLICY_VERSION = 1
DEFAULT_POLICY = {
    "schema_version": POLICY_VERSION,
    "index_generation": None,
    "index_config": None,
    "rrf_k": 30,
    "weights": {"keyword": 1.0, "filename": 2.0, "semantic": 1.0,
                "exact_filename": 0.0, "exact_title": 0.0,
                "passage_coverage": 0.0, "independent_support": 0.0},
    "semantic_floor": 0.5,
    "passage_coverage_floor": 1.0,
    "minimum_rank_score": 0.0,
    "filename_requires_exact": False,
    "allow_title_only": True,
    "allow_heading_only": True,
}


def validate_policy(policy):
    if not isinstance(policy, dict) or policy.get("schema_version") != POLICY_VERSION:
        raise ValueError("Unsupported ranking policy version")
    if not isinstance(policy.get("rrf_k"), int) or not 1 <= policy["rrf_k"] <= 200:
        raise ValueError("rrf_k must be an integer from 1 to 200")
    if policy.get("index_generation") is not None and (
            isinstance(policy["index_generation"], bool) or
            not isinstance(policy["index_generation"], int) or policy["index_generation"] < 1):
        raise ValueError("Invalid ranking policy index generation")
    if policy.get("index_config") is not None and not isinstance(policy["index_config"], str):
        raise ValueError("Invalid ranking policy index configuration")
    weights = policy.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(DEFAULT_POLICY["weights"]):
        raise ValueError("Ranking policy weights are incomplete")
    for key, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 10:
            raise ValueError(f"Invalid ranking weight: {key}")
    for key, low, high in (("semantic_floor", -1, 1), ("passage_coverage_floor", 0, 1),
                           ("minimum_rank_score", 0, 1)):
        value = policy.get(key)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid ranking threshold: {key}")
    for key in ("filename_requires_exact", "allow_title_only", "allow_heading_only"):
        if not isinstance(policy.get(key), bool):
            raise ValueError(f"Invalid ranking rule: {key}")
    return policy


@lru_cache(maxsize=8)
def _read_policy(path, modified_ns):
    return validate_policy(json.loads(Path(path).read_text(encoding="utf-8")))


def load_policy():
    path = os.getenv("SEMANTIC_GALLERY_RANK_POLICY_FILE")
    if not path:
        return DEFAULT_POLICY
    file = Path(path)
    return _read_policy(str(file.resolve()), file.stat().st_mtime_ns)


def _tokens(text):
    return re.findall(r"\w+", (text or "").casefold())


def features_from_entry(entry, *, filename="", query="", constraints_ok=True):
    channels = entry["channels"]
    keyword = channels.get("keyword") or {}
    query_terms = keyword.get("query_terms") or len(set(_tokens(query)))
    terms = set(_tokens(query))
    name_tokens = set(_tokens(Path(filename).stem)) if filename else set()
    semantic = {channel: {"rank": details["rank"], "cosine": details.get("raw_score")}
                for channel, details in channels.items() if channel.startswith("semantic:")}
    visual = "semantic:visual" in semantic
    textual = (any(channel in semantic for channel in
                   ("semantic:text", "semantic:ocr_text", "semantic:transcript")) or
               (keyword.get("passage_terms") or 0) > 0)
    return {"channel_ranks": {channel: details["rank"] for channel, details in channels.items()},
            "semantic": semantic,
            "exact_filename": bool(terms and terms == name_tokens),
            "exact_title": bool(query_terms and (keyword.get("title_terms") or 0) >= query_terms),
            "passage_coverage": min(1.0, (keyword.get("passage_terms") or 0) / query_terms) if query_terms else 0.0,
            "heading_coverage": min(1.0, (keyword.get("heading_terms") or 0) / query_terms) if query_terms else 0.0,
            "independent_support": bool(visual and textual),
            "constraints_ok": bool(constraints_ok)}


def score_features(features, policy):
    weights = policy["weights"]
    k = policy["rrf_k"]
    score = 0.0
    for channel, rank in features["channel_ranks"].items():
        weight = weights["semantic"] if channel.startswith("semantic:") else weights.get(channel, 0)
        score += weight / (k + rank)
    for name in ("exact_filename", "exact_title", "independent_support"):
        if features[name]:
            score += weights[name] / (k + 1)
    score += weights["passage_coverage"] * features["passage_coverage"] / (k + 1)
    return score


def accept_features(features, policy):
    if not features["constraints_ok"] or score_features(features, policy) < policy["minimum_rank_score"]:
        return False
    if "filename" in features["channel_ranks"] and (
            not policy["filename_requires_exact"] or features["exact_filename"]):
        return True
    if features["exact_title"] and policy["allow_title_only"]:
        return True
    if features["heading_coverage"] == 1.0 and policy["allow_heading_only"]:
        return True
    if features["passage_coverage"] >= policy["passage_coverage_floor"] and features["passage_coverage"] > 0:
        return True
    return any(details["cosine"] is not None and details["cosine"] >= policy["semantic_floor"]
               for details in features["semantic"].values())
