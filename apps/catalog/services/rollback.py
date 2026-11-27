"""Serviço de Rollback em 1 clique a partir do ListingSnapshot baseline imutável."""
from typing import Any

from django.conf import settings

from apps.agents.pipeline import filter_editable_category_attributes
from apps.catalog.models import Listing, ListingSnapshot, ListingStatus
from apps.marketplaces.exceptions import CircuitBreakerOpenError, WriteDisabledError
from apps.marketplaces.factory import get_adapter_for_account
from apps.marketplaces.sanitizer import FORBIDDEN_ATTRIBUTE_IDS, sanitize_patch_payload


def _extract_restorable_attributes(snapshot: ListingSnapshot) -> list[dict[str, str]]:
    """Extrai os atributos do snapshot baseline excluindo SELLER_SKU e atributos read-only/hidden."""
    raw_attrs = (snapshot.raw_item_json or {}).get("attributes") or []
    category_schema = snapshot.category_attributes_json or []

    allowed_ids: set[str] = set()
    if category_schema:
        editable_schema = filter_editable_category_attributes(category_schema)
        allowed_ids = {
            str(a.get("id")).strip().upper()
            for a in editable_schema
            if isinstance(a, dict) and a.get("id")
        }

    restorable: list[dict[str, str]] = []
    for attr in raw_attrs:
        if not isinstance(attr, dict):
            continue
        attr_id = str(attr.get("id") or "").strip()
        if not attr_id or attr_id.upper() in FORBIDDEN_ATTRIBUTE_IDS:
            continue
        if allowed_ids and attr_id.upper() not in allowed_ids:
            continue

        value_name = attr.get("value_name")
        if value_name is None or not str(value_name).strip():
            continue
        restorable.append({"id": attr_id, "value_name": str(value_name).strip()})

    return restorable


def rollback_listing_to_baseline(
    listing: Listing,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Restaura (ou simula restauração) do anúncio para o estado original do snapshot baseline."""
    baseline = (
        listing.snapshots.filter(is_baseline=True).order_by("captured_at").first()
    )
    if baseline is None:
        raise ValueError(
            f"O anúncio {listing.external_id} não possui ListingSnapshot baseline para rollback."
        )

    raw_item = baseline.raw_item_json or {}
    raw_desc = baseline.raw_description_json or {}

    original_title = str(raw_item.get("title") or listing.original_title or "").strip()
    original_description = str(raw_desc.get("plain_text") or "").strip()
    original_attributes = _extract_restorable_attributes(baseline)

    sold_quantity = int(listing.sold_quantity or 0)
    adapter = None

    if not dry_run:
        if not bool(getattr(settings, "MARKETPLACE_WRITE_ENABLED", False)):
            raise WriteDisabledError(
                "Rollback ao vivo bloqueado: MARKETPLACE_WRITE_ENABLED=False."
            )
        if listing.account.is_paused_by_circuit_breaker:
            raise CircuitBreakerOpenError(
                f"Conta '{listing.account.name}' está pausada pelo circuit-breaker."
            )
        adapter = get_adapter_for_account(listing.account)
        live_details = adapter.get_listing_details(listing.external_id)
        sold_quantity = int(live_details.get("sold_quantity") or sold_quantity)
        if sold_quantity != listing.sold_quantity:
            listing.sold_quantity = sold_quantity
            listing.save(update_fields=["sold_quantity", "updated_at"])

    candidate_payload: dict[str, Any] = {
        "title": original_title,
        "description": original_description,
    }
    if original_attributes:
        candidate_payload["attributes"] = original_attributes

    preflight_warnings: list[str] = []
    if sold_quantity > 0:
        candidate_payload.pop("title", None)
        preflight_warnings.append(
            f"Título omitido automaticamente no Pre-Flight do Rollback: anúncio possui {sold_quantity} venda(s)."
        )

    sanitized_payload = sanitize_patch_payload(candidate_payload)

    if dry_run:
        return {
            "dry_run": True,
            "external_id": listing.external_id,
            "baseline_snapshot_id": baseline.pk,
            "sold_quantity": sold_quantity,
            "payload": sanitized_payload,
            "preflight_warnings": preflight_warnings,
        }

    assert adapter is not None
    api_result = adapter.patch_content(
        external_id=listing.external_id,
        payload=sanitized_payload,
    )

    listing.status = ListingStatus.DETECTED
    listing.save(update_fields=["status", "updated_at"])

    return {
        "dry_run": False,
        "external_id": listing.external_id,
        "baseline_snapshot_id": baseline.pk,
        "status": listing.status,
        "payload": sanitized_payload,
        "preflight_warnings": preflight_warnings,
        "api_result": api_result,
    }
