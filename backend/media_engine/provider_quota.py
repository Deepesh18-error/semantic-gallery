"""Project/model quota reservations shared by searches, workers and enrichment."""
import re
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Count, F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import ProviderCall, ProviderQuota

PACIFIC = ZoneInfo("America/Los_Angeles")
log = logging.getLogger(__name__)


class QuotaError(RuntimeError):
    def __init__(self, category, retry_seconds=0):
        super().__init__(category)
        self.category = category
        self.retry_seconds = retry_seconds


def classify_provider_error(error):
    message = str(error).lower()
    if any(part in message for part in ("invalid api key", "api key not valid", "unauthenticated", "permission_denied", "401", "403")):
        return "credentials"
    if any(part in message for part in ("daily quota", "requests per day", "rpd", "per_day")):
        return "daily_budget"
    if any(part in message for part in ("429", "resource_exhausted", "rate limit", "quota exceeded")):
        return "rate_limit"
    if any(part in message for part in ("invalid argument", "bad request", "400 ", "400:", "unsupported media")):
        return "provider_rejected"
    if any(part in message for part in ("timeout", "connection", "unavailable", "503", "502", "500", "dns")):
        return "network"
    return "provider_error"


def retry_after(error):
    match = re.search(r"retry(?:[-_ ]after| in)?[^0-9]{0,30}(\d+(?:\.\d+)?)\s*s", str(error), re.I)
    return max(1, min(3600, int(float(match.group(1))))) if match else 60


def _pacific_day_start(now):
    local = now.astimezone(PACIFIC)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(dt_timezone.utc)


def _next_pacific_midnight(now):
    local = now.astimezone(PACIFIC)
    next_day = (local + timedelta(days=1)).date()
    return datetime.combine(next_day, datetime.min.time(), PACIFIC).astimezone(dt_timezone.utc)


def reserve(model, operation, estimated_input_tokens, *, estimated_output_tokens=0, user_id="", priority="background"):
    """Reserve before any network call. A quota-row write serializes SQLite callers."""
    import os
    project_id = os.getenv("GEMINI_PROJECT_ID", "").strip()
    if not project_id:
        raise QuotaError("quota_unconfigured")
    if estimated_input_tokens < 0 or estimated_output_tokens < 0:
        raise ValueError("Estimated tokens must be nonnegative")
    quota = ProviderQuota.objects.filter(project_id=project_id, model=model, enabled=True).first()
    if quota is None:
        raise QuotaError("quota_unconfigured")
    with transaction.atomic():
        # This UPDATE takes SQLite's write lock before counting reservations.
        ProviderQuota.objects.filter(pk=quota.pk).update(block_reason=F("block_reason"))
        quota.refresh_from_db()
        if not quota.enabled:
            raise QuotaError("quota_unconfigured")
        now = timezone.now()
        if quota.blocked_until and quota.blocked_until > now:
            raise QuotaError(quota.block_reason or "rate_limit", int((quota.blocked_until - now).total_seconds()) + 1)
        minute_start = now - timedelta(seconds=60)
        minute = ProviderCall.objects.filter(quota=quota, reserved_at__gte=minute_start).aggregate(
            calls=Count("id"), tokens=Sum(Coalesce("actual_input_tokens", "estimated_input_tokens")))
        day_start = _pacific_day_start(now)
        day_calls = ProviderCall.objects.filter(quota=quota, reserved_at__gte=day_start).count()
        background = priority != "interactive"
        rpm = max(1, int(quota.rpm * (0.75 if background else 1)))
        tpm = max(1, int(quota.input_tpm * (0.75 if background else 1)))
        rpd = max(1, int(quota.rpd * (0.9 if background else 1)))
        if estimated_input_tokens > quota.input_tpm:
            raise QuotaError("input_too_large")
        if background and estimated_input_tokens > tpm:
            raise QuotaError("input_too_large")
        if day_calls >= rpd:
            raise QuotaError("daily_budget", int((_next_pacific_midnight(now) - now).total_seconds()) + 1)
        if minute["calls"] >= rpm or (minute["tokens"] or 0) + estimated_input_tokens > tpm:
            oldest = ProviderCall.objects.filter(quota=quota, reserved_at__gte=minute_start).order_by("reserved_at").first()
            delay = max(1, int((oldest.reserved_at + timedelta(seconds=60) - now).total_seconds()) + 1) if oldest else 60
            raise QuotaError("rate_limit", delay)
        estimate = (Decimal(estimated_input_tokens) * quota.input_usd_per_million +
                    Decimal(estimated_output_tokens) * quota.output_usd_per_million) / Decimal(1_000_000)
        if quota.daily_spend_usd is not None:
            spent = ProviderCall.objects.filter(quota__project_id=project_id,
                reserved_at__gte=day_start).aggregate(total=Sum("estimated_spend_usd"))["total"] or Decimal(0)
            if spent + estimate > quota.daily_spend_usd:
                raise QuotaError("daily_budget", int((_next_pacific_midnight(now) - now).total_seconds()) + 1)
        return ProviderCall.objects.create(id=str(uuid4()), quota=quota, user_id=user_id,
            operation=operation, priority=priority, estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            estimated_spend_usd=estimate)


def finish(call, *, response=None, error=None):
    from .catalog_store import SQLiteCatalog
    usage = getattr(response, "usage_metadata", None) if response is not None else None
    input_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)
    category = classify_provider_error(error) if error else ""
    actual_input = input_tokens if input_tokens is not None else call.estimated_input_tokens
    actual_output = output_tokens if output_tokens is not None else call.estimated_output_tokens
    cost = (Decimal(actual_input) * call.quota.input_usd_per_million +
            Decimal(actual_output) * call.quota.output_usd_per_million) / Decimal(1_000_000)
    ProviderCall.objects.filter(pk=call.pk).update(
        state="failed" if error else "completed", error_category=category,
        actual_input_tokens=actual_input, actual_output_tokens=actual_output,
        estimated_spend_usd=cost, completed_at=timezone.now())
    try:
        SQLiteCatalog().record_usage(call.user_id or "project", "gemini", call.operation,
            input_units=actual_input, output_units=actual_output, estimated_spend_usd=cost)
    except Exception:
        log.exception("Usage aggregate update failed; durable ProviderCall remains authoritative")
    if category in ("rate_limit", "credentials", "daily_budget"):
        delay = retry_after(error) if category == "rate_limit" else (
            int((_next_pacific_midnight(timezone.now()) - timezone.now()).total_seconds())
            if category == "daily_budget" else 24 * 3600)
        ProviderQuota.objects.filter(pk=call.quota_id).update(blocked_until=timezone.now() + timedelta(seconds=delay),
                                                               block_reason=category)


def call_with_quota(model, operation, estimated_input_tokens, fn, *, estimated_output_tokens=0,
                    user_id="", priority="background", on_send=None):
    call = reserve(model, operation, estimated_input_tokens,
                   estimated_output_tokens=estimated_output_tokens, user_id=user_id, priority=priority)
    try:
        if on_send:
            on_send()
        response = fn()
    except Exception as error:
        try:
            finish(call, error=error)
        except Exception:
            log.exception("Could not finalize failed provider call %s", call.id)
        raise
    try:
        finish(call, response=response)
    except Exception:
        # Never repeat a successful external call because accounting failed.
        log.exception("Could not finalize successful provider call %s", call.id)
    return response
