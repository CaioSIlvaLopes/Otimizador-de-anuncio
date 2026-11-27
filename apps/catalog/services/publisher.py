"""Serviço de publicação segura com Pre-Flight Check, modo Dry-Run e controle de cota diária."""
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.catalog.models import ListingStatus, OptimizationProposal, ProposalStatus
from apps.governance.models import DailyQuotaLog
from apps.marketplaces.exceptions import (
    CircuitBreakerOpenError,
    DailyPatchQuotaExceededError,
    WriteDisabledError,
)
from apps.marketplaces.factory import get_adapter_for_account
from apps.marketplaces.sanitizer import sanitize_patch_payload


def build_safe_patch_payload(
    proposal: OptimizationProposal,
    live_item_data: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Monta o payload de atualização aplicando Pre-Flight Check (sold_quantity > 0) e whitelist."""
    preflight_warnings: list[str] = []

    if isinstance(live_item_data, dict) and "sold_quantity" in live_item_data:
        sold_quantity = int(live_item_data.get("sold_quantity") or 0)
    else:
        sold_quantity = int(proposal.listing.sold_quantity or 0)

    formatted_attributes: list[dict[str, str]] = []
    for attr in proposal.proposed_attributes_json or []:
        if not isinstance(attr, dict):
            continue
        attr_id = str(attr.get("id") or "").strip()
        value_name = str(attr.get("value_name") or "").strip()
        if not attr_id or not value_name or attr_id.upper() == "SELLER_SKU":
            continue
        formatted_attributes.append({"id": attr_id, "value_name": value_name})

    candidate_payload: dict[str, Any] = {
        "title": (proposal.proposed_title or "").strip(),
        "description": (proposal.proposed_description or "").strip(),
    }
    if formatted_attributes:
        candidate_payload["attributes"] = formatted_attributes

    # Regra Inviolável #4: Pre-Flight Check antes de Publicar
    if sold_quantity > 0:
        candidate_payload.pop("title", None)
        preflight_warnings.append(
            f"Título omitido automaticamente no Pre-Flight: anúncio já possui {sold_quantity} venda(s)."
        )

    sanitized_payload = sanitize_patch_payload(candidate_payload)
    return sanitized_payload, preflight_warnings


def simulate_proposal_patch(proposal: OptimizationProposal) -> dict[str, Any]:
    """Executa validação e sanitização em modo Dry-Run sem disparar chamadas de escrita externas."""
    sanitized_payload, preflight_warnings = build_safe_patch_payload(
        proposal=proposal,
        live_item_data=None,
    )
    return {
        "dry_run": True,
        "proposal_id": proposal.pk,
        "external_id": proposal.listing.external_id,
        "sku_readonly": proposal.listing.sku_readonly,
        "sold_quantity": proposal.listing.sold_quantity,
        "marketplace_write_enabled": bool(
            getattr(settings, "MARKETPLACE_WRITE_ENABLED", False)
        ),
        "payload": sanitized_payload,
        "preflight_warnings": preflight_warnings,
    }


def publish_proposal(proposal: OptimizationProposal) -> dict[str, Any]:
    """Executa Pre-Flight ao vivo e envia o patch sanitizado ao marketplace."""
    if not bool(getattr(settings, "MARKETPLACE_WRITE_ENABLED", False)):
        raise WriteDisabledError(
            "Publicação bloqueada: MARKETPLACE_WRITE_ENABLED=False (Modo Somente Leitura)."
        )

    listing = proposal.listing
    account = listing.account

    if account.is_paused_by_circuit_breaker:
        raise CircuitBreakerOpenError(
            f"A conta '{account.name}' está pausada pelo circuit-breaker."
        )

    quota_log = DailyQuotaLog.get_today()
    if not quota_log.can_send_patch(account):
        raise DailyPatchQuotaExceededError(
            f"Limite diário de patches atingido para a conta '{account.name}' "
            f"({account.max_daily_patches}/dia)."
        )

    adapter = get_adapter_for_account(account)

    # Pre-Flight Check ao vivo: consulta sold_quantity atualizado antes do PUT
    live_item_data = adapter.get_listing_details(listing.external_id)
    live_sold_qty = int(live_item_data.get("sold_quantity") or 0)
    live_mp_status = str(
        live_item_data.get("status") or listing.marketplace_status
    )

    if (
        listing.sold_quantity != live_sold_qty
        or listing.marketplace_status != live_mp_status
    ):
        listing.sold_quantity = live_sold_qty
        listing.marketplace_status = live_mp_status
        listing.save(
            update_fields=["sold_quantity", "marketplace_status", "updated_at"]
        )

    sanitized_payload, preflight_warnings = build_safe_patch_payload(
        proposal=proposal,
        live_item_data=live_item_data,
    )

    api_result = adapter.patch_content(
        external_id=listing.external_id,
        payload=sanitized_payload,
    )

    patches_today = quota_log.record_patch_sent(account)
    now = timezone.now()

    proposal.status = ProposalStatus.PUBLISHED
    proposal.published_at = now
    proposal.last_patch_payload_json = sanitized_payload
    proposal.error_message = ""
    proposal.save(
        update_fields=[
            "status",
            "published_at",
            "last_patch_payload_json",
            "error_message",
            "updated_at",
        ]
    )

    listing.status = ListingStatus.PUBLISHED
    listing.save(update_fields=["status", "updated_at"])

    return {
        "dry_run": False,
        "proposal_id": proposal.pk,
        "external_id": listing.external_id,
        "payload": sanitized_payload,
        "preflight_warnings": preflight_warnings,
        "patches_sent_today": patches_today,
        "published_at": now.isoformat(),
        "api_result": api_result,
    }
