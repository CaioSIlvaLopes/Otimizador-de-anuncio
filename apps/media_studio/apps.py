"""Configuração do app media_studio."""
from django.apps import AppConfig


class MediaStudioConfig(AppConfig):
    """Configuração do módulo de estúdio local de mídia."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.media_studio"
    verbose_name = "Estúdio de Mídia"
