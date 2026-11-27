"""Comando CLI para executar o Watcher de forma síncrona (sem depender de Redis/Celery Beat)."""
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts_vault.models import MarketplaceAccount
from apps.catalog.models import Listing
from apps.catalog.services.watcher import sync_account_listings


class Command(BaseCommand):
    """Executa o WatcherService localmente para uma conta específica e exibe o resumo."""

    help = "Executa o Watcher de anúncios síncrono para uma MarketplaceAccount."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--account-id",
            type=int,
            required=True,
            help="ID da MarketplaceAccount para sincronizar.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help="Quantidade máxima de anúncios recentes para verificar (padrão: 5).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        account_id: int = options["account_id"]
        limit: int = options["limit"]

        try:
            account = MarketplaceAccount.objects.get(pk=account_id)
        except MarketplaceAccount.DoesNotExist as exc:
            raise CommandError(
                f"MarketplaceAccount com ID={account_id} não encontrada."
            ) from exc

        self.stdout.write(
            self.style.NOTICE(
                f"Iniciando Watcher para '{account.name}' (ID={account.pk}, limit={limit})..."
            )
        )

        summary = sync_account_listings(account=account, limit=limit)

        self.stdout.write(
            self.style.SUCCESS(
                f"Resumo Watcher -> Novos detectados: {summary['detected']} | "
                f"Existentes atualizados: {summary['updated_existing']} | "
                f"Ignorados: {summary['skipped']}"
            )
        )

        if summary["errors"]:
            for err in summary["errors"]:
                self.stderr.write(self.style.ERROR(f"Erro: {err}"))

        listings = (
            Listing.objects.filter(account=account)
            .prefetch_related("snapshots")
            .order_by("-created_at")[:limit]
        )
        for item in listings:
            snap_count = item.snapshots.count()
            self.stdout.write(
                f" - {item.external_id} | status={item.status} | "
                f"mp_status={item.marketplace_status} | sold={item.sold_quantity} | "
                f"sku={item.sku_readonly or '-'} | snapshots={snap_count} | "
                f"title=\"{item.original_title}\""
            )
