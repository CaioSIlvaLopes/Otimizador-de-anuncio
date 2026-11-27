"""Comando idempotente para popular os 4 prompts iniciais calibrados (Mercado Livre + WeDrop)."""
from typing import Any

from django.core.management.base import BaseCommand

from apps.governance.defaults import DEFAULT_PROMPTS
from apps.governance.models import PromptConfig


class Command(BaseCommand):
    """Popula ou atualiza os PromptConfigs iniciais dos 4 sub-agentes."""

    help = "Popula de forma idempotente os 4 prompts padrão (EXTRACTOR, ATTRIBUTES, SEO_COPY, AUDITOR)."

    def handle(self, *args: Any, **options: Any) -> None:
        created_count = 0
        updated_count = 0

        for item in DEFAULT_PROMPTS:
            _, created = PromptConfig.objects.update_or_create(
                agent_name=item["agent_name"],
                channel=item["channel"],
                version=item["version"],
                defaults={
                    "system_prompt": item["system_prompt"],
                    "forbidden_terms": item["forbidden_terms"],
                    "is_active": True,
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed de prompts concluído: {created_count} criado(s), {updated_count} atualizado(s)."
            )
        )
