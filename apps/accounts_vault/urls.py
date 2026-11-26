"""Rotas OAuth2 do app accounts_vault."""
from django.urls import path

from .views import ml_callback, ml_connect

app_name = "accounts_vault"

urlpatterns = [
    path("ml/connect/", ml_connect, name="ml_connect"),
    path("ml/callback/", ml_callback, name="ml_callback"),
]
