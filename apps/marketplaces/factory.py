"""Factory de adaptadores de marketplace por canal."""
from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount

from .base import MarketplaceAdapter
from .mercadolivre import MercadoLivreAdapter


def get_adapter_for_account(account: MarketplaceAccount) -> MarketplaceAdapter:
    """Retorna a instância do adaptador correspondente ao canal da MarketplaceAccount."""
    if account.channel == ChannelChoices.MERCADO_LIVRE:
        return MercadoLivreAdapter(account=account)
    raise NotImplementedError(
        f"Adaptador para o canal '{account.channel}' ainda não foi implementado."
    )
