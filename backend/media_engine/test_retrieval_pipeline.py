import tempfile
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .auth_service import create_token
from .catalog_store import SQLiteCatalog
from .contracts import SearchRequest
from .desktop_embedding import PREVIOUS_MODEL_CONFIG
from .local_retrieval import SQLiteExactVectors, SQLiteFullText
from .models import CatalogAccount
from .retrieval_pipeline import fuse, resolve_constraints
from .ranking_policy import DEFAULT_POLICY, accept_features, features_from_entry


class RetrievalPipelineTests(TestCase):
    def setUp(self):
        from .search_cache import _items
        _items.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        setting = override_settings(MEDIA_VAULT=self.root)
        setting.enable()
        self.addCleanup(setting.disable)
        self.store = SQLiteCatalog()
        self.store.put_source(source_id="source", user_id="alice", kind="managed_import", root_path=self.root)
        CatalogAccount.objects.create(id="alice", email="alice@example.test", full_name="Alice",
                                      password_hash="unused")
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + create_token("alice"))

    def add_file(self, name, source_type, representation, text="", heading="", vector=None):
        path = self.root / name
        path.write_bytes(b"sample")
        file = self.store.put_file(file_id=name, user_id="alice", source_id="source",
            path=path, source_type=source_type,
            mime_type="application/pdf" if name.endswith(".pdf") else
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if name.endswith(".docx")
                else "application/octet-stream",
            fingerprint=name, original_name=name, processing_status="EMBEDDED")
        segment = self.store.put_segment(file_version_id=file.current_version_id,
            ordinal=0, representation=representation, text=text, heading=heading,
            title=name, page_number=12 if source_type == "DOCUMENT" else None)
        if vector is not None:
            SQLiteExactVectors().upsert(vector_id="v:" + name, segment_id=segment.id,
                values=vector, model_config=PREVIOUS_MODEL_CONFIG, generation=1)
        return segment

    def test_constraints_and_explicit_chip(self):
        request = SearchRequest.parse({"query": "give me photos of burger"})
        self.assertEqual(resolve_constraints(request, {})[0], "IMAGE")
        self.assertEqual(resolve_constraints(request, {})[1], "burger")
        explicit = SearchRequest.parse({"query": "give me photos of burger", "file_type": "DOCUMENT"})
        self.assertEqual(resolve_constraints(explicit, {"file_type": "DOCUMENT"})[0], "DOCUMENT")
        broad = SearchRequest.parse({"query": "photo of a video camera"})
        self.assertIsNone(resolve_constraints(broad, {})[0])

    def test_food_semantic_search_without_generative_ranking(self):
        burger = [1.0] + [0.0] * 3071
        cake = [0.9, 0.1] + [0.0] * 3070
        unrelated = [0.0, 1.0] + [0.0] * 3070
        self.add_file("biryani.jpg", "IMAGE", "visual", vector=burger)
        self.add_file("cake.webp", "IMAGE", "visual", vector=cake)
        self.add_file("unrelated.pdf", "DOCUMENT", "text", text="cloud networking", vector=unrelated)
        with patch("media_engine.desktop_embedding.embed_query", return_value=burger) as embed:
            response = self.client.post("/api/search/", {"query": "food items"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["original_filename"] for row in response.data["results"]},
                         {"biryani.jpg", "cake.webp"})
        self.assertEqual(embed.call_count, 1)
        self.assertEqual(response.data["results"][0]["evidence_source"], "semantic:visual")
        self.assertEqual(response.data["results"][0]["similarity_percentage"], None)

    def test_long_document_cannot_fill_visual_capacity(self):
        pdf = self.add_file("long.pdf", "DOCUMENT", "text", text="AWS VPC", vector=[1, 0, 0])
        image = self.add_file("burger.jpg", "IMAGE", "visual", vector=[0, 1, 0])
        file = pdf.file_version.file
        for ordinal in range(1, 30):
            segment = self.store.put_segment(file_version_id=file.current_version_id,
                ordinal=ordinal, representation="text", text="AWS VPC", page_number=ordinal + 1)
            SQLiteExactVectors().upsert(vector_id=f"pdf:{ordinal}", segment_id=segment.id,
                values=[1, 0, 0], model_config="test", generation=2)
        SQLiteExactVectors().upsert(vector_id="pdf:0", segment_id=pdf.id,
            values=[1, 0, 0], model_config="test", generation=2)
        SQLiteExactVectors().upsert(vector_id="image:0", segment_id=image.id,
            values=[0, 1, 0], model_config="test", generation=2)
        groups = SQLiteExactVectors().query_grouped([0.7, 0.7, 0], user_id="alice",
            model_config="test", generation=2, per_group=1, per_file=2)
        self.assertEqual({row["file_id"] for row in groups["text"]}, {"long.pdf"})
        self.assertLessEqual(len(groups["text"]), 2)
        self.assertEqual({row["file_id"] for row in groups["visual"]}, {"burger.jpg"})

    def test_fts_indexes_title_heading_and_page(self):
        segment = self.add_file("cloud.pdf", "DOCUMENT", "text", text="private routing",
                                heading="AWS VPC")
        hits = SQLiteFullText().search_evidence("AWS VPC", user_id="alice")
        self.assertEqual(hits[0]["segment_id"], segment.id)
        self.assertEqual(hits[0]["page_number"], 12)
        self.assertEqual(hits[0]["passage_terms"], 0)
        self.assertEqual(hits[0]["heading_terms"], 2)
        self.add_file("interview.mp3", "AUDIO", "transcript", text="rocket launch interview")
        transcript_hits = SQLiteFullText().search_evidence("rocket launch", user_id="alice")
        self.assertEqual(transcript_hits[0]["representation"], "transcript")

    def test_final_policy_can_return_no_match(self):
        entries = fuse([], {"visual": [{"file_id": "unrelated", "segment_id": 1,
                                         "score": 0.1}]}, [])
        features = features_from_entry(entries["unrelated"], filename="unrelated.jpg", query="burger")
        self.assertFalse(accept_features(features, DEFAULT_POLICY))

    def test_overlapping_passages_contribute_one_rank_per_channel(self):
        one = fuse([{"file_id": "pdf", "segment_id": 1, "matched_terms": 2,
                     "query_terms": 2, "bm25": -2}], {}, [])
        repeated = fuse([{"file_id": "pdf", "segment_id": i, "matched_terms": 2,
                          "query_terms": 2, "bm25": -2} for i in range(1, 20)], {}, [])
        self.assertEqual(one["pdf"]["rank_score"], repeated["pdf"]["rank_score"])
        self.assertLessEqual(len(repeated["pdf"]["evidence"]), 2)

    def test_api_no_match_and_explicit_filter(self):
        query_vector = [1.0] + [0.0] * 3071
        unrelated = [0.0, 1.0] + [0.0] * 3070
        self.add_file("burger.jpg", "IMAGE", "visual", vector=unrelated)
        self.add_file("burger.pdf", "DOCUMENT", "text", text="recipe", vector=unrelated)
        with patch("media_engine.desktop_embedding.embed_query", return_value=query_vector):
            missing = self.client.post("/api/search/", {"query": "purple elephant"}, format="json")
            filtered = self.client.post("/api/search/", {"query": "photos of burger",
                "file_type": "DOCUMENT"}, format="json")
        self.assertEqual(missing.status_code, 200)
        self.assertEqual(missing.data["match_decision"], "no_match")
        self.assertEqual(missing.data["results"], [])
        self.assertEqual(filtered.data["effective_file_type"], "DOCUMENT")
        self.assertEqual([row["media_type"] for row in filtered.data["results"]], ["DOCUMENT"])

    def test_inferred_pdf_excludes_docx(self):
        self.add_file("burger.pdf", "DOCUMENT", "text", text="burger recipe")
        self.add_file("burger.docx", "DOCUMENT", "text", text="burger recipe")
        response = self.client.post("/api/search/", {"query": "find PDF burger"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["original_filename"] for row in response.data["results"]],
                         ["burger.pdf"])

    def test_rejects_policy_for_different_generation(self):
        self.add_file("burger.jpg", "IMAGE", "visual")
        policy = deepcopy(DEFAULT_POLICY)
        policy["index_generation"] = 99
        path = self.root / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        with patch.dict("os.environ", {"SEMANTIC_GALLERY_RANK_POLICY_FILE": str(path)}):
            response = self.client.post("/api/search/", {"query": "burger"}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "RANK_POLICY_INVALID")

    def test_local_suggestions_never_embed_and_cache_invalidates_on_changes(self):
        segment = self.add_file("burger.jpg", "IMAGE", "visual", vector=[1.0] + [0.0] * 3071)
        with patch("media_engine.desktop_embedding.embed_query", side_effect=AssertionError("Cloud call")):
            first = self.client.post("/api/search/", {"query": "burger", "mode": "local"}, format="json")
            second = self.client.post("/api/search/", {"query": "burger", "mode": "local"}, format="json")
            self.assertFalse(first.data["metrics"]["result_cache_hit"])
            self.assertTrue(second.data["metrics"]["result_cache_hit"])
            self.assertNotIn("vector_ids", first.data["results"][0])
            self.assertNotIn("file_path", first.data["results"][0])
            file = segment.file_version.file
            file.original_name = "waterfall.jpg"
            file.save(update_fields=["original_name"])
            changed = self.client.post("/api/search/", {"query": "burger", "mode": "local"}, format="json")
            self.assertFalse(changed.data["metrics"]["result_cache_hit"])
            self.assertEqual(changed.data["results"][0]["original_filename"], "waterfall.jpg")
            file.delete()
            deleted = self.client.post("/api/search/", {"query": "burger", "mode": "local"}, format="json")
            self.assertEqual(deleted.data["results"], [])

    def test_result_cache_does_not_cross_filters_or_users(self):
        self.add_file("burger.jpg", "IMAGE", "visual")
        first = self.client.post("/api/search/", {"query": "burger", "mode": "local"}, format="json")
        filtered = self.client.post("/api/search/", {"query": "burger", "mode": "local", "file_type": "DOCUMENT"}, format="json")
        self.assertEqual(len(first.data["results"]), 1)
        self.assertEqual(filtered.data["results"], [])
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + create_token("bob"))
        other = self.client.post("/api/search/", {"query": "burger", "mode": "local"}, format="json")
        self.assertEqual(other.data["results"], [])

    def test_grid_thumbnail_does_not_download_original_when_derivative_missing(self):
        self.add_file("burger.jpg", "IMAGE", "visual")
        response = self.client.get("/api/media/file/burger.jpg/?thumbnail=true")
        self.assertEqual(response.status_code, 404)
