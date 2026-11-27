"""Configuração do Celery com isolamento estrito entre queue_fast e queue_media."""
import os

from celery import Celery
from kombu import Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

app = Celery("core", broker=REDIS_URL, backend=REDIS_URL)

# Carrega configurações prefixadas com CELERY_ do django.conf.settings
app.config_from_object("django.conf:settings", namespace="CELERY")

# Garante isolamento explícito de filas, roteamento automático e agendamento Beat
app.conf.update(
    broker_url=REDIS_URL,
    result_backend=REDIS_URL,
    task_default_queue="queue_fast",
    task_queues=(
        Queue("queue_fast"),
        Queue("queue_media"),
    ),
    task_routes={
        "apps.media_studio.*": {"queue": "queue_media"},
        "apps.media_studio.tasks.*": {"queue": "queue_media"},
        "*": {"queue": "queue_fast"},
    },
    beat_schedule={
        "watcher-sync-active-accounts-every-10-min": {
            "task": "apps.catalog.tasks.watch_all_active_accounts",
            "schedule": 600.0,
            "options": {"queue": "queue_fast"},
        },
    },
    imports=("core.tasks",),
)

# Descobre tasks automaticamente em todos os apps registrados em INSTALLED_APPS
app.autodiscover_tasks()
