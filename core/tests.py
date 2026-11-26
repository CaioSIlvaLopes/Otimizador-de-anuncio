"""Testes unitários da fundação (health check, trava global e roteamento de filas Celery)."""
from django.conf import settings
from django.test import SimpleTestCase

from core.celery import app as celery_app
from core.tasks import ping_fast


class CoreFoundationTests(SimpleTestCase):
    """Valida as garantias arquiteturais da Fase 0."""

    def test_marketplace_write_disabled_by_default(self) -> None:
        """A trava global MARKETPLACE_WRITE_ENABLED deve iniciar estritamente False."""
        self.assertFalse(settings.MARKETPLACE_WRITE_ENABLED)

    def test_health_endpoint_returns_json_status(self) -> None:
        """O endpoint GET /health/ deve retornar HTTP 200 com status e flag de escrita."""
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertIsInstance(payload["marketplace_write_enabled"], bool)
        self.assertFalse(payload["marketplace_write_enabled"])

    def test_celery_queues_and_routing(self) -> None:
        """Garante isolamento entre queue_fast e queue_media."""
        queue_names = {q.name for q in celery_app.conf.task_queues}
        self.assertIn("queue_fast", queue_names)
        self.assertIn("queue_media", queue_names)
        self.assertEqual(celery_app.conf.task_default_queue, "queue_fast")
        self.assertEqual(
            celery_app.conf.task_routes["apps.media_studio.*"]["queue"],
            "queue_media",
        )

    def test_ping_fast_task_execution(self) -> None:
        """Valida execução síncrona local da task de diagnóstico core.tasks.ping_fast."""
        result = ping_fast.apply().get()
        self.assertEqual(result["status"], "pong")
        self.assertEqual(result["queue"], "queue_fast")
        self.assertFalse(result["marketplace_write_enabled"])
