"""Configuração do app governance."""
from django.apps import AppConfig


class GovernanceConfig(AppConfig):
    """Configuração do módulo de governança, prompts e cotas."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.governance"
    verbose_name = "Governança e Revisão"
