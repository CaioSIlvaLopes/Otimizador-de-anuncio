"""Rotas do Painel Web de Revisão Antes vs Depois (/dashboard/)."""
from django.urls import path

from .views import dashboard_queue, proposal_detail

app_name = "dashboard"

urlpatterns = [
    path("", dashboard_queue, name="queue"),
    path("proposals/<int:proposal_id>/", proposal_detail, name="proposal_detail"),
]
