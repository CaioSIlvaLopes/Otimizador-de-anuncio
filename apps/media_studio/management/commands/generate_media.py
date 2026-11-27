"""Comando CLI para testar a geração local de capa fundo branco 1200x1200 e cards HTML via Playwright."""
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.catalog.models import OptimizationProposal
from apps.media_studio.services.pipeline import generate_images_for_proposal


class Command(BaseCommand):
    """Gera capa 1200x1200 (rembg + Pillow) e 2 cards informativos (Playwright) para uma proposta."""

    help = "Executa o pipeline local de imagens (rembg + Pillow + Playwright) para uma OptimizationProposal."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--proposal-id",
            type=int,
            required=True,
            help="ID da OptimizationProposal para gerar os ativos de mídia.",
        )
        parser.add_argument(
            "--source-image",
            type=str,
            default=None,
            help="Caminho local opcional de uma foto do produto no computador.",
        )
        parser.add_argument(
            "--use-sample-image",
            action="store_true",
            default=False,
            help="Gera uma imagem sintética via Pillow caso o anúncio seja mockado sem fotos reais.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        proposal_id: int = options["proposal_id"]
        source_image: str | None = options.get("source_image")
        use_sample_image: bool = bool(options.get("use_sample_image"))

        try:
            proposal = OptimizationProposal.objects.select_related(
                "listing",
                "baseline_snapshot",
            ).get(pk=proposal_id)
        except OptimizationProposal.DoesNotExist as exc:
            raise CommandError(
                f"OptimizationProposal com ID={proposal_id} não encontrada."
            ) from exc

        self.stdout.write(
            self.style.NOTICE(
                f"Iniciando Estúdio de Mídia para Proposta #{proposal.pk} "
                f"({proposal.listing.external_id} - {proposal.proposed_title})..."
            )
        )

        try:
            assets = generate_images_for_proposal(
                proposal=proposal,
                source_image_path=source_image,
                use_sample_image=use_sample_image,
            )
        except Exception as exc:
            raise CommandError(f"Falha ao gerar mídia: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSucesso! {len(assets)} ativos de mídia gerados para a Proposta #{proposal.pk}:"
            )
        )
        for asset in assets:
            file_path = asset.file.path if asset.file else "N/A"
            file_url = asset.file.url if asset.file else "N/A"
            self.stdout.write(
                f" - [{asset.asset_type}] {asset.width}x{asset.height}px "
                f"({asset.file_size_bytes} bytes)\n"
                f"   Arquivo: {file_path}\n"
                f"   URL Local: http://127.0.0.1:8000{file_url}"
            )
