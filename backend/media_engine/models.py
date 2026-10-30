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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["state", "next_retry_at", "lease_until"])]


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

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user_id", "provider", "operation", "day"], name="unique_daily_usage")]
