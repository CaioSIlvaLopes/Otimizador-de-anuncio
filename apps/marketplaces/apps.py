"""Configuração do app marketplaces."""
from django.apps import AppConfig


class MarketplacesConfig(AppConfig):
    """Configuração do módulo de integrações com marketplaces."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.marketplaces"
    verbose_name = "Integrações de Marketplaces"
