"""Tasks Celery do módulo catalog (executadas na fila rápida queue_fast)."""
from typing import Any

from celery import shared_task

from apps.accounts_vault.models import MarketplaceAccount
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
