"""Exceções de domínio para adaptadores de integração com marketplaces."""


class WriteDisabledError(PermissionError):
    """Lançada quando uma operação de escrita é tentada com MARKETPLACE_WRITE_ENABLED=False."""


class CircuitBreakerOpenError(RuntimeError):
    """Lançada quando a conta está pausada pelo circuit-breaker após erros consecutivos."""


class MarketplaceAPIError(RuntimeError):
    """Lançada quando a API externa do marketplace retorna erro ou falha de comunicação."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: str | dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class ForbiddenPayloadFieldError(MarketplaceAPIError):
    """Lançada quando o payload de patch contém campos protegidos da WeDrop (SKU, preço, estoque, variações)."""


class DailyPatchQuotaExceededError(MarketplaceAPIError):
    """Lançada quando a conta atinge o limite diário max_daily_patches configurado."""
