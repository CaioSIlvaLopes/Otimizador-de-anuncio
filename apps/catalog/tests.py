"""Testes unitários do app catalog (WatcherService, idempotência e imutabilidade de ListingSnapshot)."""
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase

from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount
from apps.catalog.models import Listing, ListingSnapshot, ListingStatus
from apps.catalog.services.watcher import sync_account_listings


class CatalogWatcherAndSnapshotTests(TestCase):
    """Suíte de testes da Fase 1 (Itens 6 e 7) para Listing, ListingSnapshot e Watcher."""

    def setUp(self) -> None:
        self.account = MarketplaceAccount.objects.create(
            name="ML Principal WeDrop",
            channel=ChannelChoices.MERCADO_LIVRE,
            seller_id="99887766",
            is_active=True,
        )

    def _build_mock_adapter(
        self,
        recent_ids: list[str],
        details_by_id: dict[str, dict],
        descriptions_by_id: dict[str, dict] | None = None,
        category_attrs: list[dict] | None = None,
    ) -> MagicMock:
        adapter = MagicMock()
        adapter.fetch_recent_listings.return_value = recent_ids
        adapter.get_listing_details.side_effect = lambda ext_id: details_by_id[ext_id]
        if descriptions_by_id is not None:
            adapter.get_listing_description.side_effect = (
                lambda ext_id: descriptions_by_id.get(ext_id, {"plain_text": ""})
            )
        else:
            adapter.get_listing_description.return_value = {
                "plain_text": "Descrição original da WeDrop"
            }
        adapter.get_category_attributes.return_value = category_attrs or [
            {"id": "BRAND", "name": "Marca"},
        ]
        return adapter

    @patch("apps.catalog.services.watcher.get_adapter_for_account")
    def test_atomic_creation_of_listing_and_baseline_snapshot(
        self,
        mock_get_adapter: MagicMock,
    ) -> None:
        """Deve criar Listing + ListingSnapshot(is_baseline=True) para um anúncio novo."""
        item_payload = {
            "id": "MLB1001",
            "title": "Cafeteira Expresso Portátil",
            "category_id": "MLB1234",
            "seller_custom_field": "WD-CAFE-01",
            "sold_quantity": 0,
            "status": "active",
        }
        mock_adapter = self._build_mock_adapter(
            recent_ids=["MLB1001"],
            details_by_id={"MLB1001": item_payload},
        )
        mock_get_adapter.return_value = mock_adapter

        summary = sync_account_listings(self.account, limit=5)

        self.assertEqual(summary["detected"], 1)
        self.assertEqual(summary["updated_existing"], 0)
        self.assertEqual(summary["errors"], [])

        listing = Listing.objects.get(account=self.account, external_id="MLB1001")
        self.assertEqual(listing.sku_readonly, "WD-CAFE-01")
        self.assertEqual(listing.original_title, "Cafeteira Expresso Portátil")
        self.assertEqual(listing.category_id, "MLB1234")
        self.assertEqual(listing.sold_quantity, 0)
        self.assertEqual(listing.status, ListingStatus.DETECTED)

        snapshots = list(listing.snapshots.all())
        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[0]
        self.assertTrue(snapshot.is_baseline)
        self.assertEqual(snapshot.raw_item_json["id"], "MLB1001")
        self.assertEqual(
            snapshot.raw_description_json["plain_text"],
            "Descrição original da WeDrop",
        )
        self.assertEqual(snapshot.category_attributes_json[0]["id"], "BRAND")

    @patch("apps.catalog.services.watcher.get_adapter_for_account")
    def test_sku_extraction_from_seller_custom_field_and_seller_sku_attribute(
        self,
        mock_get_adapter: MagicMock,
    ) -> None:
        """Deve extrair sku_readonly tanto de seller_custom_field quanto do atributo SELLER_SKU."""
        details_by_id = {
            "MLB2001": {
                "id": "MLB2001",
                "title": "Produto com seller_custom_field",
                "category_id": "MLB555",
                "seller_custom_field": "SKU-DIRECT-10",
                "sold_quantity": 1,
                "status": "active",
            },
            "MLB2002": {
                "id": "MLB2002",
                "title": "Produto com atributo SELLER_SKU",
                "category_id": "MLB555",
                "seller_custom_field": None,
                "attributes": [
                    {"id": "BRAND", "value_name": "Genérica"},
                    {"id": "SELLER_SKU", "value_name": "SKU-ATTR-20"},
                ],
                "sold_quantity": 0,
                "status": "active",
            },
        }
        mock_adapter = self._build_mock_adapter(
            recent_ids=["MLB2001", "MLB2002"],
            details_by_id=details_by_id,
        )
        mock_get_adapter.return_value = mock_adapter

        summary = sync_account_listings(self.account, limit=5)
        self.assertEqual(summary["detected"], 2)

        l1 = Listing.objects.get(external_id="MLB2001")
        l2 = Listing.objects.get(external_id="MLB2002")
        self.assertEqual(l1.sku_readonly, "SKU-DIRECT-10")
        self.assertEqual(l2.sku_readonly, "SKU-ATTR-20")

        # Verifica cache em memória: mesma categoria MLB555 consultada apenas 1 vez
        mock_adapter.get_category_attributes.assert_called_once_with("MLB555")

    @patch("apps.catalog.services.watcher.get_adapter_for_account")
    def test_watcher_idempotency_updates_sold_quantity_without_duplicating_snapshot(
        self,
        mock_get_adapter: MagicMock,
    ) -> None:
        """Rodar o Watcher 2 vezes não duplica Listing nem Snapshot, mas atualiza sold_quantity."""
        first_payload = {
            "id": "MLB3001",
            "title": "Fone Sem Fio",
            "category_id": "MLB1055",
            "seller_custom_field": "WD-FONE-3",
            "sold_quantity": 0,
            "status": "active",
        }
        second_payload = {
            **first_payload,
            "sold_quantity": 5,
            "status": "paused",
        }

        mock_adapter = self._build_mock_adapter(
            recent_ids=["MLB3001"],
            details_by_id={"MLB3001": first_payload},
        )
        mock_get_adapter.return_value = mock_adapter

        first_run = sync_account_listings(self.account, limit=5)
        self.assertEqual(first_run["detected"], 1)
        self.assertEqual(first_run["updated_existing"], 0)

        # 2ª execução com sold_quantity=5 e status=paused
        mock_adapter.get_listing_details.side_effect = lambda _: second_payload
        second_run = sync_account_listings(self.account, limit=5)
        self.assertEqual(second_run["detected"], 0)
        self.assertEqual(second_run["updated_existing"], 1)

        self.assertEqual(Listing.objects.count(), 1)
        self.assertEqual(ListingSnapshot.objects.count(), 1)

        listing = Listing.objects.get(external_id="MLB3001")
        self.assertEqual(listing.sold_quantity, 5)
        self.assertEqual(listing.marketplace_status, "paused")

    def test_listing_snapshot_is_strictly_immutable(self) -> None:
        """Tentar alterar e salvar ou excluir um ListingSnapshot existente deve lançar ValueError."""
        listing = Listing.objects.create(
            account=self.account,
            external_id="MLB4001",
            original_title="Item Teste Imutável",
        )
        snapshot = ListingSnapshot.objects.create(
            listing=listing,
            raw_item_json={"id": "MLB4001", "title": "Original"},
            raw_description_json={"plain_text": "Texto original"},
            category_attributes_json=[],
            is_baseline=True,
        )

        snapshot.raw_item_json = {"id": "MLB4001", "title": "Adulterado"}
        with self.assertRaises(ValueError):
            snapshot.save()

        with self.assertRaises(ValueError):
            snapshot.delete()

    @patch("apps.catalog.services.watcher.get_adapter_for_account")
    def test_watcher_skips_account_when_circuit_breaker_is_open(
        self,
        mock_get_adapter: MagicMock,
    ) -> None:
        """Contas com is_paused_by_circuit_breaker=True devem ser puladas sem chamar o adapter."""
        self.account.is_paused_by_circuit_breaker = True
        self.account.save()

        summary = sync_account_listings(self.account, limit=5)
        self.assertEqual(summary["detected"], 0)
        self.assertEqual(summary["skipped"], 1)
        mock_get_adapter.assert_not_called()

    @patch("apps.catalog.services.watcher.get_adapter_for_account")
    def test_run_watcher_management_command(
        self,
        mock_get_adapter: MagicMock,
    ) -> None:
        """Valida execução do comando CLI python manage.py run_watcher."""
        mock_adapter = self._build_mock_adapter(
            recent_ids=["MLB5001"],
            details_by_id={
                "MLB5001": {
                    "id": "MLB5001",
                    "title": "Mouse Gamer RGB",
                    "category_id": "MLB1714",
                    "seller_custom_field": "WD-MOUSE-9",
                    "sold_quantity": 2,
                    "status": "active",
                }
            },
        )
        mock_get_adapter.return_value = mock_adapter

        out = StringIO()
        call_command(
            "run_watcher",
            f"--account-id={self.account.pk}",
            "--limit=5",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("Novos detectados: 1", output)
        self.assertIn("MLB5001", output)
        self.assertIn("WD-MOUSE-9", output)
