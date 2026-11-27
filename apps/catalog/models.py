"""Models do app catalog: Listing, ListingSnapshot (imutável) e OptimizationProposal."""
from typing import Any

from django.db import models


class ListingStatus(models.TextChoices):
    """Estados do ciclo de vida do anúncio no motor de otimização."""

    DETECTED = "DETECTED", "Detectado"
    PROCESSING = "PROCESSING", "Processando"
    REVIEW = "REVIEW", "Aguardando Revisão"
    PUBLISHED = "PUBLISHED", "Publicado"
    FAILED = "FAILED", "Falhou"
    IGNORED = "IGNORED", "Ignorado"


class ProposalStatus(models.TextChoices):
    """Estados de revisão e publicação de uma proposta de otimização."""

    PENDING_REVIEW = "PENDING_REVIEW", "Aguardando Revisão"
    APPROVED = "APPROVED", "Aprovada"
    REJECTED = "REJECTED", "Rejeitada"
    PUBLISHED = "PUBLISHED", "Publicada"
    FAILED = "FAILED", "Falhou"


class Listing(models.Model):
    """Anúncio monitorado de uma loja conectada (padrão Watcher + Patch)."""

    account = models.ForeignKey(
        "accounts_vault.MarketplaceAccount",
        on_delete=models.CASCADE,
        related_name="listings",
        verbose_name="Conta de Marketplace",
    )
    external_id = models.CharField(
        "ID Externo no Portal",
        max_length=100,
        help_text="Ex: MLB123456789",
    )
    category_id = models.CharField(
        "ID da Categoria",
        max_length=100,
        blank=True,
    )
    sku_readonly = models.CharField(
        "SKU WeDrop (Somente Leitura)",
        max_length=120,
        blank=True,
        help_text="Extraído de seller_custom_field ou SELLER_SKU apenas para rastreabilidade.",
    )
    original_title = models.CharField(
        "Título Original",
        max_length=255,
        blank=True,
    )
    marketplace_status = models.CharField(
        "Status no Marketplace",
        max_length=40,
        blank=True,
        help_text="Ex: active, paused, closed",
    )
    sold_quantity = models.PositiveIntegerField(
        "Quantidade Vendida",
        default=0,
        help_text="Quando > 0 no Mercado Livre, o campo title fica travado para edição.",
    )
    status = models.CharField(
        "Status no Motor",
        max_length=30,
        choices=ListingStatus.choices,
        default=ListingStatus.DETECTED,
    )
    confidence_score = models.PositiveSmallIntegerField(
        "Nota de Confiança",
        null=True,
        blank=True,
    )
    last_synced_at = models.DateTimeField(
        "Última Sincronização",
        auto_now=True,
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
        verbose_name = "Anúncio (Listing)"
        verbose_name_plural = "Anúncios (Listings)"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "external_id"],
                name="uniq_listing_account_external_id",
            ),
        ]
        indexes = [
            models.Index(
                fields=["account", "status"],
                name="idx_listing_account_status",
            ),
            models.Index(
                fields=["external_id"],
                name="idx_listing_external_id",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.external_id} - {self.original_title or 'Sem título'} ({self.status})"


class ListingSnapshot(models.Model):
    """Snapshot bruto e estritamente imutável do anúncio para auditoria e rollback em 1 clique."""

    IMMUTABLE_ERROR_MESSAGE = (
        "ListingSnapshot é imutável e não pode ser alterado ou excluído."
    )

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="snapshots",
        verbose_name="Anúncio",
    )
    raw_item_json = models.JSONField(
        "Payload Bruto do Item",
    )
    raw_description_json = models.JSONField(
        "Payload Bruto da Descrição",
        default=dict,
    )
    category_attributes_json = models.JSONField(
        "Schema de Atributos da Categoria",
        default=list,
    )
    is_baseline = models.BooleanField(
        "Snapshot Baseline Original",
        default=True,
    )
    captured_at = models.DateTimeField(
        "Capturado em",
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "Snapshot de Anúncio"
        verbose_name_plural = "Snapshots de Anúncios"
        ordering = ["-captured_at"]

    def __str__(self) -> str:
        kind = "Baseline" if self.is_baseline else "Snapshot"
        return f"{kind} #{self.pk} - {self.listing.external_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Impede qualquer alteração (UPDATE) em um snapshot já persistido."""
        if self.pk is not None or not self._state.adding:
            raise ValueError(self.IMMUTABLE_ERROR_MESSAGE)
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Impede exclusão manual direta de um snapshot."""
        raise ValueError(self.IMMUTABLE_ERROR_MESSAGE)


class OptimizationProposal(models.Model):
    """Proposta de enriquecimento gerada pelos 4 sub-agentes vinculada ao snapshot baseline."""

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="proposals",
        verbose_name="Anúncio",
    )
    baseline_snapshot = models.ForeignKey(
        ListingSnapshot,
        on_delete=models.PROTECT,
        related_name="proposals",
        verbose_name="Snapshot Baseline",
    )
    extracted_facts_json = models.JSONField(
        "Fatos Extraídos (ExtractedFacts)",
        default=dict,
    )
    proposed_title = models.CharField(
        "Título Proposto",
        max_length=255,
    )
    proposed_description = models.TextField(
        "Descrição Proposta",
    )
    proposed_attributes_json = models.JSONField(
        "Atributos Propostos",
        default=list,
    )
    card_highlights_json = models.JSONField(
        "Destaques para Cards (Infográficos)",
        default=list,
    )
    video_script_json = models.JSONField(
        "Roteiro de Vídeo",
        default=dict,
    )
    media_assets_json = models.JSONField(
        "Ativos de Mídia Gerados",
        default=dict,
        blank=True,
    )
    auditor_warnings = models.JSONField(
        "Alertas do Auditor",
        default=list,
        blank=True,
    )
    confidence_score = models.PositiveSmallIntegerField(
        "Nota de Confiança (0-100)",
        default=0,
    )
    status = models.CharField(
        "Status da Proposta",
        max_length=30,
        choices=ProposalStatus.choices,
        default=ProposalStatus.PENDING_REVIEW,
    )
    llm_tokens_used = models.PositiveIntegerField(
        "Tokens LLM Consumidos",
        default=0,
    )
    last_patch_payload_json = models.JSONField(
        "Último Payload Sanitizado",
        default=dict,
        blank=True,
    )
    published_at = models.DateTimeField(
        "Publicado em",
        null=True,
        blank=True,
    )
    error_message = models.TextField(
        "Mensagem de Erro",
        blank=True,
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
        verbose_name = "Proposta de Otimização"
        verbose_name_plural = "Propostas de Otimização"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return (
            f"Proposta #{self.pk} - {self.listing.external_id} "
            f"(Score: {self.confidence_score}/100 - {self.status})"
        )
