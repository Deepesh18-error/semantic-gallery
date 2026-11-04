# Phase 7: judged ranking and no-match calibration

Phase 7 uses a versioned, replayable ranking policy bound to its evaluated index generation and configuration. The desktop search endpoint computes file-level evidence from exact filename/title matches, representation-specific semantic ranks, coverage in the **actual passage**, and independent visual/textual support. Explicit source-type constraints remain mandatory filters. Missing captions have no negative weight. The default policy preserves Phase 6 behavior until a reviewed policy file is activated with `SEMANTIC_GALLERY_RANK_POLICY_FILE`.

The current seven-query gallery evaluation is a smoke set. It has four development queries, three held-out queries, and one no-answer query that still requires collection-wide review. It cannot justify tuning or prove a false-positive target. Add judged queries for known files, broad categories, exact text, visual scenes, and verified no-answer cases. Do not change held-out labels while tuning. A no-answer label means a human checked the collection and confirmed that no file should answer the query.

Store personal reports, judgments, and policy drafts under ignored `evaluation/results/`. A judgments file has this form:

```json
{
  "cases": {
    "food": {"category": "broad_category", "positive": ["biryani.jpg", "cake.webp"]},
    "not_present_01": {"category": "no_answer", "positive": [], "verified_no_answer": true}
  }
}
```

Run the desktop server on one index generation and capture candidate diagnostics for the development split:

```powershell
backend\venv\Scripts\python.exe evaluation\run.py --mode live --split development --collection-id COLLECTION_ID --token TOKEN --repetitions 3 --debug-candidates --output evaluation\results\development.json
backend\venv\Scripts\python.exe evaluation\ranking_gate.py tune evaluation\results\development.json evaluation\results\judgments.json --false-positive-target 0.10 --policy-output evaluation\results\rank-policy.json
```

The tuner requires at least five judged *distinct queries* per category by default and refuses to tune when a known relevant file never reached the candidate pool. It explores bounded rank-fusion weights and final rejection rules using development cases only. Its policy output is a proposal; it does not activate the policy or modify held-out labels.

Capture a held-out baseline under the default policy, then set `SEMANTIC_GALLERY_RANK_POLICY_FILE` to the proposed policy and capture the candidate report with the same collection, index generation, query set, and load conditions. Validate with:

```powershell
backend\venv\Scripts\python.exe evaluation\ranking_gate.py validate evaluation\results\heldout-candidate.json evaluation\results\judgments.json --baseline evaluation\results\heldout-baseline.json --policy evaluation\results\rank-policy.json --false-positive-target 0.10
```

Validation reports quality separately by query category. It checks that the candidate pool and evidence stayed identical between baseline and candidate runs, then requires strictly better held-out answer quality with no answer-category regression, a p95 round-trip time no more than 1.2 times baseline with at least 20 timed requests per run, and a 95% Wilson upper bound on the no-answer false-positive rate below the agreed target. At the agreed 10% target, at least 35 reviewed no-answer queries with zero false positives are needed to pass this statistical gate. Fewer cases produce an inconclusive result rather than a false success. Adjust `--false-positive-target` to the target agreed for this project.

If the judged held-out results still fail after candidate retrieval and local ranking are sound, benchmark an optional bounded reranker against the same passages and images. Keep it outside the normal search path unless its measured benefit satisfies the p95 budget. No generative reranker is added by this phase.
