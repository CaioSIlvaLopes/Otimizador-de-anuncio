"""Configuração do Django Admin para PromptConfig e DailyQuotaLog (paleta Azul #3b82f6 / Laranja #f97316)."""
from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html

from .models import DailyQuotaLog, PromptConfig


@admin.register(PromptConfig)
class PromptConfigAdmin(admin.ModelAdmin):
    """Gerenciamento de prompts versionados e blocklists de termos proibidos."""

    list_display = (
        "id",
        "agent_name",
        "channel",
        "version",
        "active_badge",
        "forbidden_terms_count",
        "updated_at",
    )
    list_filter = ("agent_name", "channel", "is_active")
    search_fields = ("agent_name", "system_prompt")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Status")
    def active_badge(self, obj: PromptConfig) -> str:
        if obj.is_active:
            return format_html('<strong style="color: #3b82f6;">ATIVO</strong>')
        return format_html('<span style="color: #f97316;">INATIVO</span>')

    @admin.display(description="Termos Proibidos")
    def forbidden_terms_count(self, obj: PromptConfig) -> int:
        if isinstance(obj.forbidden_terms, list):
            return len(obj.forbidden_terms)
        return 0


@admin.register(DailyQuotaLog)
class DailyQuotaLogAdmin(admin.ModelAdmin):
    """Monitoramento diário de consumo de tokens LLM e orçamento."""

    list_display = (
        "date",
        "llm_calls_count",
        "llm_tokens_input",
        "llm_tokens_output",
        "budget_status_badge",
        "media_jobs_run",
    )
    readonly_fields = (
        "date",
        "llm_tokens_input",
        "llm_tokens_output",
        "llm_calls_count",
        "media_jobs_run",
        "patches_sent_by_account",
        "budget_status_badge",
    )

    @admin.display(description="Orçamento Diário LLM")
    def budget_status_badge(self, obj: DailyQuotaLog) -> str:
        limit = int(getattr(settings, "DAILY_LLM_TOKEN_LIMIT", 500000))
        total = obj.total_llm_tokens
        if obj.has_llm_budget():
            return format_html(
                '<span style="color: #3b82f6;">{} / {} tokens (OK)</span>',
                total,
                limit,
            )
        return format_html(
            '<strong style="color: #f97316;">{} / {} tokens (ESGOTADO)</strong>',
            total,
            limit,
        )
