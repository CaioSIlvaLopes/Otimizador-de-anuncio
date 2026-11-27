"""Tasks Celery do módulo catalog (Watcher, Otimização e Publicação com Jitter na fila queue_fast)."""
import random
import time
from typing import Any

from celery import shared_task
from django.conf import settings

from apps.accounts_vault.models import MarketplaceAccount
from apps.agents.pipeline import OptimizationPipelineService
from apps.catalog.models import (
    Listing,
    ListingStatus,
    OptimizationProposal,
    ProposalStatus,
)
from apps.catalog.services.publisher import publish_proposal
from apps.catalog.services.watcher import sync_account_listings
from apps.marketplaces.exceptions import (
    CircuitBreakerOpenError,
    DailyPatchQuotaExceededError,
    MarketplaceAPIError,
    WriteDisabledError,
)


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


@shared_task(name="apps.catalog.tasks.publish_proposal_task", queue="queue_fast")
def publish_proposal_task(
    proposal_id: int,
    apply_jitter: bool = True,
) -> dict[str, Any]:
    """Publica uma proposta aprovada aplicando atraso aleatório (jitter) sem loops infinitos de retry."""
    try:
        proposal = OptimizationProposal.objects.select_related(
            "listing",
            "listing__account",
        ).get(pk=proposal_id)
    except OptimizationProposal.DoesNotExist:
        return {
            "status": "error",
            "proposal_id": proposal_id,
            "error": f"OptimizationProposal ID={proposal_id} não encontrada.",
        }

    jitter_applied = 0.0
    if apply_jitter:
        min_sec = int(getattr(settings, "PUBLISH_JITTER_MIN_SECONDS", 45))
        max_sec = int(getattr(settings, "PUBLISH_JITTER_MAX_SECONDS", 150))
        if max_sec >= min_sec > 0:
            jitter_applied = round(random.uniform(min_sec, max_sec), 2)
            time.sleep(jitter_applied)

    try:
        result = publish_proposal(proposal=proposal)
        return {
            "status": "published",
            "proposal_id": proposal.pk,
            "jitter_seconds": jitter_applied,
            **result,
        }
    except (
        WriteDisabledError,
        CircuitBreakerOpenError,
        DailyPatchQuotaExceededError,
        MarketplaceAPIError,
    ) as exc:
        proposal.error_message = str(exc)
        if not isinstance(exc, WriteDisabledError):
            proposal.status = ProposalStatus.FAILED
        proposal.save(update_fields=["status", "error_message", "updated_at"])
        return {
            "status": "blocked_or_failed",
            "proposal_id": proposal.pk,
            "jitter_seconds": jitter_applied,
            "error": str(exc),
        }
