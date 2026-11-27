"""Serviço Watcher: sincronização de anúncios recentes e captura de snapshot baseline imutável."""
from typing import Any

from django.db import transaction

from apps.accounts_vault.models import MarketplaceAccount
from apps.catalog.models import Listing, ListingSnapshot, ListingStatus
from apps.marketplaces.exceptions import CircuitBreakerOpenError, MarketplaceAPIError
from apps.marketplaces.factory import get_adapter_for_account


def extract_sku_from_item_payload(item_data: dict[str, Any]) -> str:
    """Extrai o SKU da WeDrop (seller_custom_field ou atributo SELLER_SKU) apenas para leitura."""
    direct_sku = item_data.get("seller_custom_field")
    if direct_sku and str(direct_sku).strip():
        return str(direct_sku).strip()

    attributes = item_data.get("attributes")
    if isinstance(attributes, list):
        for attr in attributes:
            if isinstance(attr, dict) and attr.get("id") == "SELLER_SKU":
                val = attr.get("value_name") or attr.get("value_id")
                if val and str(val).strip():
                    return str(val).strip()
    return ""


def sync_account_listings(
    account: MarketplaceAccount,
    limit: int = 20,
) -> dict[str, Any]:
    """Sincroniza os anúncios recentes de uma loja criando Listing + ListingSnapshot baseline."""
    summary: dict[str, Any] = {
        "detected": 0,
        "updated_existing": 0,
        "skipped": 0,
        "errors": [],
    }

    if (
        not account.is_active
        or not account.seller_id
        or account.is_paused_by_circuit_breaker
    ):
        summary["skipped"] += 1
        return summary

    try:
        adapter = get_adapter_for_account(account)
        external_ids = adapter.fetch_recent_listings(limit=limit)
    except (CircuitBreakerOpenError, MarketplaceAPIError, NotImplementedError) as exc:
        summary["errors"].append(f"Conta '{account.name}': {exc}")
        return summary

    if not external_ids:
        return summary

    existing_listings = Listing.objects.filter(
        account=account,
        external_id__in=external_ids,
    )
    existing_map: dict[str, Listing] = {
        item.external_id: item for item in existing_listings
    }

    # Cache em memória para não consultar repetidamente os atributos da mesma categoria
    category_attributes_cache: dict[str, list[dict[str, Any]]] = {}

    for external_id in external_ids:
        try:
            details = adapter.get_listing_details(external_id)
        except CircuitBreakerOpenError as exc:
            summary["errors"].append(f"{external_id}: {exc}")
            break
        except MarketplaceAPIError as exc:
            summary["errors"].append(f"{external_id}: {exc}")
            continue

        new_sold_qty = int(details.get("sold_quantity") or 0)
        new_mp_status = str(details.get("status") or "")

        # Anúncio já existente: atualiza apenas sold_quantity e marketplace_status sem novo snapshot
        if external_id in existing_map:
            listing = existing_map[external_id]
            listing.sold_quantity = new_sold_qty
            listing.marketplace_status = new_mp_status
            listing.save(
                update_fields=[
                    "sold_quantity",
                    "marketplace_status",
                    "last_synced_at",
                    "updated_at",
                ]
            )
            summary["updated_existing"] += 1
            continue

        # Anúncio novo: captura descrição e schema de atributos da categoria e cria snapshot baseline
        try:
            description_data = adapter.get_listing_description(external_id)
            category_id = str(details.get("category_id") or "")
            if category_id:
                if category_id not in category_attributes_cache:
                    category_attributes_cache[category_id] = (
                        adapter.get_category_attributes(category_id)
                    )
                category_attrs = category_attributes_cache[category_id]
            else:
                category_attrs = []
        except CircuitBreakerOpenError as exc:
            summary["errors"].append(f"{external_id}: {exc}")
            break
        except MarketplaceAPIError as exc:
            summary["errors"].append(f"{external_id}: {exc}")
            continue

        sku_readonly = extract_sku_from_item_payload(details)
        original_title = str(details.get("title") or "")[:255]

        with transaction.atomic():
            listing = Listing.objects.create(
                account=account,
                external_id=external_id,
                category_id=category_id,
                sku_readonly=sku_readonly,
                original_title=original_title,
                marketplace_status=new_mp_status,
                sold_quantity=new_sold_qty,
                status=ListingStatus.DETECTED,
            )
            ListingSnapshot.objects.create(
                listing=listing,
                raw_item_json=details,
                raw_description_json=description_data,
                category_attributes_json=category_attrs,
                is_baseline=True,
            )
            existing_map[external_id] = listing

        summary["detected"] += 1

    return summary
