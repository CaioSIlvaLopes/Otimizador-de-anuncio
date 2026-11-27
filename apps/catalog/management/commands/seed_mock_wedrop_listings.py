"""Comando CLI para criar anúncios mockados realistas no padrão bruto WeDrop para testes locais."""
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount
from apps.catalog.models import Listing, ListingSnapshot, ListingStatus

MOCK_CATEGORY_ATTRIBUTES: list[dict[str, Any]] = [
    {
        "id": "BRAND",
        "name": "Marca",
        "value_type": "string",
        "tags": {"required": True},
        "values": [{"name": "Genérica"}],
    },
    {
        "id": "MODEL",
        "name": "Modelo",
        "value_type": "string",
        "tags": {"required": True},
        "values": [],
    },
    {
        "id": "MATERIAL",
        "name": "Material",
        "value_type": "string",
        "tags": {},
        "values": [{"name": "Aço Inox / Plástico ABS"}, {"name": "ABS"}],
    },
    {
        "id": "COLOR",
        "name": "Cor",
        "value_type": "string",
        "tags": {},
        "values": [{"name": "Branco"}, {"name": "Preto"}],
    },
    {
        "id": "CAPACITY_VOLUME",
        "name": "Capacidade em volume",
        "value_type": "number_unit",
        "tags": {},
        "values": [{"name": "250 mL"}],
    },
    {
        "id": "IS_WIRELESS",
        "name": "É sem fio",
        "value_type": "boolean",
        "tags": {},
        "values": [{"name": "Sim"}, {"name": "Não"}],
    },
    {
        "id": "SELLER_SKU",
        "name": "SKU do Vendedor (Protegido WeDrop)",
        "value_type": "string",
        "tags": {"hidden": True},
        "values": [],
    },
    {
        "id": "READONLY_INTERNAL_CODE",
        "name": "Código Interno Read-Only",
        "value_type": "string",
        "tags": {"read_only": True},
        "values": [],
    },
]

MOCK_LISTINGS_DATA: list[dict[str, Any]] = [
    {
        "external_id": "MLB9000000001",
        "category_id": "MLB12345",
        "sku_readonly": "WD-PROC-250",
        "original_title": "Mini Processador Triturador Alimentos Eletrico Usb 250ml",
        "marketplace_status": "active",
        "sold_quantity": 0,
        "raw_item_json": {
            "id": "MLB9000000001",
            "title": "Mini Processador Triturador Alimentos Eletrico Usb 250ml",
            "category_id": "MLB12345",
            "seller_custom_field": "WD-PROC-250",
            "price": 39.90,
            "available_quantity": 45,
            "sold_quantity": 0,
            "condition": "new",
            "status": "active",
            "pictures": [],
            "attributes": [
                {"id": "BRAND", "name": "Marca", "value_name": "Genérica"},
                {"id": "SELLER_SKU", "name": "SKU", "value_name": "WD-PROC-250"},
            ],
        },
        "raw_description_json": {
            "plain_text": (
                "Mini processador triturador elétrico recarregável via USB com capacidade de 250ml e "
                "3 lâminas em aço inox. Ideal para triturar alho, cebola e temperos rapidamente.\n"
                "Acompanha: 1 mini processador 250ml, 1 cabo USB para carregamento."
            )
        },
    },
    {
        "external_id": "MLB9000000002",
        "category_id": "MLB3697",
        "sku_readonly": "WD-TWS-002",
        "original_title": "Fone De Ouvido Bluetooth Sem Fio Tws Esportivo Microfone",
        "marketplace_status": "active",
        "sold_quantity": 3,
        "raw_item_json": {
            "id": "MLB9000000002",
            "title": "Fone De Ouvido Bluetooth Sem Fio Tws Esportivo Microfone",
            "category_id": "MLB3697",
            "seller_custom_field": "WD-TWS-002",
            "price": 59.90,
            "available_quantity": 18,
            "sold_quantity": 3,
            "condition": "new",
            "status": "active",
            "pictures": [],
            "attributes": [
                {"id": "BRAND", "name": "Marca", "value_name": "Genérica"},
                {"id": "COLOR", "name": "Cor", "value_name": "Preto"},
                {"id": "SELLER_SKU", "name": "SKU", "value_name": "WD-TWS-002"},
            ],
        },
        "raw_description_json": {
            "plain_text": (
                "Fone de ouvido Bluetooth 5.3 TWS sem fio na cor preta com microfone integrado e "
                "estojo de recarga portátil. Conexão estável para chamadas, treinos e músicas.\n"
                "Itens inclusos: 1 par de fones TWS, 1 estojo carregador, 1 cabo USB."
            )
        },
    },
]


class Command(BaseCommand):
    """Cria de forma idempotente uma conta ML Sandbox (Mock) e 2 anúncios no estilo WeDrop."""

    help = "Cria conta sandbox mockada e 2 anúncios WeDrop (MLB9000000001 sem vendas e MLB9000000002 com 3 vendas)."

    def handle(self, *args: Any, **options: Any) -> None:
        account, _ = MarketplaceAccount.objects.get_or_create(
            name="ML Sandbox (Mock)",
            channel=ChannelChoices.MERCADO_LIVRE,
            defaults={
                "seller_id": "MOCK_WEDROP_SELLER",
                "is_active": True,
            },
        )

        for item in MOCK_LISTINGS_DATA:
            with transaction.atomic():
                listing, created = Listing.objects.update_or_create(
                    account=account,
                    external_id=item["external_id"],
                    defaults={
                        "category_id": item["category_id"],
                        "sku_readonly": item["sku_readonly"],
                        "original_title": item["original_title"],
                        "marketplace_status": item["marketplace_status"],
                        "sold_quantity": item["sold_quantity"],
                        "status": ListingStatus.DETECTED,
                    },
                )
                if not listing.snapshots.filter(is_baseline=True).exists():
                    ListingSnapshot.objects.create(
                        listing=listing,
                        raw_item_json=item["raw_item_json"],
                        raw_description_json=item["raw_description_json"],
                        category_attributes_json=MOCK_CATEGORY_ATTRIBUTES,
                        is_baseline=True,
                    )

                state_str = "CRIADO" if created else "ATUALIZADO"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[{state_str}] Listing ID={listing.pk} | external_id={listing.external_id} | "
                        f"SKU={listing.sku_readonly} | sold_quantity={listing.sold_quantity}"
                    )
                )
