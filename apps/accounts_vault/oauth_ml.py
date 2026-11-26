"""Serviço de autenticação e renovação OAuth 2.0 para o Mercado Livre."""
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .models import MarketplaceAccount

ML_AUTH_BASE_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_USERS_ME_URL = "https://api.mercadolibre.com/users/me"
DEFAULT_HTTP_TIMEOUT = 15.0


class MercadoLivreOAuthError(Exception):
    """Erro durante troca, renovação de token ou consulta de perfil OAuth no Mercado Livre."""


def _validate_ml_oauth_settings(require_secret: bool = False) -> None:
    """Garante que as variáveis de ambiente do app Mercado Livre estão configuradas."""
    if not getattr(settings, "ML_CLIENT_ID", "") or not getattr(
        settings, "ML_REDIRECT_URI", ""
    ):
        raise ImproperlyConfigured(
            "ML_CLIENT_ID e ML_REDIRECT_URI devem estar configurados no arquivo .env."
        )
    if require_secret and not getattr(settings, "ML_CLIENT_SECRET", ""):
        raise ImproperlyConfigured(
            "ML_CLIENT_SECRET deve estar configurado no arquivo .env."
        )


def build_authorization_url(state: str) -> str:
    """Constrói a URL de autorização OAuth2 do Mercado Livre Brasil."""
    _validate_ml_oauth_settings(require_secret=False)
    params = {
        "response_type": "code",
        "client_id": settings.ML_CLIENT_ID,
        "redirect_uri": settings.ML_REDIRECT_URI,
        "state": state,
    }
    return f"{ML_AUTH_BASE_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """Troca o authorization code recebido no callback por access_token e refresh_token."""
    _validate_ml_oauth_settings(require_secret=True)
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.ML_CLIENT_ID,
        "client_secret": settings.ML_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.ML_REDIRECT_URI,
    }
    try:
        with httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = client.post(
                ML_TOKEN_URL,
                data=payload,
                headers={"Accept": "application/json"},
            )
    except httpx.RequestError as exc:
        raise MercadoLivreOAuthError(
            f"Falha de rede ao trocar authorization code no Mercado Livre: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise MercadoLivreOAuthError(
            f"Erro HTTP {response.status_code} ao trocar code por token: {response.text}"
        )
    return response.json()


def refresh_account_token(account: MarketplaceAccount) -> str:
    """Renova o token OAuth da conta usando grant_type=refresh_token e persiste no cofre."""
    _validate_ml_oauth_settings(require_secret=True)
    refresh_token = account.get_refresh_token()
    if not refresh_token:
        raise MercadoLivreOAuthError(
            f"A conta '{account.name}' não possui refresh_token armazenado."
        )

    payload = {
        "grant_type": "refresh_token",
        "client_id": settings.ML_CLIENT_ID,
        "client_secret": settings.ML_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    try:
        with httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = client.post(
                ML_TOKEN_URL,
                data=payload,
                headers={"Accept": "application/json"},
            )
    except httpx.RequestError as exc:
        account.record_api_error()
        raise MercadoLivreOAuthError(
            f"Falha de rede ao renovar token da conta '{account.name}': {exc}"
        ) from exc

    if response.status_code >= 400:
        account.record_api_error()
        raise MercadoLivreOAuthError(
            f"Erro HTTP {response.status_code} ao renovar token: {response.text}"
        )

    data = response.json()
    new_access_token = data["access_token"]
    new_refresh_token = data.get("refresh_token") or refresh_token
    expires_in = int(data.get("expires_in", 21600))

    account.set_tokens(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        expires_in_seconds=expires_in,
    )
    return new_access_token


def ensure_valid_token(account: MarketplaceAccount) -> str:
    """Retorna um access_token válido, renovando-o caso falte menos de 10 minutos para expirar."""
    if account.is_token_expiring_soon(margin_seconds=600):
        return refresh_account_token(account)
    return account.get_access_token()


def fetch_ml_seller_profile(access_token: str) -> dict:
    """Consulta os dados da conta autenticada em GET /users/me."""
    try:
        with httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT) as client:
            response = client.get(
                ML_USERS_ME_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
    except httpx.RequestError as exc:
        raise MercadoLivreOAuthError(
            f"Falha de rede ao consultar perfil /users/me no Mercado Livre: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise MercadoLivreOAuthError(
            f"Erro HTTP {response.status_code} ao consultar /users/me: {response.text}"
        )
    return response.json()
