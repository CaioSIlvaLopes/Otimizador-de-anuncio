"""Tasks de diagnóstico e infraestrutura do pacote core."""
from celery import shared_task
from django.conf import settings
from django.utils import timezone


@shared_task(name="core.tasks.ping_fast", queue="queue_fast")
def ping_fast() -> dict:
    """Task simples de diagnóstico executada na fila rápida (queue_fast)."""
    return {
        "status": "pong",
        "queue": "queue_fast",
        "marketplace_write_enabled": bool(
            getattr(settings, "MARKETPLACE_WRITE_ENABLED", False)
        ),
        "timestamp": timezone.now().isoformat(),
    }
