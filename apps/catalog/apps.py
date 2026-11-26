"""Configuração do app catalog."""
from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Configuração do módulo de catálogo de anúncios e snapshots."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    verbose_name = "Catálogo e Snapshots"
