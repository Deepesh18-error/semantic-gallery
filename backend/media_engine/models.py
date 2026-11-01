"""Durable desktop catalog. Media bytes and derivatives remain on disk."""
from django.db import models


class CatalogAccount(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255)
    password_hash = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class CatalogSource(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    user_id = models.CharField(max_length=128, db_index=True)
    kind = models.CharField(max_length=24)
    root_path = models.TextField()
    state = models.CharField(max_length=24, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user_id", "kind", "root_path"], name="unique_catalog_source")]


class CatalogCollection(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    user_id = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    theme_color = models.CharField(max_length=32, default="#3498db")
    icon_tag = models.CharField(max_length=64, default="folder")
    created_at = models.DateTimeField(auto_now_add=True)


class CatalogFile(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    user_id = models.CharField(max_length=128, db_index=True)
    source = models.ForeignKey(CatalogSource, on_delete=models.PROTECT, related_name="files")
    path = models.TextField()
    original_name = models.TextField()
    source_type = models.CharField(max_length=16, db_index=True)
    mime_type = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField(default=0)
    modified_ns = models.BigIntegerField(null=True)
    available = models.BooleanField(default=True)
    processing_status = models.CharField(max_length=32, default="PENDING")
    error_message = models.TextField(blank=True)
    thumbnail_path = models.TextField(blank=True)
    ai_description = models.TextField(blank=True)
    description_status = models.CharField(max_length=32, default="PENDING")
    current_version = models.ForeignKey("CatalogFileVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    collections = models.ManyToManyField(CatalogCollection, through="CatalogMembership", related_name="files")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user_id", "source_type", "available"])]
        constraints = [models.UniqueConstraint(fields=["source", "path"], name="unique_source_file_path")]


class CatalogFileVersion(models.Model):
    file = models.ForeignKey(CatalogFile, on_delete=models.CASCADE, related_name="versions")
    generation = models.PositiveIntegerField()
    fingerprint = models.CharField(max_length=128, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    modified_ns = models.BigIntegerField(null=True)
    extraction_coverage = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["file", "generation"], name="unique_file_generation")]


class CatalogMembership(models.Model):
    collection = models.ForeignKey(CatalogCollection, on_delete=models.CASCADE)
    file = models.ForeignKey(CatalogFile, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["collection", "file"], name="unique_catalog_membership")]


class CatalogSegment(models.Model):
    file_version = models.ForeignKey(CatalogFileVersion, on_delete=models.CASCADE, related_name="segments")
    ordinal = models.PositiveIntegerField()
    representation = models.CharField(max_length=32)
    text = models.TextField(blank=True)
    derivative_path = models.TextField(blank=True)
    page_number = models.PositiveIntegerField(null=True)
    source_start = models.PositiveIntegerField(null=True)
    source_end = models.PositiveIntegerField(null=True)
    heading = models.TextField(blank=True)
    title = models.TextField(blank=True)
    ocr_confidence = models.FloatField(null=True)
    start_seconds = models.FloatField(null=True)
    end_seconds = models.FloatField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["file_version", "representation", "ordinal"], name="unique_version_segment")]


class CatalogVectorRecord(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    segment = models.ForeignKey(CatalogSegment, on_delete=models.CASCADE, related_name="vectors")
    backend = models.CharField(max_length=32)
    model_config = models.CharField(max_length=255)
    index_generation = models.PositiveIntegerField(default=1)
    dimensions = models.PositiveIntegerField()
    vector_blob = models.BinaryField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["backend", "model_config", "index_generation"])]


class CatalogEmbeddingCache(models.Model):
    user_id = models.CharField(max_length=128)
    model_config = models.CharField(max_length=255)
    content_digest = models.CharField(max_length=64)
    dimensions = models.PositiveIntegerField()
    vector_blob = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user_id", "model_config", "content_digest"],
                                               name="unique_embedding_cache_key")]


class CatalogJob(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    file_version = models.ForeignKey(CatalogFileVersion, on_delete=models.CASCADE, related_name="jobs")
    stage = models.CharField(max_length=32)
    state = models.CharField(max_length=24, default="queued")
    attempts = models.PositiveIntegerField(default=0)
    progress_done = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(default=0)
    lease_owner = models.CharField(max_length=128, blank=True)
    lease_until = models.DateTimeField(null=True)
    next_retry_at = models.DateTimeField(null=True)
    error = models.TextField(blank=True)
    error_category = models.CharField(max_length=40, blank=True)
    priority = models.PositiveSmallIntegerField(default=50)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    pause_requested = models.BooleanField(default=False)
    cancel_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["state", "next_retry_at", "lease_until"])]
        constraints = [models.UniqueConstraint(fields=["file_version"],
            condition=models.Q(state__in=["queued", "running", "paused"]),
            name="unique_active_file_version_job")]


class CatalogSetting(models.Model):
    user_id = models.CharField(max_length=128)
    key = models.CharField(max_length=128)
    value = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user_id", "key"], name="unique_catalog_setting")]


class CatalogUsage(models.Model):
    user_id = models.CharField(max_length=128)
    provider = models.CharField(max_length=64)
    operation = models.CharField(max_length=64)
    day = models.DateField()
    calls = models.PositiveIntegerField(default=0)
    input_units = models.BigIntegerField(default=0)
    output_units = models.BigIntegerField(default=0)
    estimated_spend_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user_id", "provider", "operation", "day"], name="unique_daily_usage")]


class ProviderQuota(models.Model):
    project_id = models.CharField(max_length=128)
    model = models.CharField(max_length=128)
    rpm = models.PositiveIntegerField()
    input_tpm = models.PositiveIntegerField()
    rpd = models.PositiveIntegerField()
    daily_spend_usd = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    input_usd_per_million = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    output_usd_per_million = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    enabled = models.BooleanField(default=True)
    blocked_until = models.DateTimeField(null=True)
    block_reason = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project_id", "model"], name="unique_project_model_quota")]


class ProviderCall(models.Model):
    id = models.CharField(max_length=128, primary_key=True)
    quota = models.ForeignKey(ProviderQuota, on_delete=models.CASCADE, related_name="calls")
    user_id = models.CharField(max_length=128, blank=True)
    operation = models.CharField(max_length=64)
    priority = models.CharField(max_length=16)
    estimated_input_tokens = models.PositiveIntegerField(default=0)
    estimated_output_tokens = models.PositiveIntegerField(default=0)
    actual_input_tokens = models.PositiveIntegerField(null=True)
    actual_output_tokens = models.PositiveIntegerField(null=True)
    estimated_spend_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    state = models.CharField(max_length=20, default="reserved")
    error_category = models.CharField(max_length=40, blank=True)
    reserved_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["quota", "reserved_at"])]
