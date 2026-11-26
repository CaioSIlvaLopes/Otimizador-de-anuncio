"""Views de diagnóstico e saúde do sistema."""
from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health_check(request: HttpRequest) -> JsonResponse:
    """Retorna o status operacional do Django e da trava global de escrita."""
    return JsonResponse(
        {
            "status": "ok",
            "service": "catalog_engine",
            "marketplace_write_enabled": bool(
                getattr(settings, "MARKETPLACE_WRITE_ENABLED", False)
            ),
        }
    )
