"""Adaptador de integração com o Mercado Livre (Leitura + Patch de Conteúdo Blindado)."""
from typing import Any

import httpx
from django.conf import settings

from apps.accounts_vault.models import MarketplaceAccount
from apps.accounts_vault.oauth_ml import MercadoLivreOAuthError, ensure_valid_token

from .base import MarketplaceAdapter
from .exceptions import (
    CircuitBreakerOpenError,
    MarketplaceAPIError,
    WriteDisabledError,
)
from .sanitizer import sanitize_patch_payload


class MercadoLivreAdapter(MarketplaceAdapter):
    """Implementação do adaptador Mercado Livre com proteção de circuit-breaker, trava de escrita e whitelist."""

    BASE_URL = "https://api.mercadolibre.com"
    DEFAULT_TIMEOUT = 15.0

    def __init__(
        self,
        account: MarketplaceAccount,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(account)
        self.timeout = timeout

    def _check_circuit_breaker(self) -> None:
        """Impede chamadas caso o circuit-breaker da conta esteja aberto."""
        if self.account.is_paused_by_circuit_breaker:
            raise CircuitBreakerOpenError(
                f"Circuit-breaker ativo para a conta '{self.account.name}' "
                f"({self.account.consecutive_errors} erros consecutivos)."
            )

    def _check_write_enabled(self) -> None:
        """Impede qualquer chamada de escrita enquanto MARKETPLACE_WRITE_ENABLED for False."""
        if not bool(getattr(settings, "MARKETPLACE_WRITE_ENABLED", False)):
            raise WriteDisabledError(
                "Operação de escrita bloqueada: MARKETPLACE_WRITE_ENABLED=False "
                "(Regra Inviolável #2 - Somente Leitura)."
            )

    def _clear_errors_on_success(self) -> None:
        """Zera o contador de erros consecutivos após uma chamada bem-sucedida."""
        if self.account.consecutive_errors > 0:
            self.account.consecutive_errors = 0
            if self.account.pk is not None:
                self.account.save(update_fields=["consecutive_errors", "updated_at"])

    def _get_auth_headers(self) -> dict[str, str]:
        """Obtém cabeçalhos autenticados renovando token se necessário."""
        try:
            access_token = ensure_valid_token(self.account)
        except MercadoLivreOAuthError as exc:
            raise MarketplaceAPIError(
                f"Falha de autenticação OAuth para a conta '{self.account.name}': {exc}"
            ) from exc

        return {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

    def _handle_response(
        self,
        response: httpx.Response,
        method: str,
        path: str,
        allow_404: bool = False,
    ) -> Any:
        """Processa status HTTP, contabilizando falhas críticas no circuit-breaker."""
        if allow_404 and response.status_code == 404:
            self._clear_errors_on_success()
            return None

        if response.status_code in (401, 403, 429) or response.status_code >= 500:
            self.account.record_api_error()
            raise MarketplaceAPIError(
                f"Erro crítico HTTP {response.status_code} em {method} {path}: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )

        if response.status_code >= 400:
            raise MarketplaceAPIError(
                f"Erro HTTP {response.status_code} em {method} {path}: {response.text}",
                status_code=response.status_code,
                response_body=response.text,
            )

        self._clear_errors_on_success()
        if not response.content:
            return {}
        return response.json()

    def _request_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        """Executa um GET autenticado tratando renovação OAuth e circuit-breaker."""
        self._check_circuit_breaker()
        headers = self._get_auth_headers()

        try:
            with httpx.Client(
                base_url=self.BASE_URL,
                timeout=self.timeout,
                headers=headers,
            ) as client:
                response = client.get(path, params=params)
        except httpx.RequestError as exc:
            self.account.record_api_error()
            raise MarketplaceAPIError(
                f"Falha de rede na requisição GET {path}: {exc}"
            ) from exc

        return self._handle_response(response, "GET", path, allow_404=allow_404)

    def _request_put(self, path: str, json_body: dict[str, Any]) -> Any:
        """Executa um PUT autenticado respeitando circuit-breaker."""
        self._check_write_enabled()
        self._check_circuit_breaker()
        headers = self._get_auth_headers()

        try:
            with httpx.Client(
                base_url=self.BASE_URL,
                timeout=self.timeout,
                headers=headers,
            ) as client:
                response = client.put(path, json=json_body)
        except httpx.RequestError as exc:
            self.account.record_api_error()
            raise MarketplaceAPIError(
                f"Falha de rede na requisição PUT {path}: {exc}"
            ) from exc

        return self._handle_response(response, "PUT", path)

    def fetch_recent_listings(self, limit: int = 20, offset: int = 0) -> list[str]:
        """Busca IDs dos anúncios mais recentes do vendedor ordenados por start_time_desc."""
        self._check_circuit_breaker()
        if not self.account.seller_id:
            raise MarketplaceAPIError(
                f"A conta '{self.account.name}' não possui seller_id configurado."
            )
        path = f"/users/{self.account.seller_id}/items/search"
        data = self._request_get(
            path,
            params={
                "orders": "start_time_desc",
                "limit": int(limit),
                "offset": int(offset),
            },
        )
        results = data.get("results", []) if isinstance(data, dict) else []
        return [str(item_id) for item_id in results]

    def get_listing_details(self, external_id: str) -> dict:
        """Retorna os detalhes completos de um item incluindo todos os atributos."""
        data = self._request_get(
            f"/items/{external_id}",
            params={"include_attributes": "all"},
        )
        return dict(data) if isinstance(data, dict) else {}

    def get_listing_description(self, external_id: str) -> dict:
        """Retorna a descrição do item (trata HTTP 404 retornando plain_text vazio)."""
        data = self._request_get(
            f"/items/{external_id}/description",
            allow_404=True,
        )
        if data is None:
            return {"plain_text": ""}
        return dict(data) if isinstance(data, dict) else {"plain_text": ""}

    def get_category_attributes(self, category_id: str) -> list[dict]:
        """Retorna o catálogo de atributos técnicos da categoria no Mercado Livre."""
        data = self._request_get(f"/categories/{category_id}/attributes")
        return list(data) if isinstance(data, list) else []

    def upload_media(self, file_path: str) -> str:
        """Bloqueia escrita se MARKETPLACE_WRITE_ENABLED=False; Fase 4 implementará upload de mídia."""
        self._check_write_enabled()
        self._check_circuit_breaker()
        raise NotImplementedError("Upload de mídia será implementado na Fase 4")

    def patch_content(self, external_id: str, payload: dict) -> dict:
        """Atualiza campos de conteúdo do anúncio no Mercado Livre usando whitelist estrita."""
        # 1. Trava global de escrita
        self._check_write_enabled()
        # 2. Circuit-breaker
        self._check_circuit_breaker()
        # 3. Sanitização estrita de whitelist e proteção WeDrop
        sanitized = sanitize_patch_payload(payload)

        item_fields: dict[str, Any] = {}
        for field in ("title", "attributes", "pictures", "video"):
            if field in sanitized and sanitized[field] is not None:
                if field == "attributes" and not sanitized[field]:
                    continue
                item_fields[field] = sanitized[field]

        result: dict[str, Any] = {
            "external_id": external_id,
            "sanitized_payload": sanitized,
            "item_updated": False,
            "description_updated": False,
            "item_response": None,
            "description_response": None,
        }

        if item_fields:
            result["item_response"] = self._request_put(
                f"/items/{external_id}",
                json_body=item_fields,
            )
            result["item_updated"] = True

        if "description" in sanitized and sanitized["description"] is not None:
            result["description_response"] = self._request_put(
                f"/items/{external_id}/description",
                json_body={"plain_text": str(sanitized["description"])},
            )
            result["description_updated"] = True

        return result
