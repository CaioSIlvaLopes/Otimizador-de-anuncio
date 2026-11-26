"""Comando de diagnóstico para testar leitura real de anúncios no Mercado Livre."""
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts_vault.models import MarketplaceAccount
from apps.marketplaces.exceptions import CircuitBreakerOpenError, MarketplaceAPIError
from apps.marketplaces.mercadolivre import MercadoLivreAdapter


def _extract_sku(item_data: dict[str, Any]) -> str:
    """Extrai o SKU sem alterá-lo (seller_custom_field ou atributo SELLER_SKU)."""
    direct_sku = item_data.get("seller_custom_field")
    if direct_sku:
        return str(direct_sku)
    for attr in item_data.get("attributes") or []:
        if isinstance(attr, dict) and attr.get("id") == "SELLER_SKU":
            val = attr.get("value_name")
            if val:
                return str(val)
    return "-"


class Command(BaseCommand):
    """Testa a conexão somente leitura com o Mercado Livre e lista os anúncios recentes."""

    help = "Busca os últimos anúncios da conta no Mercado Livre em modo somente leitura."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--account-id",
            type=int,
            required=True,
            help="ID da MarketplaceAccount cadastrada no banco.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help="Quantidade máxima de anúncios recentes para listar (padrão: 5).",
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

        adapter = MercadoLivreAdapter(account=account)

        self.stdout.write(
            self.style.NOTICE(
                f"Conectando à conta '{account.name}' (Seller ID: {account.seller_id or 'N/A'})..."
            )
        )

        try:
            item_ids = adapter.fetch_recent_listings(limit=limit)
        except (CircuitBreakerOpenError, MarketplaceAPIError) as exc:
            raise CommandError(f"Erro ao buscar anúncios recentes: {exc}") from exc

        if not item_ids:
            self.stdout.write(
                self.style.WARNING("Nenhum anúncio encontrado para esta conta.")
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Encontrados {len(item_ids)} anúncio(s). Buscando detalhes...\n"
            )
        )

        for idx, external_id in enumerate(item_ids, start=1):
            try:
                details = adapter.get_listing_details(external_id)
            except (CircuitBreakerOpenError, MarketplaceAPIError) as exc:
                self.stderr.write(
                    self.style.ERROR(f"[{idx}] Erro ao consultar {external_id}: {exc}")
                )
                continue

            title = details.get("title", "")
            sku = _extract_sku(details)
            sold_qty = details.get("sold_quantity", 0)
            category_id = details.get("category_id", "")
            status = details.get("status", "")

            self.stdout.write(
                f"[{idx}] external_id={details.get('id', external_id)} | "
                f"status={status} | category_id={category_id} | "
                f"sold_quantity={sold_qty} | seller_custom_field={sku} | "
                f"title=\"{title}\""
            )
