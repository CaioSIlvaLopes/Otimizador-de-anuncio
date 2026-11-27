"""Orquestrador do pipeline local de imagens (rembg + Pillow + Playwright Cards)."""
from io import BytesIO
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from PIL import Image

from apps.agents.client import download_listing_images
from apps.catalog.models import OptimizationProposal
from apps.governance.models import DailyQuotaLog
from apps.media_studio.models import (
    MediaAsset,
    MediaAssetStatus,
    MediaAssetType,
)
from apps.media_studio.services.card_studio import (
    build_benefits_card_html,
    build_specs_card_html,
    render_html_to_image,
)
from apps.media_studio.services.cover_studio import (
    build_synthetic_sample_image,
    build_white_bg_cover,
    extract_product_cutout,
    save_cutout_png,
)


def _resolve_source_image_bytes(
    proposal: OptimizationProposal,
    source_image_path: str | None = None,
    use_sample_image: bool = False,
) -> bytes:
    """Obtém os bytes da imagem fonte: arquivo ou pasta local -> snapshot do anúncio -> imagem sintética."""
    if source_image_path:
        path_obj = Path(source_image_path)
        if path_obj.exists() and path_obj.is_dir():
            valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
            candidates = sorted(
                f
                for f in path_obj.iterdir()
                if f.is_file() and f.suffix.lower() in valid_exts
            )
            if not candidates:
                raise FileNotFoundError(
                    f"Nenhuma imagem (.jpg, .png, .webp) encontrada dentro da pasta: {source_image_path}"
                )
            return candidates[0].read_bytes()
        if not path_obj.exists() or not path_obj.is_file():
            raise FileNotFoundError(
                f"Arquivo de imagem fonte não encontrado: {source_image_path}"
            )
        return path_obj.read_bytes()

    raw_item = (
        proposal.baseline_snapshot.raw_item_json
        if proposal.baseline_snapshot_id
        else {}
    )
    downloaded = download_listing_images(raw_item or {}, max_images=1)
    if downloaded:
        return downloaded[0][0]

    if use_sample_image:
        return build_synthetic_sample_image()

    raise ValueError(
        f"Nenhuma imagem encontrada para o anúncio {proposal.listing.external_id}. "
        "Informe --source-image=<caminho> ou --use-sample-image."
    )


def _persist_media_asset(
    *,
    proposal: OptimizationProposal,
    asset_type: str,
    file_name: str,
    file_bytes: bytes,
    metadata: dict[str, Any] | None = None,
) -> MediaAsset:
    """Salva ou substitui o MediaAsset do tipo informado para a proposta."""
    with Image.open(BytesIO(file_bytes)) as img:
        width, height = img.size

    asset, _ = MediaAsset.objects.update_or_create(
        proposal=proposal,
        asset_type=asset_type,
        defaults={
            "status": MediaAssetStatus.READY,
            "width": width,
            "height": height,
            "file_size_bytes": len(file_bytes),
            "metadata_json": metadata or {},
            "error_message": "",
        },
    )
    asset.file.save(file_name, ContentFile(file_bytes), save=True)
    return asset


def generate_images_for_proposal(
    proposal: OptimizationProposal,
    source_image_path: str | None = None,
    use_sample_image: bool = False,
) -> list[MediaAsset]:
    """Gera CUTOUT_PNG, WHITE_BG_COVER (1200x1200), INFO_CARD_BENEFITS e INFO_CARD_SPECS."""
    source_bytes = _resolve_source_image_bytes(
        proposal=proposal,
        source_image_path=source_image_path,
        use_sample_image=use_sample_image,
    )

    ext_id = proposal.listing.external_id.lower()

    # 1. Remoção de fundo única (rembg) + PNG transparente reutilizável
    cutout_image = extract_product_cutout(source_bytes)
    cutout_png_bytes = save_cutout_png(cutout_image)

    cutout_asset = _persist_media_asset(
        proposal=proposal,
        asset_type=MediaAssetType.CUTOUT_PNG,
        file_name=f"{ext_id}_cutout_p{proposal.pk}.png",
        file_bytes=cutout_png_bytes,
        metadata={"engine": "rembg+pillow"},
    )

    # 2. Capa Fundo Branco Puro #FFFFFF 1200x1200 (padrão Mercado Livre)
    cover_jpeg_bytes = build_white_bg_cover(
        cutout_image=cutout_image,
        canvas_size=1200,
        padding_ratio=0.08,
    )
    cover_asset = _persist_media_asset(
        proposal=proposal,
        asset_type=MediaAssetType.WHITE_BG_COVER,
        file_name=f"{ext_id}_cover_1200x1200_p{proposal.pk}.jpg",
        file_bytes=cover_jpeg_bytes,
        metadata={"engine": "pillow", "canvas": "1200x1200", "background": "#FFFFFF"},
    )

    # 3. Card Informativo 1 — Benefícios (HTML/CSS -> Playwright 1200x1200)
    benefits_html = build_benefits_card_html(
        proposal=proposal,
        cutout_png_bytes=cutout_png_bytes,
    )
    benefits_png_bytes = render_html_to_image(
        html_content=benefits_html,
        width=1200,
        height=1200,
    )
    benefits_asset = _persist_media_asset(
        proposal=proposal,
        asset_type=MediaAssetType.INFO_CARD_BENEFITS,
        file_name=f"{ext_id}_card_benefits_1200x1200_p{proposal.pk}.png",
        file_bytes=benefits_png_bytes,
        metadata={"engine": "playwright_chromium", "template": "card_benefits.html"},
    )

    # 4. Card Informativo 2 — Ficha Técnica & Embalagem (HTML/CSS -> Playwright 1200x1200)
    specs_html = build_specs_card_html(
        proposal=proposal,
        cutout_png_bytes=cutout_png_bytes,
    )
    specs_png_bytes = render_html_to_image(
        html_content=specs_html,
        width=1200,
        height=1200,
    )
    specs_asset = _persist_media_asset(
        proposal=proposal,
        asset_type=MediaAssetType.INFO_CARD_SPECS,
        file_name=f"{ext_id}_card_specs_1200x1200_p{proposal.pk}.png",
        file_bytes=specs_png_bytes,
        metadata={"engine": "playwright_chromium", "template": "card_specs.html"},
    )

    assets = [cutout_asset, cover_asset, benefits_asset, specs_asset]

    proposal.media_assets_json = {
        "assets": [
            {
                "id": a.pk,
                "asset_type": a.asset_type,
                "label": a.get_asset_type_display(),
                "status": a.status,
                "url": a.file.url if a.file else "",
                "path": a.file.name if a.file else "",
                "width": a.width,
                "height": a.height,
                "file_size_bytes": a.file_size_bytes,
            }
            for a in assets
        ]
    }
    proposal.save(update_fields=["media_assets_json", "updated_at"])

    DailyQuotaLog.get_today().record_media_job(count=1)

    return assets
