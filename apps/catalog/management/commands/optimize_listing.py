"""Comando CLI para executar o pipeline dos 4 sub-agentes em um anúncio de forma síncrona."""
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.agents.pipeline import OptimizationPipelineService
from apps.catalog.models import Listing, ListingStatus


class Command(BaseCommand):
    """Executa o pipeline de 4 sub-agentes (Gemini 2.5 Flash) para um Listing e imprime o resultado."""

    help = "Executa os 4 sub-agentes de otimização para um anúncio (--listing-id ou --external-id)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--listing-id",
            type=int,
            default=None,
            help="ID interno do Listing no banco.",
        )
        parser.add_argument(
            "--external-id",
            type=str,
            default=None,
            help="ID externo do anúncio no marketplace (ex: MLB9000000001).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        listing_id: int | None = options.get("listing_id")
        external_id: str | None = options.get("external_id")

        if not listing_id and not external_id:
            raise CommandError("Informe --listing-id=<id> ou --external-id=<MLB...>.")

        try:
            if listing_id:
                listing = Listing.objects.get(pk=listing_id)
            else:
                listing = Listing.objects.get(external_id=external_id)
        except Listing.DoesNotExist as exc:
            raise CommandError("Anúncio (Listing) não encontrado no banco.") from exc

        self.stdout.write(
            self.style.NOTICE(
                f"Iniciando Pipeline de 4 Sub-Agentes para {listing.external_id} "
                f"(ID={listing.pk}, SKU={listing.sku_readonly}, Vendas={listing.sold_quantity})...\n"
            )
        )

        listing.status = ListingStatus.PROCESSING
        listing.save(update_fields=["status", "updated_at"])

        try:
            proposal = OptimizationPipelineService.run_for_listing(listing=listing)
        except Exception as exc:
            listing.status = ListingStatus.FAILED
            listing.save(update_fields=["status", "updated_at"])
            raise CommandError(f"Falha durante otimização: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("=" * 78))
        self.stdout.write(
            self.style.SUCCESS(
                f"PROPOSTA #{proposal.pk} GERADA COM SUCESSO | STATUS DO LISTING: {listing.status}"
            )
        )
        self.stdout.write(self.style.SUCCESS("=" * 78))
        self.stdout.write(f"Título Original ({len(listing.original_title)} chars):")
        self.stdout.write(f"  {listing.original_title}")
        self.stdout.write(f"Título Proposto ({len(proposal.proposed_title)} chars):")
        self.stdout.write(f"  {proposal.proposed_title}\n")

        self.stdout.write("Atributos Preenchidos:")
        for attr in proposal.proposed_attributes_json:
            self.stdout.write(
                f"  - [{attr.get('id')}] {attr.get('name')}: {attr.get('value_name')} "
                f"(Evidência: {attr.get('source_evidence')})"
            )

        self.stdout.write("\nDestaques para Cards (Fase 4):")
        for hl in proposal.card_highlights_json:
            self.stdout.write(f"  * {hl}")

        self.stdout.write("\nDescrição Proposta (Texto Puro):")
        self.stdout.write("-" * 78)
        self.stdout.write(proposal.proposed_description)
        self.stdout.write("-" * 78)

        self.stdout.write(
            f"\nNota de Confiança do Auditor: {proposal.confidence_score}/100"
        )
        self.stdout.write(f"Tokens LLM Consumidos: {proposal.llm_tokens_used}")

        if proposal.auditor_warnings:
            self.stdout.write(self.style.WARNING("\nAlertas do Auditor:"))
            for warn in proposal.auditor_warnings:
                self.stdout.write(self.style.WARNING(f"  ! {warn}"))
        else:
            self.stdout.write(self.style.SUCCESS("\nAlertas do Auditor: Nenhum alerta."))
