"""Tune on judged development queries; validate once on held-out queries."""
import argparse
import itertools
import json
import math
import statistics
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from media_engine.ranking_policy import (DEFAULT_POLICY, accept_features,
                                         score_features, validate_policy)

CATEGORIES = ("known_file", "broad_category", "exact_text", "visual_scene", "no_answer")
FALSE_POSITIVE_TARGET = 0.10


def _cases(report, split):
    if report.get("mode") != "live":
        raise ValueError("Use a live desktop evaluation report with candidate diagnostics")
    unique = {}
    for case in report.get("cases", []):
        if case.get("split") == split:
            if case.get("candidate_debug") is None:
                raise ValueError("Rerun evaluation with --debug-candidates")
            if case.get("candidate_debug_truncated"):
                raise ValueError(f"Candidate diagnostics were truncated for {case['id']}")
            unique.setdefault(case["id"], case)
    if not unique:
        raise ValueError(f"No {split} cases in report")
    return unique


def _labels(judgments, case_id):
    row = judgments.get("cases", {}).get(case_id)
    if not row:
        raise ValueError(f"Missing explicit judgments for {case_id}")
    category = row.get("category")
    if category not in CATEGORIES:
        raise ValueError(f"Invalid query category for {case_id}")
    positive = {name.casefold() for name in row.get("positive", [])}
    negative = {name.casefold() for name in row.get("negative", [])}
    if positive & negative:
        raise ValueError(f"Contradictory file judgments for {case_id}")
    if category == "no_answer":
        if positive or row.get("verified_no_answer") is not True:
            raise ValueError(f"No-answer query {case_id} needs collection-wide review")
    elif not positive:
        raise ValueError(f"Known-answer query {case_id} needs positive file labels")
    return category, positive


def _wilson_upper(positives, total, z=1.96):
    if not total:
        return None
    rate = positives / total
    denominator = 1 + z * z / total
    center = rate + z * z / (2 * total)
    spread = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    return min(1.0, (center + spread) / denominator)


def evaluate(report, judgments, policy, *, split, min_per_category=1):
    validate_policy(policy)
    cases = _cases(report, split)
    per_category = {name: [] for name in CATEGORIES}
    missing_candidates = []
    details = []
    for case_id, case in cases.items():
        category, positive = _labels(judgments, case_id)
        pool = case["candidate_debug"]
        pool_names = {row["original_filename"].casefold() for row in pool}
        if positive - pool_names:
            missing_candidates.append({"id": case_id, "files": sorted(positive - pool_names)})
        ranked = sorted((row for row in pool if accept_features(row["features"], policy)),
                        key=lambda row: (-score_features(row["features"], policy),
                                         row["original_filename"].casefold()))
        names = [row["original_filename"].casefold() for row in ranked]
        if category == "no_answer":
            metric = float(bool(names))
        else:
            recall = len(set(names[:5]) & positive) / len(positive)
            ranks = [index + 1 for index, name in enumerate(names) if name in positive]
            reciprocal_rank = 1 / min(ranks) if ranks else 0.0
            metric = (recall + reciprocal_rank) / 2
        per_category[category].append(metric)
        details.append({"id": case_id, "category": category, "top_files": names[:5],
                        "score": round(metric, 4)})
    deficits = {name: min_per_category - len(values) for name, values in per_category.items()
                if len(values) < min_per_category}
    answer_categories = [name for name in CATEGORIES if name != "no_answer"]
    quality = statistics.mean(statistics.mean(per_category[name]) for name in answer_categories
                              if per_category[name]) if any(per_category[name] for name in answer_categories) else 0.0
    empty = per_category["no_answer"]
    false_positives = sum(empty)
    return {"split": split, "case_counts": {name: len(values) for name, values in per_category.items()},
            "category_quality": {name: round(statistics.mean(per_category[name]), 4)
                                 for name in answer_categories if per_category[name]},
            "insufficient_categories": deficits, "missing_positive_candidates": missing_candidates,
            "macro_answer_quality": round(quality, 4),
            "no_answer_false_positives": int(false_positives),
            "no_answer_queries": len(empty),
            "no_answer_false_positive_rate": round(false_positives / len(empty), 4) if empty else None,
            "no_answer_wilson_upper_95": round(_wilson_upper(false_positives, len(empty)), 4) if empty else None,
            "details": details}


def _floors(report):
    scores = sorted({round(float(row["semantic_cosine"]), 4)
                     for case in _cases(report, "development").values()
                     for row in case["candidate_debug"] if row.get("semantic_cosine") is not None})
    if not scores:
        return [DEFAULT_POLICY["semantic_floor"]]
    selected = [scores[round(index * (len(scores) - 1) / 12)] for index in range(13)]
    return sorted(set([DEFAULT_POLICY["semantic_floor"]] + selected))


def tune(report, judgments, *, false_positive_target=FALSE_POSITIVE_TARGET, min_per_category=5):
    if not 0 <= false_positive_target < 1:
        raise ValueError("False-positive target must be between 0 and 1")
    first = evaluate(report, judgments, DEFAULT_POLICY, split="development",
                     min_per_category=min_per_category)
    if first["insufficient_categories"] or first["missing_positive_candidates"]:
        raise ValueError(json.dumps({"insufficient_categories": first["insufficient_categories"],
                                     "missing_positive_candidates": first["missing_positive_candidates"]}))
    best = None
    floors = _floors(report)
    def consider(policy):
        nonlocal best
        metrics = evaluate(report, judgments, policy, split="development", min_per_category=0)
        if metrics["no_answer_false_positives"] / metrics["no_answer_queries"] > false_positive_target:
            return
        key = (metrics["macro_answer_quality"],
               -metrics["no_answer_false_positive_rate"],
               -abs(policy["semantic_floor"] - DEFAULT_POLICY["semantic_floor"]))
        if best is None or key > best[0]:
            best = (key, deepcopy(policy), metrics)
    for keyword, filename, semantic, passage, support, floor in itertools.product(
            (0.75, 1.0, 1.25), (1.5, 2.0, 2.5), (0.75, 1.0, 1.25),
            (0.0, 0.5), (0.0, 0.5), floors):
        policy = deepcopy(DEFAULT_POLICY)
        policy["weights"].update(keyword=keyword, filename=filename, semantic=semantic,
                                 passage_coverage=passage, independent_support=support)
        policy["semantic_floor"] = floor
        consider(policy)
        policy.update(filename_requires_exact=True, allow_title_only=False,
                      allow_heading_only=False)
        consider(policy)
    if best is None:
        raise ValueError("No policy meets the development no-answer false-positive target")
    base = best[1]
    for exact_filename, exact_title, strict_filename, title_only, heading_only, passage_floor in itertools.product(
            (0.0, 0.5, 1.0), (0.0, 0.5, 1.0), (False, True), (False, True),
            (False, True), (0.75, 1.0)):
        policy = deepcopy(base)
        policy["weights"].update(exact_filename=exact_filename, exact_title=exact_title)
        policy.update(filename_requires_exact=strict_filename, allow_title_only=title_only,
                      allow_heading_only=heading_only, passage_coverage_floor=passage_floor)
        consider(policy)
    best[1]["index_generation"] = report.get("index_generation")
    best[1]["index_config"] = report.get("index_config")
    if best[1]["index_generation"] is None or best[1]["index_config"] is None:
        raise ValueError("Development report must identify its index generation and configuration")
    return {"policy": best[1], "development": best[2],
            "false_positive_target": false_positive_target,
            "held_out_untouched": True}


def validate_held_out(report, judgments, policy, *, baseline=None,
                      false_positive_target=FALSE_POSITIVE_TARGET, min_per_category=3, max_p95_ratio=1.2):
    if (policy.get("index_generation") != report.get("index_generation") or
            policy.get("index_config") != report.get("index_config")):
        raise ValueError("Policy and held-out report use different index configurations")
    metrics = evaluate(report, judgments, policy, split="held_out", min_per_category=min_per_category)
    baseline_quality = None
    category_regressions = []
    latency = {"baseline_p95_ms": None, "candidate_p95_ms": None, "passed": False}
    if baseline is not None:
        if (baseline.get("collection_id") != report.get("collection_id") or
                baseline.get("index_generation") != report.get("index_generation") or
                baseline.get("index_config") != report.get("index_config") or
                set(_cases(baseline, "held_out")) != set(_cases(report, "held_out"))):
            raise ValueError("Baseline and candidate must use the same collection, generation, and held-out cases")
        old_cases, new_cases = _cases(baseline, "held_out"), _cases(report, "held_out")
        for case_id in old_cases:
            old_pool = {row["original_filename"].casefold() for row in old_cases[case_id]["candidate_debug"]}
            new_pool = {row["original_filename"].casefold() for row in new_cases[case_id]["candidate_debug"]}
            if old_pool != new_pool:
                raise ValueError(f"Candidate pool changed between ranking runs for {case_id}")
            old_features = {row["original_filename"].casefold(): row["features"]
                            for row in old_cases[case_id]["candidate_debug"]}
            new_features = {row["original_filename"].casefold(): row["features"]
                            for row in new_cases[case_id]["candidate_debug"]}
            if old_features != new_features:
                raise ValueError(f"Candidate evidence changed between ranking runs for {case_id}")
        previous = evaluate(baseline, judgments, DEFAULT_POLICY, split="held_out", min_per_category=0)
        baseline_quality = previous["macro_answer_quality"]
        category_regressions = [name for name, value in metrics["category_quality"].items()
                                if value < previous["category_quality"].get(name, 0)]
        old_summary, new_summary = baseline.get("summary", {}), report.get("summary", {})
        old_p95, new_p95 = old_summary.get("round_trip_p95_ms"), new_summary.get("round_trip_p95_ms")
        latency = {"baseline_p95_ms": old_p95, "candidate_p95_ms": new_p95,
                   "passed": bool(old_p95 and new_p95 and
                                  old_summary.get("queries", 0) >= 20 and
                                  new_summary.get("queries", 0) >= 20 and
                                  new_p95 <= old_p95 * max_p95_ratio)}
    upper = metrics["no_answer_wilson_upper_95"]
    passed = (not metrics["insufficient_categories"] and
              not metrics["missing_positive_candidates"] and
              upper is not None and upper <= false_positive_target and
              baseline_quality is not None and metrics["macro_answer_quality"] > baseline_quality and
              not category_regressions and
              latency["passed"])
    return {"held_out": metrics, "baseline_quality": baseline_quality,
            "category_regressions": category_regressions,
            "false_positive_target": false_positive_target,
            "latency": latency,
            "completion_gate_passed": passed,
            "note": "The 95% Wilson upper bound needs many reviewed no-answer queries; a small sample is inconclusive."}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("tune", "validate"):
        command = sub.add_parser(name)
        command.add_argument("report", type=Path)
        command.add_argument("judgments", type=Path)
        command.add_argument("--false-positive-target", type=float, default=FALSE_POSITIVE_TARGET)
        command.add_argument("--min-per-category", type=int, default=5 if name == "tune" else 3)
        if name == "tune":
            command.add_argument("--policy-output", type=Path, required=True)
        else:
            command.add_argument("--policy", type=Path, required=True)
            command.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    judgments = json.loads(args.judgments.read_text(encoding="utf-8"))
    if args.command == "tune":
        result = tune(report, judgments, false_positive_target=args.false_positive_target,
                      min_per_category=args.min_per_category)
        args.policy_output.parent.mkdir(parents=True, exist_ok=True)
        args.policy_output.write_text(json.dumps(result["policy"], indent=2) + "\n", encoding="utf-8")
    else:
        policy = validate_policy(json.loads(args.policy.read_text(encoding="utf-8")))
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        result = validate_held_out(report, judgments, policy, baseline=baseline,
            false_positive_target=args.false_positive_target, min_per_category=args.min_per_category)
    print(json.dumps(result, indent=2))
    return 0 if args.command == "tune" or result["completion_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
