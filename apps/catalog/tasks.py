"""Tasks Celery do módulo catalog (Watcher e Otimização de Anúncio na fila queue_fast)."""
from typing import Any

from celery import shared_task

from apps.accounts_vault.models import MarketplaceAccount
from apps.agents.pipeline import OptimizationPipelineService
from apps.catalog.models import (
    Listing,
    ListingStatus,
    OptimizationProposal,
    ProposalStatus,
)
from apps.catalog.services.watcher import sync_account_listings


@shared_task(name="apps.catalog.tasks.watch_account_listings", queue="queue_fast")
def watch_account_listings(account_id: int, limit: int = 20) -> dict[str, Any]:
    """Sincroniza os anúncios recentes de uma MarketplaceAccount específica."""
    try:
        account = MarketplaceAccount.objects.get(pk=account_id)
    except MarketplaceAccount.DoesNotExist:
        return {
            "detected": 0,
            "updated_existing": 0,
            "skipped": 1,
            "errors": [f"MarketplaceAccount ID={account_id} não encontrada."],
        }
    return sync_account_listings(account=account, limit=limit)


@shared_task(name="apps.catalog.tasks.watch_all_active_accounts", queue="queue_fast")
def watch_all_active_accounts(limit: int = 20) -> dict[str, Any]:
    """Varre todas as contas ativas e não pausadas pelo circuit-breaker."""
    accounts = MarketplaceAccount.objects.filter(
        is_active=True,
        is_paused_by_circuit_breaker=False,
    ).exclude(seller_id="")

    aggregated: dict[str, Any] = {
        "accounts_processed": 0,
        "detected": 0,
        "updated_existing": 0,
        "skipped": 0,
        "errors": [],
    }

    for account in accounts:
        result = sync_account_listings(account=account, limit=limit)
        aggregated["accounts_processed"] += 1
        aggregated["detected"] += result["detected"]
        aggregated["updated_existing"] += result["updated_existing"]
        aggregated["skipped"] += result["skipped"]
        aggregated["errors"].extend(result["errors"])

    return aggregated


@shared_task(name="apps.catalog.tasks.optimize_listing_task", queue="queue_fast")
def optimize_listing_task(listing_id: int) -> dict[str, Any]:
    """Executa os 4 sub-agentes para um Listing, gerando OptimizationProposal e mudando status para REVIEW."""
    try:
        listing = Listing.objects.get(pk=listing_id)
    except Listing.DoesNotExist:
        return {
            "status": "error",
            "listing_id": listing_id,
            "error": f"Listing ID={listing_id} não encontrado.",
        }

    listing.status = ListingStatus.PROCESSING
    listing.save(update_fields=["status", "updated_at"])

    try:
        proposal = OptimizationPipelineService.run_for_listing(listing=listing)
        return {
            "status": "ok",
            "listing_id": listing.pk,
            "external_id": listing.external_id,
            "proposal_id": proposal.pk,
            "confidence_score": proposal.confidence_score,
            "llm_tokens_used": proposal.llm_tokens_used,
        }
    except Exception as exc:
        listing.status = ListingStatus.FAILED
        listing.save(update_fields=["status", "updated_at"])

        baseline = (
            listing.snapshots.filter(is_baseline=True).order_by("-captured_at").first()
            or listing.snapshots.order_by("-captured_at").first()
        )
        if baseline is not None:
            OptimizationProposal.objects.create(
                listing=listing,
                baseline_snapshot=baseline,
                proposed_title=listing.original_title,
                proposed_description="",
                status=ProposalStatus.FAILED,
                error_message=str(exc),
            )
        return {
            "status": "failed",
            "listing_id": listing.pk,
            "external_id": listing.external_id,
            "error": str(exc),
        }
