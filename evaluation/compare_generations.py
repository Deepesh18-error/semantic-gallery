"""Compare two live Phase 0 evaluation reports before search generation promotion."""
import argparse
import json
from pathlib import Path


def compare(baseline, candidate):
    if baseline.get("mode") != "live" or candidate.get("mode") != "live":
        raise ValueError("Both inputs must be live evaluation reports")
    if baseline.get("collection_id") != candidate.get("collection_id"):
        raise ValueError("Reports must use the same collection")
    if baseline.get("index_generation") != 1 or candidate.get("index_generation") != 2:
        raise ValueError("Expected a generation 1 baseline and generation 2 candidate")
    old = {case["id"]: case for case in baseline["cases"]}
    new = {case["id"]: case for case in candidate["cases"]}
    if old.keys() != new.keys():
        raise ValueError("Reports must contain the same evaluation cases")
    regressions = []
    for key, before in old.items():
        after = new[key]
        if before["expected"] != after["expected"]:
            raise ValueError(f"Labels changed for {key}")
        if before["recall_at_5"] is not None and after["recall_at_5"] < before["recall_at_5"]:
            regressions.append(key)
        if after.get("type_violation"):
            regressions.append(key + ":type")
    a, b = baseline["summary"], candidate["summary"]
    return {"baseline": a, "candidate": b, "recall_regressions": regressions,
            "promotion_ready": not regressions and
                a["mean_recall_at_5"] is not None and
                b["mean_recall_at_5"] is not None and
                b["mean_recall_at_5"] >= a["mean_recall_at_5"] and
                a.get("queries", 0) >= 20 and b.get("queries", 0) >= 20 and
                a["round_trip_p95_ms"] is not None and b["round_trip_p95_ms"] is not None and
                b["round_trip_p95_ms"] <= a["round_trip_p95_ms"] * 1.2}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    report = compare(json.loads(args.baseline.read_text(encoding="utf-8")),
                     json.loads(args.candidate.read_text(encoding="utf-8")))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["promotion_ready"] else 1)
