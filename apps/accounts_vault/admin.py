"""Configuração segura do Django Admin para MarketplaceAccount (sem exposição de tokens)."""
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html

from .models import ChannelChoices, MarketplaceAccount


@admin.register(MarketplaceAccount)
class MarketplaceAccountAdmin(admin.ModelAdmin):
    """Admin blindado: oculta tokens criptografados/brutos e gerencia o circuit-breaker."""

    exclude = ("encrypted_access_token", "encrypted_refresh_token")
    list_display = (
        "id",
        "name",
        "channel",
        "seller_id",
        "is_active",
        "token_validity_status",
        "circuit_breaker_badge",
        "consecutive_errors",
        "max_daily_patches",
        "oauth_connect_link",
    )
    list_filter = ("channel", "is_active", "is_paused_by_circuit_breaker")
    search_fields = ("name", "seller_id")
    readonly_fields = (
        "seller_id",
        "token_expires_at",
        "token_validity_status",
        "circuit_breaker_badge",
        "consecutive_errors",
        "is_paused_by_circuit_breaker",
        "created_at",
        "updated_at",
    )
    actions = ["reset_circuit_breaker_action"]

    @admin.display(description="Validade do Token")
    def token_validity_status(self, obj: MarketplaceAccount) -> str:
        """Exibe se o token está válido, próximo de expirar ou não conectado."""
        if not obj.encrypted_access_token:
            return "Não conectado"
        if obj.is_token_expiring_soon():
            return "Expirado / Renovação pendente"
        return f"Válido até {obj.token_expires_at:%d/%m/%Y %H:%M}"

    @admin.display(description="Circuit-Breaker")
    def circuit_breaker_badge(self, obj: MarketplaceAccount) -> str:
        """Exibe o estado operacional do circuit-breaker da conta."""
        if obj.is_paused_by_circuit_breaker:
            return format_html(
                '<strong style="color: #f97316;">PAUSADO ({} erros)</strong>',
                obj.consecutive_errors,
            )
        return format_html('<span style="color: #3b82f6;">OK</span>')

    @admin.display(description="Conexão OAuth")
    def oauth_connect_link(self, obj: MarketplaceAccount) -> str:
        """Exibe link direto para iniciar autorização OAuth2 do Mercado Livre."""
        if obj.pk and obj.channel == ChannelChoices.MERCADO_LIVRE:
            url = f"{reverse('accounts_vault:ml_connect')}?account_id={obj.pk}"
            return format_html(
                '<a class="button" href="{}">Conectar ML</a>',
                url,
            )
        return "-"

    @admin.action(description="Resetar Circuit-Breaker")
    def reset_circuit_breaker_action(
        self,
        request: HttpRequest,
        queryset: QuerySet[MarketplaceAccount],
    ) -> None:
        """Zera erros consecutivos e reativa contas pausadas pelo circuit-breaker."""
        count = 0
        for account in queryset:
            account.reset_circuit_breaker()
            count += 1
        self.message_user(
            request,
            f"Circuit-Breaker resetado com sucesso para {count} conta(s).",
            level=messages.SUCCESS,
        )
