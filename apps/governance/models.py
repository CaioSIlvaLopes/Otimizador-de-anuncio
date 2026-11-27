"""Models de governança: PromptConfig (versionamento e blocklists) e DailyQuotaLog (orçamento diário)."""
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.accounts_vault.models import MarketplaceAccount


class AgentNameChoices(models.TextChoices):
    """Identificadores dos 4 sub-agentes do pipeline de otimização."""

    EXTRACTOR = "EXTRACTOR", "Extrator de Fatos (Multimodal)"
    ATTRIBUTES = "ATTRIBUTES", "Preenchedor de Ficha Técnica"
    SEO_COPY = "SEO_COPY", "Especialista SEO & Copywriting"
    AUDITOR = "AUDITOR", "Auditor de Qualidade & Anti-Alucinação"


class PromptChannelChoices(models.TextChoices):
    """Escopo de canal para customização de prompts."""

    ALL = "ALL", "Todos os Canais (Padrão)"
    MERCADO_LIVRE = "MERCADO_LIVRE", "Mercado Livre"
    SHOPEE = "SHOPEE", "Shopee"
    TIKTOK_SHOP = "TIKTOK_SHOP", "TikTok Shop"


class PromptConfig(models.Model):
    """Configuração versionada de prompt de sistema e termos proibidos por sub-agente e canal."""

    agent_name = models.CharField(
        "Sub-Agente",
        max_length=30,
        choices=AgentNameChoices.choices,
    )
    channel = models.CharField(
        "Canal",
        max_length=30,
        choices=PromptChannelChoices.choices,
        default=PromptChannelChoices.ALL,
    )
    version = models.PositiveIntegerField(
        "Versão",
        default=1,
    )
    system_prompt = models.TextField(
        "System Prompt",
    )
    forbidden_terms = models.JSONField(
        "Termos Proibidos (Blocklist)",
        default=list,
        blank=True,
        help_text="Lista de strings proibidas (ex: frete grátis, promoção, envio imediato).",
    )
    is_active = models.BooleanField(
        "Ativo",
        default=True,
    )
    created_at = models.DateTimeField(
        "Criado em",
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        "Atualizado em",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Configuração de Prompt"
        verbose_name_plural = "Configurações de Prompts"
        ordering = ["agent_name", "channel", "-version"]
        indexes = [
            models.Index(
                fields=["agent_name", "channel", "is_active"],
                name="idx_prompt_agent_chan_active",
            ),
        ]

    def __str__(self) -> str:
        state = "Ativo" if self.is_active else "Inativo"
        return f"{self.agent_name} [{self.channel}] v{self.version} ({state})"

    @classmethod
    def get_active(
        cls,
        agent_name: str,
        channel: str = PromptChannelChoices.ALL,
    ) -> "PromptConfig | None":
        """Busca a configuração ativa específica do canal com fallback automático para channel='ALL'."""
        specific = (
            cls.objects.filter(
                agent_name=agent_name,
                channel=channel,
                is_active=True,
            )
            .order_by("-version", "-id")
            .first()
        )
        if specific is not None:
            return specific

        if channel != PromptChannelChoices.ALL:
            return (
                cls.objects.filter(
                    agent_name=agent_name,
                    channel=PromptChannelChoices.ALL,
                    is_active=True,
                )
                .order_by("-version", "-id")
                .first()
            )
        return None


class DailyQuotaLog(models.Model):
    """Registro diário de consumo de tokens LLM, jobs de mídia e patches enviados por conta."""

    date = models.DateField(
        "Data",
        unique=True,
        default=timezone.localdate,
    )
    llm_tokens_input = models.PositiveBigIntegerField(
        "Tokens LLM (Input)",
        default=0,
    )
    llm_tokens_output = models.PositiveBigIntegerField(
        "Tokens LLM (Output)",
        default=0,
    )
    llm_calls_count = models.PositiveIntegerField(
        "Total de Chamadas LLM",
        default=0,
    )
    media_jobs_run = models.PositiveIntegerField(
        "Jobs de Mídia Executados",
        default=0,
    )
    patches_sent_by_account = models.JSONField(
        "Patches Enviados por Conta",
        default=dict,
        blank=True,
    )

    class Meta:
        verbose_name = "Log de Cota Diária"
        verbose_name_plural = "Logs de Cotas Diárias"
        ordering = ["-date"]

    def __str__(self) -> str:
        total = self.total_llm_tokens
        return f"Cota {self.date:%d/%m/%Y} — {total} tokens ({self.llm_calls_count} chamadas)"

    @property
    def total_llm_tokens(self) -> int:
        """Soma total de tokens (input + output) consumidos no dia."""
        return int(self.llm_tokens_input or 0) + int(self.llm_tokens_output or 0)

    @classmethod
    def get_today(cls) -> "DailyQuotaLog":
        """Obtém ou cria o registro de cota da data local atual."""
        today = timezone.localdate()
        obj, _ = cls.objects.get_or_create(date=today)
        return obj

    def has_llm_budget(self) -> bool:
        """Verifica se o consumo acumulado do dia está abaixo de settings.DAILY_LLM_TOKEN_LIMIT."""
        limit = int(getattr(settings, "DAILY_LLM_TOKEN_LIMIT", 500000))
        return self.total_llm_tokens < limit

    def record_llm_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Incrementa atomicamente os contadores de tokens e chamadas usando expressões F()."""
        safe_in = max(0, int(input_tokens or 0))
        safe_out = max(0, int(output_tokens or 0))
        type(self).objects.filter(pk=self.pk).update(
            llm_tokens_input=models.F("llm_tokens_input") + safe_in,
            llm_tokens_output=models.F("llm_tokens_output") + safe_out,
            llm_calls_count=models.F("llm_calls_count") + 1,
        )
        self.refresh_from_db()

    def record_media_job(self, count: int = 1) -> None:
        """Incrementa atomicamente o contador diário de jobs de mídia executados."""
        safe_count = max(1, int(count or 1))
        type(self).objects.filter(pk=self.pk).update(
            media_jobs_run=models.F("media_jobs_run") + safe_count,
        )
        self.refresh_from_db()

    def get_account_patches_count(self, account: MarketplaceAccount | int) -> int:
        """Retorna quantos patches foram enviados hoje para a conta especificada."""
        account_id = str(account.pk if hasattr(account, "pk") else account)
        mapping = (
            self.patches_sent_by_account
            if isinstance(self.patches_sent_by_account, dict)
            else {}
        )
        return int(mapping.get(account_id, 0) or 0)

    def can_send_patch(self, account: MarketplaceAccount) -> bool:
        """Verifica se a conta ainda possui saldo dentro de account.max_daily_patches hoje."""
        self.refresh_from_db()
        return self.get_account_patches_count(account) < int(account.max_daily_patches)

    def record_patch_sent(self, account: MarketplaceAccount) -> int:
        """Registra atomicamente o envio de 1 patch para a conta no dia atual."""
        account_key = str(account.pk)
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            mapping = (
                dict(locked.patches_sent_by_account)
                if isinstance(locked.patches_sent_by_account, dict)
                else {}
            )
            new_count = int(mapping.get(account_key, 0) or 0) + 1
            mapping[account_key] = new_count
            locked.patches_sent_by_account = mapping
            locked.save(update_fields=["patches_sent_by_account"])
            self.patches_sent_by_account = mapping
        return new_count
