"""Model MediaAsset para gestão de imagens e vídeos gerados pelo estúdio local."""
from django.db import models


class MediaAssetType(models.TextChoices):
    """Tipos de ativos de mídia produzidos pelo pipeline local."""

    WHITE_BG_COVER = "WHITE_BG_COVER", "Capa Fundo Branco 1200x1200"
    CUTOUT_PNG = "CUTOUT_PNG", "Recorte Transparente PNG"
    INFO_CARD_BENEFITS = "INFO_CARD_BENEFITS", "Card Informativo — Benefícios"
    INFO_CARD_SPECS = "INFO_CARD_SPECS", "Card Informativo — Ficha Técnica"
    VIDEO_VERTICAL_9_16 = "VIDEO_VERTICAL_9_16", "Vídeo Vertical 9:16"
    VIDEO_SQUARE_1_1 = "VIDEO_SQUARE_1_1", "Vídeo Quadrado 1:1"


class MediaAssetStatus(models.TextChoices):
    """Estados de processamento de um ativo de mídia."""

    PENDING = "PENDING", "Pendente"
    PROCESSING = "PROCESSING", "Processando"
    READY = "READY", "Pronto"
    FAILED = "FAILED", "Falhou"


class MediaAsset(models.Model):
    """Ativo de mídia gerado localmente (rembg, Pillow, Playwright, FFmpeg) vinculado a uma proposta."""

    proposal = models.ForeignKey(
        "catalog.OptimizationProposal",
        on_delete=models.CASCADE,
        related_name="media_assets",
        verbose_name="Proposta de Otimização",
    )
    asset_type = models.CharField(
        "Tipo de Ativo",
        max_length=40,
        choices=MediaAssetType.choices,
    )
    status = models.CharField(
        "Status",
        max_length=20,
        choices=MediaAssetStatus.choices,
        default=MediaAssetStatus.PENDING,
    )
    file = models.FileField(
        "Arquivo Gerado",
        upload_to="studio/%Y/%m/%d/",
        blank=True,
        null=True,
    )
    external_media_id = models.CharField(
        "ID de Mídia no Marketplace",
        max_length=128,
        blank=True,
        help_text="Preenchido após upload da mídia no Mercado Livre / Shopee (Fase 4B).",
    )
    width = models.PositiveIntegerField(
        "Largura (px)",
        default=0,
    )
    height = models.PositiveIntegerField(
        "Altura (px)",
        default=0,
    )
    file_size_bytes = models.PositiveBigIntegerField(
        "Tamanho (bytes)",
        default=0,
    )
    metadata_json = models.JSONField(
        "Metadados",
        default=dict,
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
        verbose_name = "Ativo de Mídia"
        verbose_name_plural = "Ativos de Mídia"
        ordering = ["proposal", "id"]

    def __str__(self) -> str:
        return (
            f"{self.get_asset_type_display()} (#{self.pk}) — "
            f"Proposta #{self.proposal_id} [{self.status}]"
        )
