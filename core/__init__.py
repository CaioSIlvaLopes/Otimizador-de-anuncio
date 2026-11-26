"""Inicialização do pacote core com carregamento automático do app Celery."""
from .celery import app as celery_app

__all__ = ("celery_app",)
