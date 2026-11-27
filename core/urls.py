"""Mapeamento principal de URLs do projeto."""
from django.contrib import admin
from django.urls import include, path

from .views import health_check

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health_check"),
    path("vault/", include("apps.accounts_vault.urls")),
    path("dashboard/", include("apps.governance.urls")),
]
