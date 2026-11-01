"""Local exact vector and full-text retrieval backed by the SQLite catalog."""
from array import array
from math import sqrt, sumprod
from heapq import heappush, heapreplace

from django.db import connection

from .models import CatalogSegment, CatalogVectorRecord


class SQLiteExactVectors:
    """Exact cosine search. Stored float32 vectors survive process restart."""
    backend = "sqlite_exact"

    def upsert(self, *, vector_id, segment_id, values, model_config, generation=1,
               expected_dimensions=None):
        vector = array("f", values)
        if expected_dimensions is not None and len(vector) != expected_dimensions:
            raise ValueError("Vector dimensions do not match the index configuration")
        if not vector or not all(float("-inf") < v < float("inf") for v in vector):
            raise ValueError("Vector must contain finite values")
        norm = sqrt(sumprod(vector, vector))
        if not norm:
            raise ValueError("Vector must be nonzero")
        vector = array("f", (value / norm for value in vector))
        segment = CatalogSegment.objects.get(pk=segment_id)
        if segment.file_version.file.current_version_id != segment.file_version_id:
            raise ValueError("Cannot index a stale file version")
        record, _ = CatalogVectorRecord.objects.update_or_create(
            id=vector_id, defaults={"segment": segment, "backend": self.backend,
                                    "model_config": model_config, "index_generation": generation,
                                    "dimensions": len(vector), "vector_blob": vector.tobytes()})
        return record

    def query(self, values, *, user_id, model_config, collection_id=None, source_type=None,
              limit=20, generation=1, expected_dimensions=None):
        needle = array("f", values)
        if expected_dimensions is not None and len(needle) != expected_dimensions:
            raise ValueError("Query dimensions do not match the index configuration")
        norm = sqrt(sumprod(needle, needle)) if needle else 0
        if not needle or norm == 0:
            raise ValueError("Query vector must be nonzero")
        if limit < 1:
            raise ValueError("limit must be positive")
        needle = array("f", (value / norm for value in needle))
        sql = """SELECT r.id, r.segment_id, v.file_id, r.vector_blob
                 FROM media_engine_catalogvectorrecord r
                 JOIN media_engine_catalogsegment s ON s.id = r.segment_id
                 JOIN media_engine_catalogfileversion v ON v.id = s.file_version_id
                 JOIN media_engine_catalogfile f ON f.id = v.file_id
                 WHERE r.backend = %s AND r.model_config = %s AND r.index_generation = %s
                   AND r.dimensions = %s AND r.vector_blob IS NOT NULL
                   AND f.user_id = %s AND f.available = 1 AND f.current_version_id = v.id"""
        params = [self.backend, model_config, generation, len(needle), user_id]
        if source_type:
            sql += " AND f.source_type = %s"
            params.append(source_type)
        if collection_id:
            sql += " AND EXISTS (SELECT 1 FROM media_engine_catalogmembership m WHERE m.file_id = f.id AND m.collection_id = %s)"
            params.append(collection_id)
        heap = []
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            while rows := cursor.fetchmany(128):
                for vector_id, segment_id, file_id, blob in rows:
                    vector = array("f")
                    vector.frombytes(blob)
                    score = sumprod(needle, vector)
                    candidate = (score, vector_id, segment_id, file_id)
                    if len(heap) < limit:
                        heappush(heap, candidate)
                    elif candidate > heap[0]:
                        heapreplace(heap, candidate)
        return [{"vector_id": vector_id, "segment_id": segment_id,
                 "file_id": file_id, "score": score}
                for score, vector_id, segment_id, file_id in sorted(heap, reverse=True)]

    def delete(self, vector_id):
        return CatalogVectorRecord.objects.filter(pk=vector_id, backend=self.backend).delete()[0]


class SQLiteFullText:
    def search(self, query, *, user_id, collection_id=None, source_type=None, limit=20):
        terms = [part.strip('"') for part in query.split() if part.strip('"')]
        if not terms:
            return []
        expression = " ".join('"' + term.replace('"', '""') + '"' for term in terms)
        sql = """SELECT s.id, s.file_version_id, bm25(catalog_segment_fts) AS rank
                 FROM catalog_segment_fts JOIN media_engine_catalogsegment s
                   ON s.id = catalog_segment_fts.rowid
                 JOIN media_engine_catalogfileversion v ON v.id = s.file_version_id
                 JOIN media_engine_catalogfile f ON f.id = v.file_id
                 WHERE catalog_segment_fts MATCH %s AND f.user_id = %s
                   AND f.available = 1 AND f.current_version_id = v.id"""
        params = [expression, user_id]
        if source_type:
            sql += " AND f.source_type = %s"
            params.append(source_type)
        if collection_id:
            sql += " AND EXISTS (SELECT 1 FROM media_engine_catalogmembership m WHERE m.file_id = f.id AND m.collection_id = %s)"
            params.append(collection_id)
        sql += " ORDER BY rank LIMIT %s"
        params.append(limit)
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [{"segment_id": row[0], "file_version_id": row[1], "rank": row[2]} for row in cursor.fetchall()]
