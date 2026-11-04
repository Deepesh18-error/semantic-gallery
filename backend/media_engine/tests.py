from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from .contracts import SearchRequest, SearchValidationError, coverage_for_item, representation_for_vector, source_matches_filter
from . import views


class ContractTests(SimpleTestCase):
    def test_request_validation(self):
        self.assertEqual(SearchRequest.parse({"query": "  burger ", "file_type": "ALL"}).query, "burger")
        for body in ({"query": ""}, {"query": "x" * 501}, {"query": "ok", "limit": 0},
                     {"query": "ok", "limit": True}, {"query": "ok", "file_type": "PDF"},
                     {"query": "ok", "collection_id": 123}):
            with self.assertRaises(SearchValidationError):
                SearchRequest.parse(body)

    def test_document_representation_is_not_source_type(self):
        self.assertEqual(representation_for_vector({"file_type": "TEXT", "vector_type": "content"}), "text")
        self.assertTrue(source_matches_filter({"media_type": "DOCUMENT"}, "DOCUMENT"))
        self.assertFalse(source_matches_filter({"media_type": "TEXT"}, "DOCUMENT"))
        self.assertFalse(source_matches_filter({"media_type": "DOCUMENT"}, "IMAGE"))
        self.assertTrue(coverage_for_item({"processing_status": "PARTIALLY_EMBEDDED", "total_vectors": 2, "failed_chunks": [3]})["partial"])


class AccessTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.auth = {"HTTP_AUTHORIZATION": "Bearer test-token"}

    def test_file_lookup_is_scoped_to_user(self):
        with patch("media_engine.auth_service.jwt.decode", return_value={"user_id": "alice"}), patch.object(views, "db") as db:
            db.media_items.find_one.return_value = None
            response = views.serve_media_file(self.factory.get("/api/media/file/file-1/", **self.auth), "file-1")
            self.assertEqual(response.status_code, 404)
            db.media_items.find_one.assert_called_once_with({"_id": "file-1", "user_id": "alice"})

    def test_search_rejects_other_users_collection_before_provider_call(self):
        with patch("media_engine.auth_service.jwt.decode", return_value={"user_id": "alice"}), patch.object(views, "db") as db, patch.object(views, "execute_search") as search:
            db.collections.find_one.return_value = None
            request = self.factory.post("/api/search/", {"query": "AWS", "collection_id": "other"}, format="json", **self.auth)
            response = views.search_media(request)
            self.assertEqual(response.status_code, 404)
            search.assert_not_called()
            db.collections.find_one.assert_called_once_with({"_id": "other", "user_id": "alice"}, {"_id": 1})

    def test_invalid_search_avoids_provider_call(self):
        with patch("media_engine.auth_service.jwt.decode", return_value={"user_id": "alice"}), patch.object(views, "execute_search") as search:
            request = self.factory.post("/api/search/", {"query": "x" * 501}, format="json", **self.auth)
            response = views.search_media(request)
            self.assertEqual(response.status_code, 400)
            search.assert_not_called()

    def test_provider_limit_has_distinct_error(self):
        with patch("media_engine.auth_service.jwt.decode", return_value={"user_id": "alice"}), patch.object(views, "execute_search", side_effect=RuntimeError("429 RESOURCE_EXHAUSTED")):
            request = self.factory.post("/api/search/", {"query": "burger"}, format="json", **self.auth)
            response = views.search_media(request)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.data["code"], "RATE_LIMITED")

    def test_legacy_local_suggestions_never_call_search_provider(self):
        with patch("media_engine.auth_service.jwt.decode", return_value={"user_id": "alice"}), \
             patch.object(views, "db") as db, patch.object(views, "execute_search") as search:
            db.media_items.find.return_value.limit.return_value = []
            request = self.factory.post("/api/search/", {"query": "burger", "mode": "local"}, format="json", **self.auth)
            response = views.search_media(request)
            self.assertEqual(response.status_code, 200)
            search.assert_not_called()
