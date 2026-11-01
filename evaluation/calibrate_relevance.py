"""Propose a final semantic floor from explicitly judged development candidates."""
import argparse
import json
from pathlib import Path


def calibrate(report, judgments):
    if report.get("mode") != "live":
        raise ValueError("A live candidate-debug report is required")
    scored = []
    for case in report["cases"]:
        if case.get("split") != "development":
            continue
        labels = judgments.get(case["id"], {})
        positive = {name.casefold() for name in labels.get("positive", [])}
        negative = {name.casefold() for name in labels.get("negative", [])}
        if positive & negative:
            raise ValueError(f"Contradictory labels for {case['id']}")
        if case.get("candidate_debug") is None:
            raise ValueError("Run evaluation with --debug-candidates")
        for candidate in case["candidate_debug"]:
            score = candidate.get("semantic_cosine")
            if score is None or candidate.get("filename_match") or (
                candidate.get("keyword_coverage") is not None and
                candidate["keyword_coverage"] == candidate.get("query_terms")):
                continue
            name = candidate["original_filename"].casefold()
            if name in positive or name in negative:
                scored.append((float(score), name in positive))
    positives = sum(label for _, label in scored)
    negatives = len(scored) - positives
    if positives < 3 or negatives < 3:
        raise ValueError("At least three positive and three negative judged semantic candidates are required")
    values = sorted({score for score, _ in scored})
    thresholds = [0.0] + [(a + b) / 2 for a, b in zip(values, values[1:])] + [1.0]
    def measure(floor):
        tp = sum(score >= floor and label for score, label in scored)
        tn = sum(score < floor and not label for score, label in scored)
        return (tp / positives + tn / negatives) / 2, tp / positives, tn / negatives
    floor = max(thresholds, key=lambda value: (measure(value)[0], measure(value)[2], value))
    accuracy, recall, specificity = measure(floor)
    return {"recommended_floor": round(floor, 4), "balanced_accuracy": round(accuracy, 3),
            "positive_recall": round(recall, 3), "negative_specificity": round(specificity, 3),
            "positive_examples": positives, "negative_examples": negatives,
            "note": "Tune only on development judgments; verify held-out and missing-answer queries before promotion."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("judgments", type=Path)
    args = parser.parse_args()
    print(json.dumps(calibrate(json.loads(args.report.read_text(encoding="utf-8")),
                               json.loads(args.judgments.read_text(encoding="utf-8"))), indent=2))
