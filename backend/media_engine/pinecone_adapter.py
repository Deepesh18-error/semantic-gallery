"""Pinecone comparison backend behind the same vector store contract."""
from .models import CatalogFile, CatalogSegment, CatalogVectorRecord
from .pinecone_service import get_pinecone_index


class PineconeVectors:
    backend = "pinecone"

    def upsert(self, *, vector_id, segment_id, values, model_config, generation=1):
        segment = CatalogSegment.objects.select_related("file_version__file").get(pk=segment_id)
        file = segment.file_version.file
        if file.current_version_id != segment.file_version_id:
            raise ValueError("Cannot index a stale file version")
        collections = list(file.collections.values_list("id", flat=True))
        metadata = {"user_id": file.user_id, "media_item_id": file.id,
                    "source_type": file.source_type, "file_type": file.source_type,
                    "collection_id": collections[0] if collections else "",
                    "segment_id": segment.id, "index_config": model_config,
                    "index_generation": generation}
        get_pinecone_index().upsert(vectors=[(vector_id, list(values), metadata)])
        return CatalogVectorRecord.objects.update_or_create(id=vector_id, defaults={
            "segment": segment, "backend": self.backend, "model_config": model_config,
            "index_generation": generation, "dimensions": len(values), "vector_blob": None})[0]

    def query(self, values, *, user_id, model_config, collection_id=None, source_type=None, limit=20, generation=1):
        metadata_filter = {"user_id": user_id, "index_config": model_config,
                           "index_generation": generation}
        if collection_id:
            metadata_filter["collection_id"] = collection_id
        if source_type:
            metadata_filter["source_type"] = source_type
        matches = get_pinecone_index().query(vector=list(values), filter=metadata_filter,
                                            top_k=limit, include_metadata=True).matches
        ids = [match.metadata.get("media_item_id") for match in matches]
        allowed = {file.id: file for file in CatalogFile.objects.filter(id__in=ids, user_id=user_id, available=True)}
        return [{"vector_id": match.id, "file_id": match.metadata["media_item_id"], "score": match.score,
                 "segment_id": match.metadata.get("segment_id")}
                for match in matches if match.metadata.get("media_item_id") in allowed
                and (not source_type or allowed[match.metadata["media_item_id"]].source_type == source_type)]

    def delete(self, vector_id):
        get_pinecone_index().delete(ids=[vector_id])
        return CatalogVectorRecord.objects.filter(pk=vector_id, backend=self.backend).delete()[0]
