"""Repeatable Semantic Gallery evaluation. Uses only Python's standard library."""
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from urllib import request

ROOT = Path(__file__).resolve().parent


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    low, high = math.floor(rank), math.ceil(rank)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 2)


def score_case(case, response):
    names = [str(r.get("original_filename", "")).casefold() for r in response.get("results", [])]
    expected = {name.casefold() for name in case["relevant_files"]}
    ranks = [i + 1 for i, name in enumerate(names) if name in expected]
    return {
        "id": case["id"], "query": case["query"], "result_names": names,
        "expected": sorted(expected), "recall_at_5": round(len(set(names[:5]) & expected) / len(expected), 3) if expected else None,
        "precision_at_5": round(len([name for name in names[:5] if name in expected]) / min(5, len(names)), 3) if expected and names else None,
        "first_relevant_rank": min(ranks) if ranks else None,
        "empty_query_returned_results": bool(names) if not expected else None,
        "type_violation": any(r.get("media_type") != case["file_type"] for r in response.get("results", [])) if case.get("file_type") else None,
        "requires_review": bool(case.get("requires_review")),
        "index_generation": response.get("index_generation"),
        "index_config": response.get("index_config"),
        "candidate_debug": response.get("candidate_debug"),
        "candidate_pool_count": response.get("candidate_pool_count"),
        "candidate_debug_truncated": response.get("candidate_debug_truncated"),
        "metrics": response.get("metrics"),
    }


def summarize(rows):
    scored = [r for r in rows if r["expected"]]
    times = [r["client_round_trip_ms"] for r in rows if "client_round_trip_ms" in r]
    calls = {}
    for row in rows:
        for kind, count in (row.get("metrics") or {}).get("api_calls", {}).items():
            calls[kind] = calls.get(kind, 0) + count
    return {
        "queries": len(rows), "scored_queries": len(scored),
        "mean_recall_at_5": round(statistics.mean(r["recall_at_5"] for r in scored), 3) if scored else None,
        "first_result_accuracy": round(sum(r["first_relevant_rank"] == 1 for r in scored) / len(scored), 3) if scored else None,
        "round_trip_p50_ms": percentile(times, 50), "round_trip_p95_ms": percentile(times, 95),
        "api_calls": calls,
    }


def post_json(url, token, data):
    payload = json.dumps(data).encode("utf-8")
    req = request.Request(url, payload, {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with request.urlopen(req, timeout=45) as result:
        return json.load(result)


def offline():
    fixture = json.loads((ROOT / "known_food_candidates.json").read_text(encoding="utf-8"))
    candidates = fixture["candidates"]
    rejected = [c["filename"] for c in candidates if c["raw_score"] < fixture["thresholds"][c["type"]]]
    report = {"mode": "offline", "source": fixture["source"], "rejected_by_current_gate": rejected,
              "known_relevant_rejected": sorted(set(rejected) & {"biryani.jpg", "cake.webp"})}
    if len(report["known_relevant_rejected"]) != 2:
        raise AssertionError("Known food failure was not reproduced")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "live", "direct"], default="offline")
    parser.add_argument("--split", choices=["development", "held_out", "both"], default="both")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api")
    parser.add_argument("--collection-id", default=os.getenv("EVAL_COLLECTION_ID"))
    parser.add_argument("--user-id", default=os.getenv("EVAL_USER_ID"))
    parser.add_argument("--token", default=os.getenv("EVAL_AUTH_TOKEN"))
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--debug-candidates", action="store_true", help="Include owned candidate diagnostics for calibration")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "offline":
        report = offline()
    else:
        if not args.collection_id or args.repetitions < 1 or (args.mode == "live" and not args.token):
            parser.error("Live/direct mode requires --collection-id, positive repetitions, and live mode requires --token")
        if args.mode == "direct":
            if not args.user_id:
                parser.error("Direct mode also requires --user-id")
            sys.path.insert(0, str(ROOT.parent / "backend"))
            from dotenv import load_dotenv
            load_dotenv(ROOT.parent / "backend" / ".env")
            from media_engine.search_service import execute_search
        cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
        splits = ["development", "held_out"] if args.split == "both" else [args.split]
        rows = []
        for split in splits:
            for case in cases[split]:
                for repetition in range(args.repetitions):
                    started = time.perf_counter()
                    if args.mode == "direct":
                        response = execute_search(args.user_id, case["query"], args.collection_id,
                                                  case.get("file_type"), 20)
                    else:
                        response = post_json(args.base_url.rstrip("/") + "/search/", args.token,
                                             {"query": case["query"], "collection_id": args.collection_id,
                                              "file_type": case.get("file_type"), "limit": 20,
                                              "debug_candidates": args.debug_candidates})
                    row = score_case(case, response)
                    row.update({"split": split, "repetition": repetition + 1,
                                "client_round_trip_ms": round((time.perf_counter() - started) * 1000, 2)})
                    rows.append(row)
        generations = {row.get("index_generation") for row in rows}
        if len(generations) != 1:
            raise ValueError("Index generation changed during evaluation")
        configurations = {row.get("index_config") for row in rows}
        if len(configurations) != 1:
            raise ValueError("Index configuration changed during evaluation")
        report = {"mode": args.mode, "collection_id": args.collection_id, "summary": summarize(rows), "cases": rows,
                  "index_generation": generations.pop(),
                  "index_config": configurations.pop(),
                  "note": "Cold, cached, and indexing-load labels require separate controlled runs. Missing-answer judgments require human review."}
    output = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
