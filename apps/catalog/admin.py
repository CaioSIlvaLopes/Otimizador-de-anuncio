"""Configuração do Django Admin para Listing e ListingSnapshot (somente leitura)."""
import json

from django.contrib import admin
from django.http import HttpRequest
from django.utils.html import format_html

from .models import Listing, ListingSnapshot


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    """Painel administrativo de anúncios monitorados com alerta visual de vendas (sold_quantity > 0)."""

    list_display = (
        "id",
        "external_id",
        "account",
        "sku_readonly",
        "original_title",
        "status",
        "marketplace_status",
        "sold_quantity_badge",
        "confidence_score",
        "last_synced_at",
    )
    list_filter = ("account", "status", "marketplace_status")
    search_fields = ("external_id", "sku_readonly", "original_title")
    readonly_fields = (
        "sku_readonly",
        "sold_quantity_badge",
        "last_synced_at",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Vendas / Trava de Título")
    def sold_quantity_badge(self, obj: Listing) -> str:
        """Exibe badge em laranja (#f97316) quando sold_quantity > 0 ou azul (#3b82f6) quando 0."""
        if obj.sold_quantity > 0:
            return format_html(
                '<strong style="color: #f97316;">{} vendas (Título Bloqueado)</strong>',
                obj.sold_quantity,
            )
        return format_html(
            '<span style="color: #3b82f6;">0 vendas (Título Editável)</span>'
        )


@admin.register(ListingSnapshot)
class ListingSnapshotAdmin(admin.ModelAdmin):
    """Visualização estritamente somente leitura dos snapshots brutos capturados."""

    list_display = ("id", "listing", "is_baseline", "captured_at")
    list_filter = ("is_baseline", "listing__account")
    search_fields = ("listing__external_id", "listing__sku_readonly")
    readonly_fields = (
        "listing",
        "is_baseline",
        "captured_at",
        "formatted_raw_item_json",
        "formatted_raw_description_json",
        "formatted_category_attributes_json",
    )
    exclude = (
        "raw_item_json",
        "raw_description_json",
        "category_attributes_json",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: ListingSnapshot | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: ListingSnapshot | None = None,
    ) -> bool:
        return False

    @admin.display(description="JSON Bruto do Item")
    def formatted_raw_item_json(self, obj: ListingSnapshot) -> str:
        pretty = json.dumps(obj.raw_item_json, indent=2, ensure_ascii=False)
        return format_html("<pre style='max-height: 400px; overflow: auto;'>{}</pre>", pretty)

    @admin.display(description="JSON Bruto da Descrição")
    def formatted_raw_description_json(self, obj: ListingSnapshot) -> str:
        pretty = json.dumps(obj.raw_description_json, indent=2, ensure_ascii=False)
        return format_html("<pre style='max-height: 250px; overflow: auto;'>{}</pre>", pretty)

    @admin.display(description="Schema de Atributos da Categoria")
    def formatted_category_attributes_json(self, obj: ListingSnapshot) -> str:
        pretty = json.dumps(obj.category_attributes_json, indent=2, ensure_ascii=False)
        return format_html("<pre style='max-height: 300px; overflow: auto;'>{}</pre>", pretty)
