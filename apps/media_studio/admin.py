"""Configuração do Django Admin para MediaAsset (paleta Azul #3b82f6 / Laranja #f97316)."""
from django.contrib import admin
from django.utils.html import format_html

from .models import MediaAsset, MediaAssetStatus


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    """Gerenciamento e preview de ativos de mídia gerados pelo estúdio local."""

    list_display = (
        "id",
        "proposal",
        "asset_type",
        "status_badge",
        "dimensions_display",
        "file_size_bytes",
        "file_preview_link",
        "created_at",
    )
    list_filter = ("asset_type", "status", "created_at")
    search_fields = (
        "proposal__listing__external_id",
        "proposal__listing__sku_readonly",
        "external_media_id",
    )
    readonly_fields = (
        "status_badge",
        "dimensions_display",
        "file_size_bytes",
        "file_preview_link",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Status")
    def status_badge(self, obj: MediaAsset) -> str:
        if obj.status == MediaAssetStatus.READY:
            return format_html('<strong style="color: #3b82f6;">PRONTO</strong>')
        return format_html(
            '<strong style="color: #f97316;">{}</strong>',
            obj.get_status_display(),
        )

    @admin.display(description="Dimensões")
    def dimensions_display(self, obj: MediaAsset) -> str:
        if obj.width and obj.height:
            return f"{obj.width}x{obj.height}px"
        return "-"

    @admin.display(description="Preview / Arquivo")
    def file_preview_link(self, obj: MediaAsset) -> str:
        if obj.file:
            return format_html(
                '<a href="{}" target="_blank" style="color: #3b82f6; font-weight: 600;">Abrir Arquivo</a>',
                obj.file.url,
            )
        return "-"
