"""Testes unitários rápidos e determinísticos do app media_studio (com mocks para rembg e Playwright)."""
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.test import TestCase
from PIL import Image

from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount
from apps.catalog.models import (
    Listing,
    ListingSnapshot,
    ListingStatus,
    OptimizationProposal,
    ProposalStatus,
)
from apps.governance.models import DailyQuotaLog
from apps.media_studio.models import (
    MediaAsset,
    MediaAssetStatus,
    MediaAssetType,
)
from apps.media_studio.services.card_studio import (
    build_benefits_card_html,
    build_specs_card_html,
)
from apps.media_studio.services.cover_studio import (
    build_synthetic_sample_image,
    build_white_bg_cover,
    save_cutout_png,
)
from apps.media_studio.services.pipeline import generate_images_for_proposal
from apps.media_studio.tasks import generate_proposal_images_task
from core.celery import app as celery_app


def _make_fake_png_bytes(width: int = 1200, height: int = 1200) -> bytes:
    """Cria um PNG em memória para simular retorno do Playwright screenshot."""
    img = Image.new("RGB", (width, height), (11, 15, 25))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_fake_cutout_rgba_bytes() -> bytes:
    """Cria um PNG RGBA com objeto central opaco e bordas transparentes para simular rembg.remove."""
    img = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    for x in range(150, 450):
        for y in range(100, 500):
            img.putpixel((x, y), (30, 64, 175, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class MediaStudioPhase4ATests(TestCase):
    """Valida geração de capa 1200x1200, templates de cards HTML, orquestrador e fila queue_media."""

    def setUp(self) -> None:
        self.account = MarketplaceAccount.objects.create(
            name="ML Estúdio Teste",
            channel=ChannelChoices.MERCADO_LIVRE,
            seller_id="123987",
        )
        self.listing = Listing.objects.create(
            account=self.account,
            external_id="MLB9000000001",
            sku_readonly="WD-PROC-250",
            original_title="Mini Processador Eletrico Usb 250ml",
            sold_quantity=0,
            status=ListingStatus.REVIEW,
        )
        self.snapshot = ListingSnapshot.objects.create(
            listing=self.listing,
            raw_item_json={"id": "MLB9000000001", "title": "Mini Processador"},
            raw_description_json={"plain_text": "Mini processador 250ml inox."},
            category_attributes_json=[],
            is_baseline=True,
        )
        self.proposal = OptimizationProposal.objects.create(
            listing=self.listing,
            baseline_snapshot=self.snapshot,
            extracted_facts_json={
                "product_type": "Mini Processador de Alimentos",
                "brand": "Genérica",
                "model": None,
                "material": "Aço Inox",
                "voltage": None,
                "dimensions": None,
                "included_items": ["1 Mini Processador 250ml", "1 Cabo USB"],
                "verified_features": ["3 lâminas em aço inox"],
            },
            proposed_title="Mini Processador Alimentos Elétrico USB 250ml 3 Lâminas Inox",
            proposed_description="Descrição otimizada.",
            proposed_attributes_json=[
                {
                    "id": "BRAND",
                    "name": "Marca",
                    "value_name": "Genérica",
                    "source_evidence": "Fonte",
                },
                {
                    "id": "SELLER_SKU",
                    "name": "SKU",
                    "value_name": "WD-PROC-250",
                    "source_evidence": "Não deve vazar para o card",
                },
            ],
            card_highlights_json=[
                "3 Lâminas em Aço Inox",
                "Copo Compacto de 250ml",
                "Recarregável via Cabo USB",
            ],
            status=ProposalStatus.PENDING_REVIEW,
        )

    def test_build_white_bg_cover_produces_1200x1200_pure_white_corners_and_centered_product(
        self,
    ) -> None:
        """build_white_bg_cover deve gerar JPEG 1200x1200 com cantos #FFFFFF e produto centralizado."""
        cutout_img = Image.open(BytesIO(_make_fake_cutout_rgba_bytes())).convert("RGBA")
        jpeg_bytes = build_white_bg_cover(
            cutout_image=cutout_img,
            canvas_size=1200,
            padding_ratio=0.08,
        )

        with Image.open(BytesIO(jpeg_bytes)) as cover:
            self.assertEqual(cover.format, "JPEG")
            self.assertEqual(cover.size, (1200, 1200))
            self.assertEqual(cover.mode, "RGB")

            # Verifica que os 4 cantos são branco puro (255, 255, 255)
            for corner in ((0, 0), (1199, 0), (0, 1199), (1199, 1199)):
                self.assertEqual(cover.getpixel(corner), (255, 255, 255))

            # Verifica que o centro contém o produto recortado (não é branco)
            center_pixel = cover.getpixel((600, 600))
            self.assertNotEqual(center_pixel, (255, 255, 255))

    def test_card_templates_render_highlights_and_specs_without_none_or_seller_sku(
        self,
    ) -> None:
        """card_benefits.html e card_specs.html devem exibir dados reais, omitir None e bloquear SELLER_SKU."""
        cutout_img = Image.open(BytesIO(_make_fake_cutout_rgba_bytes())).convert("RGBA")
        cutout_png = save_cutout_png(cutout_img)

        benefits_html = build_benefits_card_html(self.proposal, cutout_png)
        self.assertIn("3 Lâminas em Aço Inox", benefits_html)
        self.assertIn("Copo Compacto de 250ml", benefits_html)
        self.assertIn("data:image/png;base64,", benefits_html)
        self.assertNotIn("WD-PROC-250", benefits_html)

        specs_html = build_specs_card_html(self.proposal, cutout_png)
        self.assertIn("Genérica", specs_html)
        self.assertIn("Aço Inox", specs_html)
        self.assertIn("1 Mini Processador 250ml", specs_html)
        # Campos None (voltage, dimensions, model) e SELLER_SKU jamais devem aparecer
        self.assertNotIn("None", specs_html)
        self.assertNotIn("Voltagem", specs_html)
        self.assertNotIn("Dimensões", specs_html)
        self.assertNotIn("SELLER_SKU", specs_html)
        self.assertNotIn("WD-PROC-250", specs_html)

    @patch("apps.media_studio.services.card_studio.sync_playwright")
    @patch("apps.media_studio.services.cover_studio.rembg.remove")
    def test_generate_images_for_proposal_creates_ready_assets_and_increments_media_quota(
        self,
        mock_rembg_remove: MagicMock,
        mock_sync_playwright: MagicMock,
    ) -> None:
        """generate_images_for_proposal deve criar os MediaAssets READY, atualizar proposal e DailyQuotaLog."""
        mock_rembg_remove.return_value = _make_fake_cutout_rgba_bytes()

        mock_page = MagicMock()
        mock_page.screenshot.return_value = _make_fake_png_bytes(1200, 1200)
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw_ctx = MagicMock()
        mock_pw_ctx.chromium.launch.return_value = mock_browser
        mock_sync_playwright.return_value.__enter__.return_value = mock_pw_ctx

        assets = generate_images_for_proposal(
            proposal=self.proposal,
            use_sample_image=True,
        )

        self.assertEqual(len(assets), 4)
        asset_types = {a.asset_type for a in assets}
        self.assertEqual(
            asset_types,
            {
                MediaAssetType.CUTOUT_PNG,
                MediaAssetType.WHITE_BG_COVER,
                MediaAssetType.INFO_CARD_BENEFITS,
                MediaAssetType.INFO_CARD_SPECS,
            },
        )
        for asset in assets:
            self.assertEqual(asset.status, MediaAssetStatus.READY)
            self.assertGreater(asset.file_size_bytes, 0)
            if asset.asset_type != MediaAssetType.CUTOUT_PNG:
                self.assertEqual((asset.width, asset.height), (1200, 1200))

        self.proposal.refresh_from_db()
        self.assertIn("assets", self.proposal.media_assets_json)
        self.assertEqual(len(self.proposal.media_assets_json["assets"]), 4)

        quota = DailyQuotaLog.get_today()
        self.assertEqual(quota.media_jobs_run, 1)

    def test_generate_proposal_images_task_is_routed_to_queue_media(self) -> None:
        """Garante que generate_proposal_images_task está isolada na fila queue_media."""
        self.assertEqual(generate_proposal_images_task.queue, "queue_media")
        routes = celery_app.conf.task_routes
        self.assertEqual(routes["apps.media_studio.*"]["queue"], "queue_media")
        self.assertEqual(compute_sample := len(build_synthetic_sample_image()), compute_sample)
        self.assertEqual(MediaAsset.objects.count(), 0)
