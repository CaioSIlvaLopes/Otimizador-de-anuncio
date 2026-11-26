"""Views OAuth2 do cofre de contas para conexão com o Mercado Livre."""
import secrets

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET

from .models import MarketplaceAccount
from .oauth_ml import (
    MercadoLivreOAuthError,
    build_authorization_url,
    exchange_code_for_tokens,
    fetch_ml_seller_profile,
)

SESSION_OAUTH_STATE_KEY = "ml_oauth_state"
SESSION_OAUTH_ACCOUNT_ID_KEY = "ml_oauth_account_id"


@staff_member_required
@require_GET
def ml_connect(request: HttpRequest) -> HttpResponse:
    """Inicia o fluxo OAuth2 do Mercado Livre salvando state criptograficamente seguro na sessão."""
    account_id = request.GET.get("account_id")
    if not account_id:
        return HttpResponseBadRequest("Parâmetro 'account_id' é obrigatório.")

    account = get_object_or_404(MarketplaceAccount, pk=account_id)
    state = secrets.token_urlsafe(32)
    request.session[SESSION_OAUTH_STATE_KEY] = state
    request.session[SESSION_OAUTH_ACCOUNT_ID_KEY] = account.pk

    auth_url = build_authorization_url(state=state)
    return HttpResponseRedirect(auth_url)


@staff_member_required
@require_GET
def ml_callback(request: HttpRequest) -> HttpResponse:
    """Processa o retorno OAuth2 do Mercado Livre, salva tokens no cofre e obtém o seller_id."""
    expected_state = request.session.pop(SESSION_OAUTH_STATE_KEY, None)
    account_id = request.session.pop(SESSION_OAUTH_ACCOUNT_ID_KEY, None)
    received_state = request.GET.get("state")
    code = request.GET.get("code")

    if (
        not expected_state
        or not received_state
        or not secrets.compare_digest(str(expected_state), str(received_state))
    ):
        return HttpResponseBadRequest("State OAuth inválido ou expirado.")

    if not code or not account_id:
        return HttpResponseBadRequest("Código de autorização ou conta ausente na sessão.")

    account = get_object_or_404(MarketplaceAccount, pk=account_id)
    admin_url = reverse("admin:accounts_vault_marketplaceaccount_changelist")

    try:
        token_data = exchange_code_for_tokens(code=code)
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "")
        expires_in = int(token_data.get("expires_in", 21600))

        profile = fetch_ml_seller_profile(access_token=access_token)
        seller_id = str(profile.get("id") or token_data.get("user_id") or "")

        account.set_tokens(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in_seconds=expires_in,
        )
        account.seller_id = seller_id
        account.consecutive_errors = 0
        account.is_paused_by_circuit_breaker = False
        account.save(
            update_fields=[
                "seller_id",
                "consecutive_errors",
                "is_paused_by_circuit_breaker",
                "updated_at",
            ]
        )
    except MercadoLivreOAuthError as exc:
        messages.error(
            request,
            f"Falha ao conectar conta '{account.name}' ao Mercado Livre: {exc}",
        )
        return HttpResponseRedirect(admin_url)

    messages.success(
        request,
        f"Conta '{account.name}' conectada com sucesso ao Mercado Livre (Seller ID: {seller_id}).",
    )
    return HttpResponseRedirect(admin_url)
