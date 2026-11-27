"""Testes unitários do app agents (4 sub-agentes, bloqueio de cota, filtro SELLER_SKU e travas do Auditor)."""
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
from apps.catalog.tasks import optimize_listing_task
from apps.governance.models import DailyQuotaLog

from .client import DailyQuotaExceededError, GeminiStructuredClient
from .pipeline import (
    filter_editable_category_attributes,
    run_attributes_agent,
    run_auditor,
    run_extractor,
)
from .schemas import (
    AttributesOutput,
    AuditorOutput,
    ExtractedFacts,
    FilledAttribute,
    SeoCopyOutput,
    VideoScriptDraft,
)


class AgentsPipelineTests(TestCase):
    """Valida todas as garantias de negócio da Fase 2 com mocks do GeminiStructuredClient."""

    def setUp(self) -> None:
        call_command("seed_default_prompts")
        self.account = MarketplaceAccount.objects.create(
            name="ML Teste Agentes",
            channel=ChannelChoices.MERCADO_LIVRE,
            seller_id="12345678",
        )
        self.listing = Listing.objects.create(
            account=self.account,
            external_id="MLB9000001",
            category_id="MLB12345",
            sku_readonly="WD-PROC-250",
            original_title="Mini Processador Eletrico Usb 250ml",
            marketplace_status="active",
            sold_quantity=0,
            status=ListingStatus.DETECTED,
        )
        self.snapshot = ListingSnapshot.objects.create(
            listing=self.listing,
            raw_item_json={
                "id": "MLB9000001",
                "title": "Mini Processador Eletrico Usb 250ml",
                "seller_custom_field": "WD-PROC-250",
                "condition": "new",
                "attributes": [
                    {"id": "BRAND", "name": "Marca", "value_name": "Genérica"},
                    {"id": "SELLER_SKU", "name": "SKU", "value_name": "WD-PROC-250"},
                ],
            },
            raw_description_json={
                "plain_text": "Mini processador 250ml recarregável USB com 3 lâminas inox."
            },
            category_attributes_json=[
                {"id": "BRAND", "name": "Marca", "tags": {"required": True}},
                {"id": "MATERIAL", "name": "Material", "tags": {}},
                {"id": "SELLER_SKU", "name": "SKU", "tags": {"hidden": True}},
                {"id": "INTERNAL_RO", "name": "Read Only Attr", "tags": {"read_only": True}},
            ],
            is_baseline=True,
        )

    @override_settings(DAILY_LLM_TOKEN_LIMIT=500, GEMINI_API_KEY="fake-test-key")
    def test_daily_quota_exceeded_blocks_gemini_call(self) -> None:
        """Deve lançar DailyQuotaExceededError imediatamente quando o orçamento diário esgotar."""
        quota = DailyQuotaLog.get_today()
        quota.record_llm_usage(input_tokens=300, output_tokens=200)
        self.assertFalse(quota.has_llm_budget())

        client = GeminiStructuredClient(api_key="fake-test-key")
        with self.assertRaises(DailyQuotaExceededError):
            client.generate_structured(
                system_prompt="System",
                user_prompt="User",
                response_schema=ExtractedFacts,
            )

    def test_extracted_facts_preserves_null_fields_for_unverified_specs(self) -> None:
        """Garante que campos ausentes na fonte permanecem None (null) em ExtractedFacts."""
        mock_client = MagicMock(spec=GeminiStructuredClient)
        mock_facts = ExtractedFacts(
            product_type="Mini Processador de Alimentos",
            brand="Genérica",
            model=None,
            material="Aço Inox",
            color=None,
            voltage=None,
            dimensions=None,
            weight=None,
            included_items=["1 Mini Processador 250ml", "1 Cabo USB"],
            verified_features=["Capacidade de 250ml", "3 lâminas em aço inox"],
            missing_critical_info=["voltage", "dimensions", "weight"],
        )
        mock_client.generate_structured.return_value = (
            mock_facts,
            {"input_tokens": 100, "output_tokens": 80, "total_tokens": 180},
        )

        facts, _ = run_extractor(
            raw_item_json=self.snapshot.raw_item_json,
            raw_description_json=self.snapshot.raw_description_json,
            client=mock_client,
        )
        self.assertIsNone(facts.voltage)
        self.assertIsNone(facts.dimensions)
        self.assertIsNone(facts.weight)
        self.assertIsNone(facts.model)
        self.assertEqual(facts.material, "Aço Inox")

    def test_attributes_agent_strictly_filters_seller_sku_and_readonly_on_input_and_output(
        self,
    ) -> None:
        """Impede SELLER_SKU e atributos read-only tanto no prompt de entrada quanto na saída do LLM."""
        filtered_input = filter_editable_category_attributes(
            self.snapshot.category_attributes_json
        )
        filtered_ids = {a["id"] for a in filtered_input}
        self.assertEqual(filtered_ids, {"BRAND", "MATERIAL"})
        self.assertNotIn("SELLER_SKU", filtered_ids)
        self.assertNotIn("INTERNAL_RO", filtered_ids)

        # Mesmo se o LLM alucinar e devolver SELLER_SKU ou INTERNAL_RO na saída, o pós-filtro descarta
        mock_client = MagicMock(spec=GeminiStructuredClient)
        mock_client.generate_structured.return_value = (
            AttributesOutput(
                attributes=[
                    FilledAttribute(
                        id="BRAND",
                        name="Marca",
                        value_name="Genérica",
                        source_evidence="Atributo original",
                    ),
                    FilledAttribute(
                        id="SELLER_SKU",
                        name="SKU",
                        value_name="HACKED-SKU",
                        source_evidence="Injetado",
                    ),
                    FilledAttribute(
                        id="INTERNAL_RO",
                        name="Read Only",
                        value_name="Valor proibido",
                        source_evidence="Injetado",
                    ),
                ],
                skipped_required_ids=["SELLER_SKU", "MODEL"],
            ),
            {"input_tokens": 120, "output_tokens": 60, "total_tokens": 180},
        )

        facts = ExtractedFacts(product_type="Mini Processador", brand="Genérica")
        output, _ = run_attributes_agent(
            extracted_facts=facts,
            category_attributes_json=self.snapshot.category_attributes_json,
            client=mock_client,
        )

        # Verifica que o prompt enviado ao modelo não continha SELLER_SKU no schema
        called_kwargs = mock_client.generate_structured.call_args.kwargs
        self.assertNotIn('"id": "SELLER_SKU"', called_kwargs["user_prompt"])
        self.assertNotIn('"id": "INTERNAL_RO"', called_kwargs["user_prompt"])

        # Verifica que a saída foi limpa pelo pós-filtro determinístico
        returned_ids = [a.id for a in output.attributes]
        self.assertEqual(returned_ids, ["BRAND"])
        self.assertNotIn("SELLER_SKU", output.skipped_required_ids)

    def test_auditor_deterministic_guardrail_clamps_score_on_forbidden_term_or_long_title(
        self,
    ) -> None:
        """Trava determinística deve limitar confidence_score <= 60 se houver termo proibido ou título > 60 chars."""
        mock_client = MagicMock(spec=GeminiStructuredClient)
        # Mock do LLM retornando nota 100 e sem riscos
        mock_client.generate_structured.side_effect = lambda **_: (
            AuditorOutput(
                confidence_score=100,
                has_hallucination_risk=False,
                forbidden_terms_found=[],
                warnings=[],
                summary="Tudo perfeito segundo o LLM.",
            ),
            {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )

        facts = ExtractedFacts(product_type="Mini Processador")
        attrs = AttributesOutput(attributes=[])

        # Caso 1: Texto contém termo proibido ("Frete Grátis" e "Envio Imediato")
        seo_with_forbidden = SeoCopyOutput(
            proposed_title="Mini Processador USB 250ml Inox Triturador Alho",
            proposed_description="Aproveite promoção com FRETE GRÁTIS e envio imediato para todo Brasil!",
            card_highlights=["3 lâminas inox"],
            video_script=VideoScriptDraft(hook="Hook", scenes=["Cena 1"], cta="Compre"),
        )
        audit_1, _ = run_auditor(
            extracted_facts=facts,
            filled_attributes=attrs,
            seo_copy=seo_with_forbidden,
            channel="MERCADO_LIVRE",
            client=mock_client,
        )
        self.assertLessEqual(audit_1.confidence_score, 60)
        self.assertTrue(any("frete grátis" in t.lower() for t in audit_1.forbidden_terms_found))
        self.assertTrue(any("Trava Determinística" in w for w in audit_1.warnings))

        # Caso 2: Título com mais de 60 caracteres no Mercado Livre (sem termos proibidos)
        seo_long_title = SeoCopyOutput(
            proposed_title="Mini Processador Triturador Elétrico De Alimentos USB 250ml Lâmina Inox Portátil",
            proposed_description="Descrição limpa e técnica sem termos proibidos.",
            card_highlights=["Compacto 250ml"],
            video_script=VideoScriptDraft(hook="Hook", scenes=["Cena 1"], cta="Confira"),
        )
        self.assertGreater(len(seo_long_title.proposed_title), 60)

        audit_2, _ = run_auditor(
            extracted_facts=facts,
            filled_attributes=attrs,
            seo_copy=seo_long_title,
            channel="MERCADO_LIVRE",
            client=mock_client,
        )
        self.assertLessEqual(audit_2.confidence_score, 60)
        self.assertTrue(any("60" in w for w in audit_2.warnings))

    @patch("apps.agents.pipeline.download_listing_images", return_value=[])
    @patch("apps.agents.pipeline.GeminiStructuredClient")
    def test_full_pipeline_and_celery_task_status_transition(
        self,
        mock_client_cls: MagicMock,
        _mock_dl: MagicMock,
    ) -> None:
        """Valida a transição DETECTED -> PROCESSING -> REVIEW e criação de OptimizationProposal."""
        mock_client = MagicMock()
        mock_client.generate_structured.side_effect = [
            (
                ExtractedFacts(
                    product_type="Mini Processador Elétrico",
                    brand="Genérica",
                    material="Aço Inox",
                    verified_features=["Capacidade 250ml", "Recarregável USB"],
                    included_items=["1 Mini Processador 250ml", "1 Cabo USB"],
                ),
                {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            ),
            (
                AttributesOutput(
                    attributes=[
                        FilledAttribute(
                            id="BRAND",
                            name="Marca",
                            value_name="Genérica",
                            source_evidence="Atributo original",
                        ),
                        FilledAttribute(
                            id="MATERIAL",
                            name="Material",
                            value_name="Aço Inox",
                            source_evidence="Descrição original",
                        ),
                    ],
                    skipped_required_ids=[],
                ),
                {"input_tokens": 120, "output_tokens": 60, "total_tokens": 180},
            ),
            (
                SeoCopyOutput(
                    proposed_title="Mini Processador Alimentos Elétrico USB 250ml 3 Lâminas Inox",
                    proposed_description="Prepare temperos em segundos com o Mini Processador USB 250ml.",
                    card_highlights=[
                        "3 Lâminas em Aço Inox",
                        "Copo Compacto de 250ml",
                        "Recarregável via Cabo USB",
                    ],
                    video_script=VideoScriptDraft(
                        hook="Chega de picar alho na mão!",
                        scenes=["Mostrando as 3 lâminas inox", "Triturando em 10 segundos"],
                        cta="Garanta praticidade na sua cozinha.",
                    ),
                ),
                {"input_tokens": 200, "output_tokens": 150, "total_tokens": 350},
            ),
            (
                AuditorOutput(
                    confidence_score=94,
                    has_hallucination_risk=False,
                    forbidden_terms_found=[],
                    warnings=[],
                    summary="Proposta fiel aos fatos extraídos e dentro de 60 caracteres.",
                ),
                {"input_tokens": 180, "output_tokens": 40, "total_tokens": 220},
            ),
        ]
        mock_client_cls.return_value = mock_client

        result = optimize_listing_task(self.listing.pk)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["confidence_score"], 94)
        self.assertEqual(result["llm_tokens_used"], 900)

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.REVIEW)
        self.assertEqual(self.listing.confidence_score, 94)

        proposal = OptimizationProposal.objects.get(pk=result["proposal_id"])
        self.assertEqual(proposal.status, ProposalStatus.PENDING_REVIEW)
        self.assertEqual(proposal.baseline_snapshot, self.snapshot)
        self.assertLessEqual(len(proposal.proposed_title), 60)
        self.assertEqual(len(proposal.proposed_attributes_json), 2)
        self.assertEqual(len(proposal.card_highlights_json), 3)

    def test_seed_mock_wedrop_listings_command(self) -> None:
        """Valida criação idempotente dos 2 anúncios mockados WeDrop."""
        call_command("seed_mock_wedrop_listings")
        l1 = Listing.objects.get(external_id="MLB9000000001")
        l2 = Listing.objects.get(external_id="MLB9000000002")
        self.assertEqual(l1.sold_quantity, 0)
        self.assertEqual(l1.sku_readonly, "WD-PROC-250")
        self.assertEqual(l2.sold_quantity, 3)
        self.assertEqual(l2.sku_readonly, "WD-TWS-002")
        self.assertTrue(l1.snapshots.filter(is_baseline=True).exists())
        self.assertTrue(l2.snapshots.filter(is_baseline=True).exists())
