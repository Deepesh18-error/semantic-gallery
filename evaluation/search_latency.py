"""HTTP latency measurements with verified cache/load labels; no file mutations."""
import argparse
import json
import os
import platform
import time
from pathlib import Path

from run import percentile, post_json


def verify_sample(mode, scenario, response):
    metrics = response.get("metrics", {})
    if mode == "semantic" and response.get("semantic_status") != "complete":
        raise RuntimeError("Semantic search did not complete; this is not a valid semantic latency sample")
    if scenario == "cached" and not metrics.get("result_cache_hit"):
        raise RuntimeError("Cached scenario missed its result cache; index may be changing")
    if scenario == "uncached" and not metrics.get("embedding_provider_calls"):
        raise RuntimeError("Query did not make a provider call; cannot label it uncached cloud search")
    if scenario == "indexing" and metrics.get("indexing_active") is not True:
        raise RuntimeError("No running indexing lease was observed; load scenario is unverified")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True, help="JSON array of stable query strings")
    parser.add_argument("--collection-id", default=os.getenv("EVAL_COLLECTION_ID"))
    parser.add_argument("--token", default=os.getenv("EVAL_AUTH_TOKEN"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api")
    parser.add_argument("--mode", choices=("local", "semantic"), default="local")
    parser.add_argument("--scenario", choices=("warm", "cached", "uncached", "indexing", "cold"), required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.token or not args.collection_id or args.repetitions < 1:
        parser.error("Set EVAL_AUTH_TOKEN, EVAL_COLLECTION_ID, and positive repetitions")
    queries = json.loads(args.queries.read_text(encoding="utf-8"))
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        parser.error("queries must be a nonempty JSON array of strings")
    if args.scenario == "uncached" and (args.mode != "semantic" or args.repetitions != 1):
        parser.error("Uncached measurements require semantic mode and one repetition of fresh queries")
    if args.scenario == "cold" and (len(queries) != 1 or args.repetitions != 1):
        parser.error("A cold sample is one request immediately after an operator restarts the server")
    rows = []
    for query in queries:
        body = {"query": query, "collection_id": args.collection_id, "mode": args.mode,
                "limit": 20, "measure_load": True}
        if args.scenario == "cached":
            post_json(args.base_url.rstrip("/") + "/search/", args.token, body)
        for _ in range(args.repetitions):
            started = time.perf_counter()
            response = post_json(args.base_url.rstrip("/") + "/search/", args.token, body)
            elapsed = (time.perf_counter() - started) * 1000
            metrics = response.get("metrics", {})
            verify_sample(args.mode, args.scenario, response)
            rows.append({"round_trip_ms": round(elapsed, 2), "result_count": response["total_count"],
                         "metrics": metrics})
    times = [row["round_trip_ms"] for row in rows]
    p50, p95 = percentile(times, 50), percentile(times, 95)
    target = 300 if args.mode == "local" else 500 if args.scenario == "cached" else 3000
    report = {"scenario": args.scenario, "mode": args.mode,
              "reference_machine": {"system": platform.platform(), "processor": platform.processor(),
                                    "logical_cpus": os.cpu_count()},
              "sample_count": len(rows), "p50_ms": p50, "p95_ms": p95,
              "p95_target_ms": target, "enough_samples": len(rows) >= 20,
              "target_passed": len(rows) >= 20 and p95 < target and
                  (args.mode == "local" or args.scenario == "cached" or p50 < 1500),
              "rows": rows, "scope": "HTTP through backend; excludes browser cards and overlay startup"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
