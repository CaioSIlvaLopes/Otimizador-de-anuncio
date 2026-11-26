"""Testes unitários do app marketplaces (MercadoLivreAdapter, trava de escrita e circuit-breaker)."""
from io import StringIO
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount

from .exceptions import (
    CircuitBreakerOpenError,
    MarketplaceAPIError,
    WriteDisabledError,
)
from .mercadolivre import MercadoLivreAdapter

TEST_FERNET_KEY = Fernet.generate_key().decode("utf-8")


@override_settings(
    FERNET_KEY=TEST_FERNET_KEY,
    MARKETPLACE_WRITE_ENABLED=False,
    ML_CLIENT_ID="123456789",
    ML_CLIENT_SECRET="ml-super-secret",
    ML_REDIRECT_URI="http://localhost:8000/vault/ml/callback/",
)
class MercadoLivreAdapterTests(TestCase):
    """Suíte de testes para o MercadoLivreAdapter sem chamadas reais à internet."""

    def setUp(self) -> None:
        self.account = MarketplaceAccount.objects.create(
            name="ML Principal",
            channel=ChannelChoices.MERCADO_LIVRE,
            seller_id="11223344",
        )
        self.account.set_tokens(
            access_token="valid-access-token",
            refresh_token="valid-refresh-token",
            expires_in_seconds=21600,
        )
        self.adapter = MercadoLivreAdapter(account=self.account)

    def test_write_methods_raise_write_disabled_error_when_flag_is_false(self) -> None:
        """patch_content e upload_media devem lançar WriteDisabledError quando MARKETPLACE_WRITE_ENABLED=False."""
        with self.assertRaises(WriteDisabledError):
            self.adapter.patch_content("MLB123456", {"title": "Novo Título"})

        with self.assertRaises(WriteDisabledError):
            self.adapter.upload_media("/tmp/card.png")

    @override_settings(MARKETPLACE_WRITE_ENABLED=True)
    def test_write_methods_raise_not_implemented_in_phase_1_when_enabled(self) -> None:
        """Se MARKETPLACE_WRITE_ENABLED=True na Fase 1, deve lançar NotImplementedError (Fase 3)."""
        with self.assertRaises(NotImplementedError):
            self.adapter.patch_content("MLB123456", {"title": "Novo Título"})

        with self.assertRaises(NotImplementedError):
            self.adapter.upload_media("/tmp/card.png")

    def test_blocked_when_circuit_breaker_is_open(self) -> None:
        """Nenhuma requisição pode ser disparada se is_paused_by_circuit_breaker=True."""
        self.account.is_paused_by_circuit_breaker = True
        self.account.save()

        with self.assertRaises(CircuitBreakerOpenError):
            self.adapter.fetch_recent_listings()

        with self.assertRaises(CircuitBreakerOpenError):
            self.adapter.get_listing_details("MLB123456")

    @patch("apps.marketplaces.mercadolivre.httpx.Client")
    def test_fetch_recent_listings_parses_results(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Valida busca e parsing dos IDs de anúncios recentes."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "seller_id": "11223344",
            "results": ["MLB100001", "MLB100002"],
        }
        mock_http = MagicMock()
        mock_http.get.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_http

        listings = self.adapter.fetch_recent_listings(limit=10, offset=0)
        self.assertEqual(listings, ["MLB100001", "MLB100002"])
        mock_http.get.assert_called_once_with(
            "/users/11223344/items/search",
            params={"orders": "start_time_desc", "limit": 10, "offset": 0},
        )

    @patch("apps.marketplaces.mercadolivre.httpx.Client")
    def test_get_listing_details_includes_all_attributes(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Valida consulta de detalhes do anúncio com include_attributes=all."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "MLB100001",
            "title": "Fone Bluetooth TWS",
            "seller_custom_field": "WD-SKU-999",
            "sold_quantity": 4,
            "category_id": "MLB1055",
            "status": "active",
        }
        mock_http = MagicMock()
        mock_http.get.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_http

        details = self.adapter.get_listing_details("MLB100001")
        self.assertEqual(details["id"], "MLB100001")
        self.assertEqual(details["seller_custom_field"], "WD-SKU-999")
        mock_http.get.assert_called_once_with(
            "/items/MLB100001",
            params={"include_attributes": "all"},
        )

    @patch("apps.marketplaces.mercadolivre.httpx.Client")
    def test_get_listing_description_handles_200_and_404(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Deve retornar plain_text normal em 200 e {'plain_text': ''} quando a API retorna 404."""
        mock_404 = MagicMock()
        mock_404.status_code = 404
        mock_http = MagicMock()
        mock_http.get.return_value = mock_404
        mock_client_cls.return_value.__enter__.return_value = mock_http

        desc_empty = self.adapter.get_listing_description("MLB100001")
        self.assertEqual(desc_empty, {"plain_text": ""})
        self.account.refresh_from_db()
        self.assertEqual(self.account.consecutive_errors, 0)

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = {"plain_text": "Descrição completa do produto."}
        mock_http.get.return_value = mock_200

        desc_ok = self.adapter.get_listing_description("MLB100001")
        self.assertEqual(desc_ok["plain_text"], "Descrição completa do produto.")

    @patch("apps.marketplaces.mercadolivre.httpx.Client")
    def test_get_category_attributes(self, mock_client_cls: MagicMock) -> None:
        """Valida consulta de atributos técnicos da categoria."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": "BRAND", "name": "Marca"},
            {"id": "MODEL", "name": "Modelo"},
        ]
        mock_http = MagicMock()
        mock_http.get.return_value = mock_response
        mock_client_cls.return_value.__enter__.return_value = mock_http

        attrs = self.adapter.get_category_attributes("MLB1055")
        self.assertEqual(len(attrs), 2)
        self.assertEqual(attrs[0]["id"], "BRAND")

    @patch("apps.marketplaces.mercadolivre.httpx.Client")
    def test_api_errors_trigger_circuit_breaker_on_third_failure(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Erros 429/5xx consecutivos no adaptador devem acionar o circuit-breaker na 3ª falha."""
        mock_503 = MagicMock()
        mock_503.status_code = 503
        mock_503.text = "Service Unavailable"
        mock_http = MagicMock()
        mock_http.get.return_value = mock_503
        mock_client_cls.return_value.__enter__.return_value = mock_http

        for expected_errors in (1, 2, 3):
            with self.assertRaises(MarketplaceAPIError):
                self.adapter.get_listing_details("MLB100001")
            self.account.refresh_from_db()
            self.assertEqual(self.account.consecutive_errors, expected_errors)

        self.assertTrue(self.account.is_paused_by_circuit_breaker)

        # A 4ª tentativa deve ser barrada imediatamente pelo CircuitBreakerOpenError
        with self.assertRaises(CircuitBreakerOpenError):
            self.adapter.get_listing_details("MLB100001")

    @patch("apps.marketplaces.mercadolivre.httpx.Client")
    def test_management_command_test_ml_connection(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Valida a saída do comando python manage.py test_ml_connection."""
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {"results": ["MLB999001"]}

        item_resp = MagicMock()
        item_resp.status_code = 200
        item_resp.json.return_value = {
            "id": "MLB999001",
            "title": "Teclado Mecânico Gamer",
            "seller_custom_field": "WEDROP-TK-01",
            "sold_quantity": 12,
            "category_id": "MLB1648",
            "status": "active",
        }

        mock_http = MagicMock()
        mock_http.get.side_effect = [search_resp, item_resp]
        mock_client_cls.return_value.__enter__.return_value = mock_http

        out = StringIO()
        call_command(
            "test_ml_connection",
            f"--account-id={self.account.pk}",
            "--limit=1",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("MLB999001", output)
        self.assertIn("WEDROP-TK-01", output)
        self.assertIn("Teclado Mecânico Gamer", output)
