"""Interface base abstrata (Padrão Adapter) para integrações com marketplaces."""
from abc import ABC, abstractmethod

from apps.accounts_vault.models import MarketplaceAccount


class MarketplaceAdapter(ABC):
    """Contrato padrão que todo adaptador de canal (Mercado Livre, Shopee, TikTok) deve seguir."""

    def __init__(self, account: MarketplaceAccount) -> None:
        self.account = account

    @abstractmethod
    def fetch_recent_listings(self, limit: int = 20, offset: int = 0) -> list[str]:
        """Retorna lista de IDs externos dos anúncios mais recentes da loja."""

    @abstractmethod
    def get_listing_details(self, external_id: str) -> dict:
        """Retorna o payload completo de detalhes e atributos de um anúncio."""

    @abstractmethod
    def get_listing_description(self, external_id: str) -> dict:
        """Retorna o texto de descrição do anúncio."""

    @abstractmethod
    def get_category_attributes(self, category_id: str) -> list[dict]:
        """Retorna a lista de atributos técnicos exigidos/suportados pela categoria."""

    @abstractmethod
    def upload_media(self, file_path: str) -> str:
        """Envia mídia ao marketplace (bloqueado enquanto MARKETPLACE_WRITE_ENABLED=False)."""

    @abstractmethod
    def patch_content(self, external_id: str, payload: dict) -> dict:
        """Atualiza campos de conteúdo do anúncio (bloqueado se MARKETPLACE_WRITE_ENABLED=False)."""
