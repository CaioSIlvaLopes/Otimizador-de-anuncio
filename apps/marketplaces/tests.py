"""Testes unitários do app marketplaces (MercadoLivreAdapter, sanitizer WeDrop, patch_content e circuit-breaker)."""
from io import StringIO
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount

from .exceptions import (
    CircuitBreakerOpenError,
    ForbiddenPayloadFieldError,
    MarketplaceAPIError,
    WriteDisabledError,
)
from .mercadolivre import MercadoLivreAdapter
from .sanitizer import sanitize_patch_payload

TEST_FERNET_KEY = Fernet.generate_key().decode("utf-8")


@override_settings(
    FERNET_KEY=TEST_FERNET_KEY,
    MARKETPLACE_WRITE_ENABLED=False,
    ML_CLIENT_ID="123456789",
    ML_CLIENT_SECRET="ml-super-secret",
    ML_REDIRECT_URI="http://localhost:8000/vault/ml/callback/",
)
class MercadoLivreAdapterTests(TestCase):
    """Suíte de testes para o MercadoLivreAdapter e sanitizador de whitelist WeDrop."""

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

    def test_sanitize_patch_payload_blocks_forbidden_wedrop_fields(self) -> None:
        """Deve lançar ForbiddenPayloadFieldError ao tentar enviar price, stock, variations ou SKU."""
        for forbidden_key in (
            "price",
            "available_quantity",
            "stock",
            "variations",
            "seller_custom_field",
            "seller_sku",
        ):
            with self.assertRaises(ForbiddenPayloadFieldError):
                sanitize_patch_payload(
                    {
                        "title": "Título Válido",
                        forbidden_key: "valor-proibido",
                    }
                )

    def test_sanitize_patch_payload_strips_seller_sku_and_non_whitelisted_keys(
        self,
    ) -> None:
        """Deve remover SELLER_SKU de attributes e descartar chaves fora de ALLOWED_PATCH_FIELDS."""
        raw = {
            "title": "Mini Processador USB 250ml Inox",
            "description": "Descrição limpa",
            "extra_untrusted_field": "descartado",
            "attributes": [
                {"id": "BRAND", "value_name": "Genérica"},
                {"id": "SELLER_SKU", "value_name": "WD-PROC-250"},
            ],
        }
        cleaned = sanitize_patch_payload(raw)
        self.assertEqual(
            set(cleaned.keys()),
            {"title", "description", "attributes"},
        )
        self.assertNotIn("extra_untrusted_field", cleaned)
        self.assertEqual(
            cleaned["attributes"],
            [{"id": "BRAND", "value_name": "Genérica"}],
        )

    def test_write_methods_raise_write_disabled_error_when_flag_is_false(self) -> None:
        """patch_content e upload_media devem lançar WriteDisabledError quando MARKETPLACE_WRITE_ENABLED=False."""
        with self.assertRaises(WriteDisabledError):
            self.adapter.patch_content("MLB123456", {"title": "Novo Título"})

        with self.assertRaises(WriteDisabledError):
            self.adapter.upload_media("/tmp/card.png")

    @override_settings(MARKETPLACE_WRITE_ENABLED=True)
    @patch("apps.marketplaces.mercadolivre.httpx.Client")
    def test_patch_content_dispatches_separate_put_requests_for_item_and_description(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Quando MARKETPLACE_WRITE_ENABLED=True, deve enviar PUT /items/{id} e PUT /items/{id}/description."""
        put_item_resp = MagicMock()
        put_item_resp.status_code = 200
        put_item_resp.content = b'{"id": "MLB123456"}'
        put_item_resp.json.return_value = {"id": "MLB123456"}

        put_desc_resp = MagicMock()
        put_desc_resp.status_code = 200
        put_desc_resp.content = b'{"plain_text": "Nova desc"}'
        put_desc_resp.json.return_value = {"plain_text": "Nova desc"}

        mock_http = MagicMock()
        mock_http.put.side_effect = [put_item_resp, put_desc_resp]
        mock_client_cls.return_value.__enter__.return_value = mock_http

        result = self.adapter.patch_content(
            "MLB123456",
            {
                "title": "Novo Título Otimizado",
                "description": "Nova descrição em texto puro",
                "attributes": [
                    {"id": "BRAND", "value_name": "Genérica"},
                    {"id": "SELLER_SKU", "value_name": "DEVE-SUMIR"},
                ],
            },
        )

        self.assertTrue(result["item_updated"])
        self.assertTrue(result["description_updated"])
        self.assertEqual(mock_http.put.call_count, 2)

        first_call = mock_http.put.call_args_list[0]
        self.assertEqual(first_call.args[0], "/items/MLB123456")
        self.assertEqual(
            first_call.kwargs["json"],
            {
                "title": "Novo Título Otimizado",
                "attributes": [{"id": "BRAND", "value_name": "Genérica"}],
            },
        )
        self.assertNotIn("description", first_call.kwargs["json"])

        second_call = mock_http.put.call_args_list[1]
        self.assertEqual(second_call.args[0], "/items/MLB123456/description")
        self.assertEqual(
            second_call.kwargs["json"],
            {"plain_text": "Nova descrição em texto puro"},
        )

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
        mock_response.content = b'{"results": ["MLB100001", "MLB100002"]}'
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
        mock_response.content = b'{"id": "MLB100001"}'
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
        mock_200.content = b'{"plain_text": "Desc"}'
        mock_200.json.return_value = {"plain_text": "Descrição completa do produto."}
        mock_http.get.return_value = mock_200

        desc_ok = self.adapter.get_listing_description("MLB100001")
        self.assertEqual(desc_ok["plain_text"], "Descrição completa do produto.")

    @patch("apps.marketplaces.mercadolivre.httpx.Client")
    def test_get_category_attributes(self, mock_client_cls: MagicMock) -> None:
        """Valida consulta de atributos técnicos da categoria."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'[{"id": "BRAND"}]'
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
        search_resp.content = b'{"results": ["MLB999001"]}'
        search_resp.json.return_value = {"results": ["MLB999001"]}

        item_resp = MagicMock()
        item_resp.status_code = 200
        item_resp.content = b'{"id": "MLB999001"}'
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
