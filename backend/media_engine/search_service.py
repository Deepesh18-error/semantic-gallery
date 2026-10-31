import json
import os
import re
import time
from .gemini_service import get_query_embedding
from .pinecone_service import get_pinecone_index
from .database import db
from .search_metrics import SearchMetrics
from .provider_quota import call_with_quota
from .contracts import SEARCH_SCHEMA_VERSION, representation_for_vector, coverage_for_item, source_matches_filter
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from google import genai

MODALITY_THRESHOLDS = {
    "VISUAL": {
        "IMAGE": 0.30,
        "VIDEO": 0.30,
        "AUDIO": 0.45,
        "DOCUMENT": 0.62,
        "TEXT": 0.62,
    },
    "AUDIO": {
        "IMAGE": 0.55,
        "VIDEO": 0.45,
        "AUDIO": 0.30,
        "DOCUMENT": 0.55,
        "TEXT": 0.55,
    },
    "DOCUMENT": {
        "IMAGE": 0.52,
        "VIDEO": 0.52,
        "AUDIO": 0.45,
        "DOCUMENT": 0.30,
        "TEXT": 0.30,
    },
    "MIXED": {
        "IMAGE": 0.40,
        "VIDEO": 0.40,
        "AUDIO": 0.40,
        "DOCUMENT": 0.40,
        "TEXT": 0.40,
    },
}

MODALITY_BOOSTS = {
    "VISUAL": {
        "IMAGE": 1.35,
        "VIDEO": 1.30,
        "AUDIO": 0.80,
        "DOCUMENT": 0.75,
        "TEXT": 0.75,
    },
    "AUDIO": {
        "IMAGE": 0.85,
        "VIDEO": 1.05,
        "AUDIO": 1.30,
        "DOCUMENT": 0.85,
        "TEXT": 0.85,
    },
    "DOCUMENT": {
        "IMAGE": 0.90,
        "VIDEO": 0.85,
        "AUDIO": 0.90,
        "DOCUMENT": 1.25,
        "TEXT": 1.25,
    },
    "MIXED": {
        "IMAGE": 1.10,
        "VIDEO": 1.08,
        "AUDIO": 1.00,
        "DOCUMENT": 1.00,
        "TEXT": 1.00,
    },
}

FILE_TYPE_FILTER_MAP = {"DOCUMENT": ["DOCUMENT", "TEXT"]}

RERANK_REJECTION_THRESHOLD = 2.0


def _measured_call(metrics, name, function, *args):
    started = time.perf_counter()
    try:
        return function(*args)
    finally:
        metrics.duration(name, started)


def _extract_json_object(raw_text):
    """
    Gemini usually follows the JSON-only instruction, but this keeps search
    resilient if the response includes markdown fences or surrounding text.
    """
    if not raw_text:
        raise ValueError("Empty intent classifier response")

    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _extract_json_array(raw_text):
    """
    Parses a Gemini response that should contain a JSON array. The fallback
    keeps reranking resilient if the model wraps the array in extra text.
    """
    if not raw_text:
        raise ValueError("Empty reranker response")

    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if not match:
            raise
        return json.loads(match.group(0))


def classify_query_intent(query_text, on_api_call=None):
    """
    Phase C intent classifier.
    Uses Gemini Flash to classify the query into a soft routing intent.
    Returns a safe MIXED fallback if the classifier is unavailable.
    """
    fallback = {"intent": "MIXED", "confidence": 0.0}
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("[INTENT] GOOGLE_API_KEY missing; falling back to MIXED.")
        return fallback

    prompt = f"""
You are an intent classifier for a multimodal semantic search engine.

Classify the user's query into exactly one intent:
- VISUAL: the user likely wants images, photos, screenshots, visual scenes, or videos.
- AUDIO: the user likely wants voice notes, music, meetings, podcasts, recordings, or sound.
- DOCUMENT: the user likely wants PDFs, notes, text files, code, written content, reports, or exact document information.
- MIXED: the query is broad, ambiguous, or asks for everything across media types.

Rules:
- Intent is a soft ranking signal, not a hard media filter.
- Use MIXED for broad requests such as "everything related to ...".
- Use MIXED when multiple media types are equally plausible.
- Return ONLY valid JSON. No markdown. No explanation.

JSON schema:
{{"intent":"VISUAL|AUDIO|DOCUMENT|MIXED","confidence":0.0}}

User query: {query_text}
""".strip()

    try:
        client = genai.Client(api_key=api_key)
        flash_model = os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash-lite")
        response = call_with_quota(flash_model, "intent", max(1, len(prompt) // 4),
            lambda: client.models.generate_content(
            model=os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash-lite"),
            contents=prompt
        ), estimated_output_tokens=40, priority="interactive",
            on_send=(lambda: on_api_call("intent")) if on_api_call else None)
        data = _extract_json_object(response.text)
        intent = str(data.get("intent", "MIXED")).upper()
        if intent not in MODALITY_THRESHOLDS:
            intent = "MIXED"

        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(confidence, 1.0))
        return {"intent": intent, "confidence": confidence}

    except Exception as e:
        print(f"[INTENT] Gemini Flash classification failed: {e}. Falling back to MIXED.")
        return fallback


def get_modality_threshold(intent, media_type):
    return MODALITY_THRESHOLDS.get(intent, MODALITY_THRESHOLDS["MIXED"]).get(media_type, 0.40)


def get_modality_boost(intent, media_type):
    return MODALITY_BOOSTS.get(intent, MODALITY_BOOSTS["MIXED"]).get(media_type, 1.00)


def rerank_candidates_with_flash(query_text, candidates, on_api_call=None):
    """
    Phase D: Flash Reranker.
    Takes the assembled candidate list (after boost + noise gate),
    asks Gemini Flash to score each one 0-10 for genuine relevance,
    and returns candidates with a new 'final_score' field.

    Final score formula: (0.40 Ã— pinecone_similarity) + (0.60 Ã— flash_score_normalized)

    Falls back to Phase A scores silently if Flash fails.
    """
    if not candidates:
        return candidates

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("[RERANK] GOOGLE_API_KEY missing. Skipping reranking.")
        for c in candidates:
            c['final_score'] = c['similarity_percentage'] / 100.0
        return candidates

    # --- BUILD THE BATCH PROMPT ---
    # For each candidate, we give Flash: index, filename, type, size, and
    # the most descriptive text we have about it (ai_description or chunk text).
    candidate_lines = []
    for i, c in enumerate(candidates):
        filename = c.get('original_filename', 'Unknown')
        media_type = c.get('media_type', 'UNKNOWN')
        size_kb = round((c.get('file_size_bytes') or 0) / 1024, 1)

        # The evidence text is what Phase B+C prepared for exactly this moment
        evidence = c.get('rerank_evidence_text', '').strip()
        if not evidence:
            evidence = filename  # Absolute fallback â€” filename only

        # Truncate to keep the prompt size safe (max ~300 chars per candidate)
        evidence = evidence[:300]

        candidate_lines.append(
            f"{i+1}. [{media_type}] \"{filename}\" ({size_kb} KB)\n"
            f"   Description: {evidence}"
        )

    candidates_block = "\n\n".join(candidate_lines)

    prompt = f"""You are a strict relevance judge for a multimodal semantic file search engine.

User's search query: "{query_text}"

Below are {len(candidates)} search result candidates. Each has a type (IMAGE, VIDEO, AUDIO, DOCUMENT, TEXT), a filename, and a description or matched text.

Your job: Score each candidate from 0 to 10 based on how genuinely relevant it is to the user's query.

Scoring rules:
- 9-10: Clearly and directly relevant. Exactly what the user is looking for.
- 7-8: Very likely relevant. Strong match.
- 4-6: Possibly relevant. Partial match or uncertain.
- 1-3: Unlikely to be what the user wants. Weak or coincidental match.
- 0: Completely irrelevant. No connection to the query whatsoever.

Be strict. A document about mathematics should score 0 for a query about cricket or sports photos.
A nature photo should score 0 for a query about code or assignments.
Do not reward weak keyword overlap if the candidate is not truly about the query.

Return ONLY a valid JSON array of numbers, one score per candidate, in the same order.
No explanation. No markdown. No text. Just the JSON array.

Example output for 4 candidates: [8, 0, 6, 9]

Candidates:
{candidates_block}"""

    try:
        rerank_start = time.time()
        print(f"  ðŸ¤– [RERANK] Sending {len(candidates)} candidates to Flash for scoring...")

        client = genai.Client(api_key=api_key)
        flash_model = os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash-lite")
        response = call_with_quota(flash_model, "rerank", max(1, len(prompt) // 4),
            lambda: client.models.generate_content(
            model=os.getenv("GEMINI_FLASH_MODEL", "gemini-2.5-flash-lite"),
            contents=prompt
        ), estimated_output_tokens=max(40, len(candidates) * 4), priority="interactive",
            on_send=(lambda: on_api_call("rerank")) if on_api_call else None)

        raw = response.text.strip()

        scores = _extract_json_array(raw)

        # Validate: must be a list of the right length
        if not isinstance(scores, list) or len(scores) != len(candidates):
            raise ValueError(
                f"Score list length mismatch: got {len(scores)}, expected {len(candidates)}"
            )

        rerank_time = time.time() - rerank_start
        print(f"  âœ… [RERANK] Flash scored {len(scores)} candidates in {rerank_time:.3f}s")
        print(f"  ðŸ“Š [RERANK] Scores: {scores}")

        # --- APPLY COMBINED SCORING ---
        for i, c in enumerate(candidates):
            raw_flash_score = float(scores[i])
            # Clamp to 0-10 range in case Flash goes rogue
            raw_flash_score = max(0.0, min(10.0, raw_flash_score))

            flash_normalized = raw_flash_score / 10.0
            pinecone_sim = c['similarity_percentage'] / 100.0

            # The formula: Flash dominates (60%), Pinecone is context (40%)
            combined = (0.40 * pinecone_sim) + (0.60 * flash_normalized)
            c['final_score'] = combined
            c['flash_score'] = raw_flash_score  # Store for debugging/UI

        return candidates

    except Exception as e:
        print(f"  âš ï¸ [RERANK] Flash reranking failed: {e}. Falling back to Phase A scores.")
        # Safe fallback â€” use the Phase A boosted score as final_score
        for c in candidates:
            c['final_score'] = c['similarity_percentage'] / 100.0
        return candidates

def _log_search_history_async(user_id, query_text, results_count, result_media_ids):
    """
    Worker function for Stage F. Runs in a daemon thread 
    to prevent slowing down the search response.
    """
    try:
        history_doc = {
            "user_id": str(user_id),
            "query_text": query_text,
            "results_count": results_count,
            "result_media_ids": result_media_ids,
            "searched_at": datetime.utcnow()
        }
        db.search_history.insert_one(history_doc)
    except Exception as e:
        print(f"âš ï¸ [HISTORY] Failed to log search: {e}")
        
       
def execute_search(user_id, query_text, collection_id=None, file_type=None, limit=10):
    """
    Stages: A (Embed), B (Pinecone), C (Dedup), D (Mongo), E (Assembly)
    """
    search_start = time.time()
    metrics = SearchMetrics()
    print(f"\nðŸ” [SEARCH PIPELINE] Starting search for: '{query_text}'")
    #  STAGE A: EMBED QUERY + CLASSIFY INTENT IN PARALLEL
    # This is our only external API call
    stage_a_start = time.time()
    with ThreadPoolExecutor(max_workers=2) as executor:
        embedding_future = executor.submit(_measured_call, metrics, "query_embedding", get_query_embedding, query_text, metrics.call)
        intent_future = executor.submit(_measured_call, metrics, "intent_classification", classify_query_intent, query_text, metrics.call)
        query_vector = embedding_future.result()
        intent_data = intent_future.result()
    stage_a_end = time.time()
    metrics.timings_ms["query_embedding_and_intent"] = round((stage_a_end - stage_a_start) * 1000, 2)

    query_intent = intent_data["intent"]
    intent_confidence = intent_data["confidence"]
    print(
        f"ðŸ§­ [SEARCH PIPELINE] Phase C intent: {query_intent} "
        f"(confidence={intent_confidence:.2f})"
    )

    if not query_vector:
        return {"error": "Failed to generate query embedding"}

    #  STAGE B: QUERY PINECONE 
    # We ask for Top 50 to allow for document/video deduplication later
    stage_b_start = time.time()
    try:
        index = get_pinecone_index()
        
        # 1. Build the Secure Metadata Filter
        # Crucial: user_id ensures users only see their own data
        pinecone_filter = {
            "user_id": str(user_id)
        }
        
        # 2. Add Optional Filters (Scoping search)
        if collection_id:
            pinecone_filter["collection_id"] = str(collection_id)
        
        if file_type and file_type != "ALL":
            compatible_types = FILE_TYPE_FILTER_MAP.get(file_type)
            pinecone_filter["file_type"] = {"$in": compatible_types} if compatible_types else file_type

        # 3. Execute Pinecone Search
        pinecone_response = index.query(
            vector=query_vector,
            filter=pinecone_filter,
            top_k=50, # Ask for 50 raw vectors
            include_metadata=True
        )
        
        print(f"\nðŸ” [SCORE DEBUG] Raw Pinecone returned {len(pinecone_response.matches)} matches:")
        for match in pinecone_response.matches:
            print(f"   ID: {match.id} | Score: {match.score:.4f} | type: {match.metadata.get('vector_type')} | file: {match.metadata.get('original_filename')}")
        print()
        
        raw_results = pinecone_response['matches']
        stage_b_end = time.time()
        metrics.timings_ms["vector_retrieval"] = round((stage_b_end - stage_b_start) * 1000, 2)

        print(f"ðŸŒ² [SEARCH PIPELINE] Pinecone found {len(raw_results)} raw vector matches.")

        # --- PHASE E: NON-PRIMARY CHUNK SUPPRESSION ---
        # A PDF's 15th chunk about an unrelated topic can accidentally match a query
        # just because that chunk shares a few common words. We suppress it unless
        # it scores high enough to be genuinely relevant on its own merit.
        #
        # Rules:
        # - Description vectors (vector_type == "description"): NEVER suppressed.
        #   They are always the best signal for a file and must always compete.
        # - Primary chunks (is_primary == True or chunk_index == 0): NEVER suppressed.
        #   The first chunk of any file is always allowed through.
        # - Non-primary chunks with score >= 0.55: ALLOWED.
        #   A secondary chunk scoring this high is genuinely relevant.
        # - Non-primary chunks with score < 0.55: SUPPRESSED.
        #   This is the flooding case â€” a distant chunk weakly matching the query.
        #
        # For existing files uploaded before Phase E (no chunk metadata):
        # metadata.get('is_primary') returns None â†’ treated as True (safe fallback).
        # This means old files are NOT penalised â€” they pass through unchanged.

        NON_PRIMARY_SCORE_FLOOR = 0.50 if query_intent == "DOCUMENT" else 0.55

        pre_filter_count = len(raw_results)
        filtered_results = []

        for match in raw_results:
            meta = match['metadata']
            score = match['score']
            vector_type = meta.get('vector_type', 'content')

            # Rule 1: Description vectors always pass
            if vector_type == 'description':
                filtered_results.append(match)
                continue

            # Rule 2: Check is_primary â€” None means old file, treat as primary (safe)
            is_primary = meta.get('is_primary')
            if is_primary is None or is_primary is True:
                filtered_results.append(match)
                continue

            # Rule 3: Non-primary chunk â€” apply score floor
            if score >= NON_PRIMARY_SCORE_FLOOR:
                filtered_results.append(match)
            else:
                print(
                    f"  ðŸ”‡ [PHASE E] Suppressed non-primary chunk: "
                    f"{meta.get('original_filename', 'unknown')} | "
                    f"chunk_index={meta.get('chunk_index', '?')} | "
                    f"score={score:.4f} (below {NON_PRIMARY_SCORE_FLOOR})"
                )

        raw_results = filtered_results
        suppressed_count = pre_filter_count - len(raw_results)
        if suppressed_count > 0:
            print(f"  ðŸ”‡ [PHASE E] Suppressed {suppressed_count} non-primary low-score chunks. "
                f"{len(raw_results)} vectors proceed to deduplication.")
        
    except Exception as e:
        print(f"âŒ [SEARCH PIPELINE] Pinecone stage failed: {e}")
        return {"error": "Vector database query failed"}

    #  SUMMARY LOGS 
    print(f"  â±ï¸  Stage A (Gemini + Intent): {stage_a_end - stage_a_start:.3f}s")
    print(f"  â±ï¸  Stage B (Pinecone): {stage_b_end - stage_b_start:.3f}s")

    
    # --- STAGE C: DEDUPLICATION (The In-Memory Loop) ---
    stage_c_start = time.time()

    unique_media_map = {}

    for match in raw_results:
        metadata = match['metadata']
        m_id = metadata['media_item_id']
        score = match['score']
        vector_type = metadata.get('vector_type', 'content')
        is_description = (vector_type == 'description')
        is_ocr = (vector_type == 'ocr_text')

        if m_id not in unique_media_map:
            unique_media_map[m_id] = {
                "score": score,
                "pinecone_metadata": metadata,
                "has_description": is_description,
                "has_ocr": is_ocr,
            }
            continue

        existing = unique_media_map[m_id]

        if is_description:
            # Descriptions are useful semantic evidence, but OCR/content can be stronger.
            if score >= existing['score'] - 0.03:
                unique_media_map[m_id] = {
                    "score": score,
                    "pinecone_metadata": metadata,
                    "has_description": True,
                    "has_ocr": existing.get('has_ocr', False),
                }
            else:
                existing['has_description'] = True
            continue

        if is_ocr:
            existing['has_ocr'] = True

        if score > existing['score']:
            unique_media_map[m_id] = {
                "score": score,
                "pinecone_metadata": metadata,
                "has_description": existing.get('has_description', False),
                "has_ocr": existing.get('has_ocr', False) or is_ocr,
            }

    stage_c_end = time.time()
    metrics.timings_ms["candidate_grouping"] = round((stage_c_end - stage_c_start) * 1000, 2)
    unique_ids = list(unique_media_map.keys())
    print(f"[SEARCH PIPELINE] Deduplication: {len(raw_results)} vectors -> {len(unique_ids)} unique files.")

    # --- STAGE D: MONGODB ENRICHMENT (The Single Trip) ---
    stage_d_start = time.time()
    mongo_items = list(db.media_items.find(
        {"_id": {"$in": unique_ids}, "user_id": str(user_id)},
        {
            "user_id": 0,
            "vector_ids": 0
        }
    ))

    merged_results = []
    for item in mongo_items:
        m_id = str(item['_id'])
        sim_data = unique_media_map.get(m_id)

        if sim_data and source_matches_filter(item, file_type):
            item['similarity_score'] = sim_data['score']
            item['winning_metadata'] = sim_data['pinecone_metadata']
            merged_results.append(item)

    stage_d_end = time.time()
    metrics.timings_ms["metadata_fetch"] = round((stage_d_end - stage_d_start) * 1000, 2)
    print(f"[SEARCH PIPELINE] MongoDB enriched {len(merged_results)} items.")
    print(f"  Stage C (Dedup):   {(stage_c_end - stage_c_start) * 1000:.2f}ms")
    print(f"  Stage D (Mongo):   {(stage_d_end - stage_d_start) * 1000:.2f}ms")

    # --- STAGE E: ASSEMBLY + MODALITY-AWARE FILTERING ---
    stage_e_start = time.time()
    merged_results.sort(key=lambda x: x['similarity_score'], reverse=True)

    final_results = []
    max_limit = min(limit, 20)

    for item in merged_results:
        if item.get("processing_status") not in ("EMBEDDED", "PARTIALLY_EMBEDDED"):
            continue
        score = item['similarity_score']
        m_type = item['media_type']
        raw_score = score
        boost = get_modality_boost(query_intent, m_type)
        threshold = get_modality_threshold(query_intent, m_type)

        score = min(score * boost, 1.0)

        if raw_score < threshold:
            print(
                f"  [NOISE GATE] {item['file_metadata'].get('original_name', 'unknown')} | "
                f"type: {m_type} | intent: {query_intent} | raw: {raw_score:.4f} | "
                f"boosted: {score:.4f} | threshold: {threshold:.2f}"
            )
            continue

        win_meta = item.get('winning_metadata', {})

        assembled = {
            "schema_version": SEARCH_SCHEMA_VERSION,
            "file_id": str(item['_id']),
            "media_item_id": str(item['_id']),
            "similarity_percentage": round(score * 100),
            "media_type": m_type,
            "source_type": m_type,
            "mime_type": item.get('file_metadata', {}).get('mime_type'),
            "file_version": item.get('file_version'),
            "index_config": win_meta.get('index_config'),
            "representation": representation_for_vector(win_meta),
            "evidence_source": win_meta.get('vector_type', 'content'),
            "location": {
                "page_number": win_meta.get('page_number'),
                "chunk_index": win_meta.get('chunk_index'),
                "start_time": win_meta.get('start_time'),
                "end_time": win_meta.get('end_time'),
            },
            "availability": "available" if item.get('file_path') and os.path.isfile(item['file_path']) else "missing",
            "indexing_coverage": coverage_for_item(item),
            "original_filename": item['file_metadata'].get('original_name'),
            "collection_id": str(item['collection_id']),
            "created_at": item['created_at'].isoformat() if isinstance(item['created_at'], datetime) else item['created_at'],
            "file_size_bytes": item['file_metadata'].get('size_bytes'),
            "ai_description": item.get('ai_description'),
            "description_status": item.get('description_status'),
            "matched_by_ai": False,
        }

        if win_meta.get('vector_type') == 'description':
            assembled["matched_by_ai"] = True
            assembled["ai_summary"] = win_meta.get('description_preview', win_meta.get('chunk_text', ''))[:300]
            assembled["rerank_evidence_text"] = item.get('ai_description') or assembled["ai_summary"]
        elif m_type == 'DOCUMENT':
            chunk_text = win_meta.get('chunk_text', "")
            assembled["matched_chunk_preview"] = chunk_text[:300]
            assembled["rerank_evidence_text"] = chunk_text[:1000]
        elif win_meta.get('vector_type') == 'ocr_text':
            chunk_text = win_meta.get('chunk_text', "")
            assembled["matched_chunk_preview"] = chunk_text[:300]
            assembled["rerank_evidence_text"] = chunk_text[:1000]
        elif item.get('ai_description'):
            assembled["rerank_evidence_text"] = item.get('ai_description')[:1000]
        else:
            assembled["rerank_evidence_text"] = item['file_metadata'].get('original_name', '')

        if m_type in ['VIDEO', 'AUDIO']:
            assembled["matched_segment_start_time"] = win_meta.get('start_time', 0.0)
            assembled["matched_segment_end_time"] = win_meta.get('end_time', 0.0)

        final_results.append(assembled)

    stage_e_end = time.time()
    metrics.timings_ms["assembly_and_filtering"] = round((stage_e_end - stage_e_start) * 1000, 2)

    # --- STAGE E2: PHASE D FLASH RERANKING ---
    # Decide whether to run reranker:
    # Always run on top 15 candidates when results exist.
    # Skip only if all results are same type AND intent confidence is very high.
    stage_rerank_start = time.time()

    media_types_in_results = set(r['media_type'] for r in final_results)
    should_rerank = len(final_results) > 0 and not (
        len(media_types_in_results) == 1 and intent_confidence > 0.92
    )

    if should_rerank:
        # Pass top 15 to the reranker â€” no point scoring candidates beyond 15
        top_candidates = final_results[:15]
        remainder = final_results[15:]

        top_candidates = rerank_candidates_with_flash(query_text, top_candidates, metrics.call)

        # Sort by final_score descending
        top_candidates.sort(key=lambda x: x['final_score'], reverse=True)

        rerank_succeeded = any('flash_score' in c for c in top_candidates)
        if rerank_succeeded:
            before_reject = len(top_candidates)
            top_candidates = [
                c for c in top_candidates
                if c.get('flash_score', 10.0) > RERANK_REJECTION_THRESHOLD
            ]
            rejected_count = before_reject - len(top_candidates)
            if rejected_count:
                print(
                    f"  [RERANK] Rejected {rejected_count} low-relevance candidate(s) "
                    f"with Flash score <= {RERANK_REJECTION_THRESHOLD}."
                )
            remainder = []

        # Update similarity_percentage to reflect the reranked score for the UI
        for c in top_candidates:
            c['similarity_percentage'] = round(c['final_score'] * 100)

        # Rejoin with remainder (remainder keeps Phase A scores)
        final_results = top_candidates + remainder
    else:
        print(f"  â­ï¸  [RERANK] Skipped â€” single media type with high confidence ({intent_confidence:.2f})")
        for c in final_results:
            c['final_score'] = c['similarity_percentage'] / 100.0

    stage_rerank_end = time.time()
    metrics.timings_ms["reranking"] = round((stage_rerank_end - stage_rerank_start) * 1000, 2)

    # Now slice to the requested limit AFTER reranking
    final_results = final_results[:max_limit]

    print(f"  â±ï¸  Stage E  (Assembly):  {(stage_e_end - stage_e_start) * 1000:.2f}ms")
    print(f"  â±ï¸  Stage E2 (Reranking): {(stage_rerank_end - stage_rerank_start) * 1000:.2f}ms")

    # --- STAGE F: SEARCH HISTORY (Async, Non-Blocking) ---
    # We fire this and immediately return the results to the user.
    history_thread = threading.Thread(
        target=_log_search_history_async,
        args=(
            user_id, 
            query_text, 
            len(final_results), 
            [r['media_item_id'] for r in final_results]
        ),
        daemon=True
    )
    history_thread.start()

    # FINAL RETURN
    total_time_ms = int((time.time() - search_start) * 1000)
    metric_snapshot = metrics.snapshot()
    print("[SEARCH_METRICS] " + json.dumps(metric_snapshot, sort_keys=True))
    print(f"ðŸ [SEARCH PIPELINE] Search Complete. Returned {len(final_results)} items in {total_time_ms}ms.")
    
    return {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "query": query_text,
        "intent": query_intent,
        "intent_confidence": intent_confidence,
        "results": final_results,
        "total_count": len(final_results),
        "search_time_ms": total_time_ms,
        "metrics": metric_snapshot
    }
