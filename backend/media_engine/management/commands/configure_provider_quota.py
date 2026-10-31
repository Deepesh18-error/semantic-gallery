import os
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from media_engine.models import ProviderQuota


class Command(BaseCommand):
    help = "Store active AI Studio project/model limits; no provider request is made."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", default=None)
        parser.add_argument("--model", required=True)
        parser.add_argument("--rpm", type=int, required=True)
        parser.add_argument("--input-tpm", type=int, required=True)
        parser.add_argument("--rpd", type=int, required=True)
        parser.add_argument("--daily-spend-usd", type=Decimal)
        parser.add_argument("--input-usd-per-million", type=Decimal, default=Decimal(0))
        parser.add_argument("--output-usd-per-million", type=Decimal, default=Decimal(0))

    def handle(self, *args, **options):
        project_id = options["project_id"] or os.getenv("GEMINI_PROJECT_ID")
        if not project_id:
            raise CommandError("Set GEMINI_PROJECT_ID or pass --project-id")
        if any(options[key] <= 0 for key in ("rpm", "input_tpm", "rpd")):
            raise CommandError("RPM, input TPM and RPD must be positive")
        if any(options[key] is not None and options[key] < 0 for key in (
            "daily_spend_usd", "input_usd_per_million", "output_usd_per_million")):
            raise CommandError("Spend and prices cannot be negative")
        row, _ = ProviderQuota.objects.update_or_create(project_id=project_id, model=options["model"], defaults={
            "rpm": options["rpm"], "input_tpm": options["input_tpm"], "rpd": options["rpd"],
            "daily_spend_usd": options["daily_spend_usd"],
            "input_usd_per_million": options["input_usd_per_million"],
            "output_usd_per_million": options["output_usd_per_million"],
            "enabled": True, "blocked_until": None, "block_reason": ""})
        self.stdout.write(self.style.SUCCESS(f"Configured quota for {row.project_id}/{row.model}"))
