"""Testes unitários do app accounts_vault (Criptografia Fernet, Circuit-Breaker e OAuth2 ML)."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .crypto import decrypt_secret, encrypt_secret
from .models import ChannelChoices, MarketplaceAccount
from .oauth_ml import (
    build_authorization_url,
    ensure_valid_token,
    exchange_code_for_tokens,
    fetch_ml_seller_profile,
)

TEST_FERNET_KEY = Fernet.generate_key().decode("utf-8")


@override_settings(
    FERNET_KEY=TEST_FERNET_KEY,
    ML_CLIENT_ID="123456789",
    ML_CLIENT_SECRET="ml-super-secret",
    ML_REDIRECT_URI="http://localhost:8000/vault/ml/callback/",
)
class AccountsVaultTests(TestCase):
    """Suíte de testes para criptografia em repouso, circuit-breaker e renovação OAuth."""

    def test_fernet_encrypt_and_decrypt_roundtrip(self) -> None:
        """Garante que um segredo criptografado retorna exatamente ao valor original."""
        original = "APP_USR-123456-secret-token"
        encrypted = encrypt_secret(original)
        self.assertNotEqual(encrypted, original)
        self.assertEqual(decrypt_secret(encrypted), original)

    @override_settings(FERNET_KEY="")
    def test_fernet_missing_key_raises_improperly_configured(self) -> None:
        """Deve recusar operação se FERNET_KEY estiver vazia."""
        with self.assertRaises(ImproperlyConfigured):
            encrypt_secret("qualquer-segredo")
        with self.assertRaises(ImproperlyConfigured):
            decrypt_secret("qualquer-token")

    @override_settings(FERNET_KEY="chave-invalida-nao-base64")
    def test_fernet_invalid_key_raises_improperly_configured(self) -> None:
        """Deve recusar operação se FERNET_KEY não for uma chave Fernet válida."""
        with self.assertRaises(ImproperlyConfigured):
            encrypt_secret("qualquer-segredo")

    def test_circuit_breaker_opens_on_third_consecutive_error(self) -> None:
        """O circuit-breaker deve pausar a conta exatamente no 3º erro consecutivo."""
        account = MarketplaceAccount.objects.create(
            name="ML Principal",
            channel=ChannelChoices.MERCADO_LIVRE,
        )

        # 1º erro
        account.record_api_error()
        account.refresh_from_db()
        self.assertEqual(account.consecutive_errors, 1)
        self.assertFalse(account.is_paused_by_circuit_breaker)

        # 2º erro
        account.record_api_error()
        account.refresh_from_db()
        self.assertEqual(account.consecutive_errors, 2)
        self.assertFalse(account.is_paused_by_circuit_breaker)

        # 3º erro -> aciona circuit-breaker
        account.record_api_error()
        account.refresh_from_db()
        self.assertEqual(account.consecutive_errors, 3)
        self.assertTrue(account.is_paused_by_circuit_breaker)

        # Reset do circuit-breaker
        account.reset_circuit_breaker()
        account.refresh_from_db()
        self.assertEqual(account.consecutive_errors, 0)
        self.assertFalse(account.is_paused_by_circuit_breaker)

    @patch("apps.accounts_vault.oauth_ml.httpx.Client")
    def test_ensure_valid_token_auto_refreshes_when_expiring_soon(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Deve renovar automaticamente o token quando faltar menos de 10 minutos para expirar."""
        account = MarketplaceAccount.objects.create(
            name="ML Loja 1",
            channel=ChannelChoices.MERCADO_LIVRE,
        )
        account.set_tokens(
            access_token="old-access-token",
            refresh_token="valid-refresh-token",
            expires_in_seconds=300,  # Faltam 5 min (< 600s)
        )
        self.assertTrue(account.is_token_expiring_soon(margin_seconds=600))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-refreshed-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 21600,
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        token = ensure_valid_token(account)
        self.assertEqual(token, "new-refreshed-access-token")

        account.refresh_from_db()
        self.assertEqual(account.get_access_token(), "new-refreshed-access-token")
        self.assertEqual(account.get_refresh_token(), "new-refresh-token")
        self.assertFalse(account.is_token_expiring_soon(margin_seconds=600))

    @patch("apps.accounts_vault.oauth_ml.httpx.Client")
    def test_ensure_valid_token_skips_refresh_when_token_is_fresh(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Não deve chamar a API de refresh se o token ainda tiver folga de validade."""
        account = MarketplaceAccount.objects.create(
            name="ML Loja 2",
            channel=ChannelChoices.MERCADO_LIVRE,
        )
        account.set_tokens(
            access_token="fresh-access-token",
            refresh_token="fresh-refresh-token",
            expires_in_seconds=7200,
        )
        token = ensure_valid_token(account)
        self.assertEqual(token, "fresh-access-token")
        mock_client_cls.assert_not_called()

    def test_build_authorization_url(self) -> None:
        """Valida construção da URL de autorização do Mercado Livre Brasil."""
        url = build_authorization_url(state="test-state-xyz")
        self.assertTrue(url.startswith("https://auth.mercadolivre.com.br/authorization?"))
        self.assertIn("client_id=123456789", url)
        self.assertIn("state=test-state-xyz", url)

    @patch("apps.accounts_vault.oauth_ml.httpx.Client")
    def test_oauth_connect_and_callback_flow(self, mock_client_cls: MagicMock) -> None:
        """Valida o fluxo completo das views /vault/ml/connect/ e /vault/ml/callback/."""
        User = get_user_model()
        admin_user = User.objects.create_superuser(
            username="admin_test",
            email="admin@example.com",
            password="password123",
        )
        self.client.force_login(admin_user)

        account = MarketplaceAccount.objects.create(
            name="ML Teste OAuth",
            channel=ChannelChoices.MERCADO_LIVRE,
        )

        connect_url = f"{reverse('accounts_vault:ml_connect')}?account_id={account.pk}"
        connect_res = self.client.get(connect_url)
        self.assertEqual(connect_res.status_code, 302)
        state = self.client.session["ml_oauth_state"]

        # Configura mocks para exchange_code_for_tokens (POST) e fetch_ml_seller_profile (GET)
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {
            "access_token": "callback-access-token",
            "refresh_token": "callback-refresh-token",
            "expires_in": 21600,
            "user_id": 99887766,
        }
        get_response = MagicMock()
        get_response.status_code = 200
        get_response.json.return_value = {
            "id": 99887766,
            "nickname": "LOJA_WEDROP_TESTE",
        }

        mock_http = MagicMock()
        mock_http.post.return_value = post_response
        mock_http.get.return_value = get_response
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_http
        mock_client_cls.return_value = mock_client

        callback_url = f"{reverse('accounts_vault:ml_callback')}?code=TG-CODE-123&state={state}"
        callback_res = self.client.get(callback_url)
        self.assertEqual(callback_res.status_code, 302)

        account.refresh_from_db()
        self.assertEqual(account.seller_id, "99887766")
        self.assertEqual(account.get_access_token(), "callback-access-token")
        self.assertEqual(account.get_refresh_token(), "callback-refresh-token")
        self.assertIsNotNone(account.token_expires_at)
        self.assertGreater(account.token_expires_at, timezone.now() + timedelta(hours=5))
