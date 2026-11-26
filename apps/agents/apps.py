"""Configuração do app agents."""
from django.apps import AppConfig


class AgentsConfig(AppConfig):
    """Configuração do módulo de sub-agentes de IA."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agents"
    verbose_name = "Sub-Agentes de IA"
