"""Testes unitários do app catalog (WatcherService, Pre-Flight Check, Publicação, Cota Diária e Rollback)."""
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount
from apps.catalog.models import (
    Listing,
    ListingSnapshot,
    ListingStatus,
    OptimizationProposal,
    ProposalStatus,
)
from apps.catalog.services.publisher import (
    build_safe_patch_payload,
    publish_proposal,
    simulate_proposal_patch,
)
from apps.catalog.services.rollback import rollback_listing_to_baseline
from apps.catalog.services.watcher import sync_account_listings
from apps.governance.models import DailyQuotaLog
from apps.marketplaces.exceptions import DailyPatchQuotaExceededError


class CatalogWatcherAndSnapshotTests(TestCase):
    """Suíte de testes para Listing, ListingSnapshot, Watcher, Pre-Flight, Publisher e Rollback."""

    def setUp(self) -> None:
        self.account = MarketplaceAccount.objects.create(
            name="ML Principal WeDrop",
            channel=ChannelChoices.MERCADO_LIVRE,
            seller_id="99887766",
            max_daily_patches=2,
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

    def test_preflight_check_omits_title_when_sold_quantity_greater_than_zero(
        self,
    ) -> None:
        """Pre-Flight Check deve remover automaticamente title quando sold_quantity > 0."""
        listing = Listing.objects.create(
            account=self.account,
            external_id="MLB6001",
            original_title="Fone Original",
            sold_quantity=3,
        )
        snapshot = ListingSnapshot.objects.create(
            listing=listing,
            raw_item_json={"id": "MLB6001", "title": "Fone Original"},
            raw_description_json={"plain_text": "Desc original"},
            category_attributes_json=[],
            is_baseline=True,
        )
        proposal = OptimizationProposal.objects.create(
            listing=listing,
            baseline_snapshot=snapshot,
            proposed_title="Fone Bluetooth 5.3 TWS Esportivo",
            proposed_description="Nova descrição otimizada.",
            proposed_attributes_json=[
                {"id": "BRAND", "name": "Marca", "value_name": "Genérica"}
            ],
        )

        payload, warnings = build_safe_patch_payload(proposal)
        self.assertNotIn("title", payload)
        self.assertEqual(payload["description"], "Nova descrição otimizada.")
        self.assertEqual(
            payload["attributes"],
            [{"id": "BRAND", "value_name": "Genérica"}],
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("Título omitido automaticamente no Pre-Flight", warnings[0])

        sim = simulate_proposal_patch(proposal)
        self.assertTrue(sim["dry_run"])
        self.assertNotIn("title", sim["payload"])

    @override_settings(MARKETPLACE_WRITE_ENABLED=True)
    @patch("apps.catalog.services.publisher.get_adapter_for_account")
    def test_publish_proposal_enforces_max_daily_patches_quota(
        self,
        mock_get_adapter: MagicMock,
    ) -> None:
        """Deve bloquear envio quando a loja atingir max_daily_patches no DailyQuotaLog."""
        listing = Listing.objects.create(
            account=self.account,
            external_id="MLB7001",
            original_title="Produto Cota",
            sold_quantity=0,
        )
        snapshot = ListingSnapshot.objects.create(
            listing=listing,
            raw_item_json={"id": "MLB7001", "title": "Produto Cota"},
            raw_description_json={"plain_text": "Desc"},
            category_attributes_json=[],
            is_baseline=True,
        )
        proposal = OptimizationProposal.objects.create(
            listing=listing,
            baseline_snapshot=snapshot,
            proposed_title="Produto Cota Otimizado",
            proposed_description="Descrição nova",
        )

        mock_adapter = MagicMock()
        mock_adapter.get_listing_details.return_value = {
            "id": "MLB7001",
            "sold_quantity": 0,
            "status": "active",
        }
        mock_adapter.patch_content.return_value = {"item_updated": True}
        mock_get_adapter.return_value = mock_adapter

        # max_daily_patches=2 na conta criada no setUp
        publish_proposal(proposal)
        publish_proposal(proposal)

        quota = DailyQuotaLog.get_today()
        self.assertEqual(quota.get_account_patches_count(self.account), 2)
        self.assertFalse(quota.can_send_patch(self.account))

        # 3ª tentativa no mesmo dia deve lançar DailyPatchQuotaExceededError
        with self.assertRaises(DailyPatchQuotaExceededError):
            publish_proposal(proposal)

    @override_settings(MARKETPLACE_WRITE_ENABLED=True)
    @patch("apps.catalog.services.rollback.get_adapter_for_account")
    def test_rollback_listing_to_baseline_strips_forbidden_fields_and_respects_sold_quantity(
        self,
        mock_get_adapter: MagicMock,
    ) -> None:
        """Rollback deve restaurar snapshot baseline sem campos proibidos e respeitar sold_quantity > 0."""
        listing = Listing.objects.create(
            account=self.account,
            external_id="MLB8001",
            original_title="Título Original Baseline",
            sold_quantity=4,
            status=ListingStatus.PUBLISHED,
        )
        ListingSnapshot.objects.create(
            listing=listing,
            raw_item_json={
                "id": "MLB8001",
                "title": "Título Original Baseline",
                "price": 99.90,
                "available_quantity": 10,
                "seller_custom_field": "WD-RB-01",
                "attributes": [
                    {"id": "BRAND", "value_name": "Marca Original"},
                    {"id": "SELLER_SKU", "value_name": "WD-RB-01"},
                    {"id": "RO_ATTR", "value_name": "Valor Read Only"},
                ],
            },
            raw_description_json={"plain_text": "Descrição original baseline."},
            category_attributes_json=[
                {"id": "BRAND", "name": "Marca", "tags": {}},
                {"id": "SELLER_SKU", "name": "SKU", "tags": {"hidden": True}},
                {"id": "RO_ATTR", "name": "RO", "tags": {"read_only": True}},
            ],
            is_baseline=True,
        )

        # 1. Dry-run
        dry = rollback_listing_to_baseline(listing, dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertNotIn("title", dry["payload"])
        self.assertNotIn("price", dry["payload"])
        self.assertNotIn("seller_custom_field", dry["payload"])
        self.assertEqual(
            dry["payload"]["description"],
            "Descrição original baseline.",
        )
        self.assertEqual(
            dry["payload"]["attributes"],
            [{"id": "BRAND", "value_name": "Marca Original"}],
        )

        # 2. Execução real com mock adapter
        mock_adapter = MagicMock()
        mock_adapter.get_listing_details.return_value = {
            "id": "MLB8001",
            "sold_quantity": 4,
            "status": "active",
        }
        mock_adapter.patch_content.return_value = {"description_updated": True}
        mock_get_adapter.return_value = mock_adapter

        live_rb = rollback_listing_to_baseline(listing, dry_run=False)
        self.assertFalse(live_rb["dry_run"])
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.DETECTED)
        mock_adapter.patch_content.assert_called_once()
