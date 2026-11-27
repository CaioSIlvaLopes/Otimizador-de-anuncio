"""Testes unitários do app governance (PromptConfig, DailyQuotaLog e Painel Web /dashboard/)."""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts_vault.models import ChannelChoices, MarketplaceAccount
from apps.catalog.models import (
    Listing,
    ListingSnapshot,
    ListingStatus,
    OptimizationProposal,
    ProposalStatus,
)

from .models import (
    AgentNameChoices,
    DailyQuotaLog,
    PromptChannelChoices,
    PromptConfig,
)


class GovernanceTests(TestCase):
    """Valida fallback de PromptConfig, controle atômico de cota diária e views do Dashboard."""

    def test_prompt_config_get_active_channel_fallback_to_all(self) -> None:
        """Deve buscar primeiro o prompt específico do canal e fazer fallback para ALL."""
        all_prompt = PromptConfig.objects.create(
            agent_name=AgentNameChoices.SEO_COPY,
            channel=PromptChannelChoices.ALL,
            version=1,
            system_prompt="Prompt genérico ALL",
            is_active=True,
        )

        resolved_fallback = PromptConfig.get_active(
            AgentNameChoices.SEO_COPY,
            PromptChannelChoices.SHOPEE,
        )
        self.assertEqual(resolved_fallback, all_prompt)

        ml_prompt = PromptConfig.objects.create(
            agent_name=AgentNameChoices.SEO_COPY,
            channel=PromptChannelChoices.MERCADO_LIVRE,
            version=1,
            system_prompt="Prompt específico Mercado Livre",
            is_active=True,
        )
        resolved_ml = PromptConfig.get_active(
            AgentNameChoices.SEO_COPY,
            PromptChannelChoices.MERCADO_LIVRE,
        )
        self.assertEqual(resolved_ml, ml_prompt)

    def test_seed_default_prompts_command_is_idempotent(self) -> None:
        """Rodar seed_default_prompts duas vezes deve manter exatamente os 4 prompts padrão."""
        call_command("seed_default_prompts")
        self.assertEqual(PromptConfig.objects.count(), 4)

        call_command("seed_default_prompts")
        self.assertEqual(PromptConfig.objects.count(), 4)

        seo_ml = PromptConfig.get_active(
            AgentNameChoices.SEO_COPY,
            PromptChannelChoices.MERCADO_LIVRE,
        )
        self.assertIsNotNone(seo_ml)
        self.assertIn("frete grátis", seo_ml.forbidden_terms)

    @override_settings(DAILY_LLM_TOKEN_LIMIT=1000)
    def test_daily_quota_log_atomic_recording_and_budget_check(self) -> None:
        """Valida incremento atômico de tokens e bloqueio ao atingir DAILY_LLM_TOKEN_LIMIT."""
        quota = DailyQuotaLog.get_today()
        self.assertTrue(quota.has_llm_budget())
        self.assertEqual(quota.total_llm_tokens, 0)

        quota.record_llm_usage(input_tokens=400, output_tokens=300)
        self.assertEqual(quota.total_llm_tokens, 700)
        self.assertEqual(quota.llm_calls_count, 1)
        self.assertTrue(quota.has_llm_budget())

        quota.record_llm_usage(input_tokens=200, output_tokens=100)
        self.assertEqual(quota.total_llm_tokens, 1000)
        self.assertEqual(quota.llm_calls_count, 2)
        self.assertFalse(quota.has_llm_budget())

    def test_dashboard_views_require_staff_and_handle_review_and_dry_run(self) -> None:
        """Views /dashboard/ e /dashboard/proposals/<id>/ devem exigir staff, salvar edição e rodar Dry-Run."""
        account = MarketplaceAccount.objects.create(
            name="ML Dashboard Store",
            channel=ChannelChoices.MERCADO_LIVRE,
            seller_id="554433",
        )
        listing = Listing.objects.create(
            account=account,
            external_id="MLB9000001",
            sku_readonly="WD-PROC-250",
            original_title="Mini Processador Eletrico Usb 250ml",
            sold_quantity=0,
            status=ListingStatus.REVIEW,
        )
        snapshot = ListingSnapshot.objects.create(
            listing=listing,
            raw_item_json={
                "id": "MLB9000001",
                "title": "Mini Processador Eletrico Usb 250ml",
                "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "Genérica"}],
            },
            raw_description_json={"plain_text": "Descrição original curta."},
            category_attributes_json=[],
            is_baseline=True,
        )
        proposal = OptimizationProposal.objects.create(
            listing=listing,
            baseline_snapshot=snapshot,
            proposed_title="Mini Processador Alimentos Elétrico USB 250ml Inox",
            proposed_description="Descrição proposta completa.",
            proposed_attributes_json=[
                {
                    "id": "BRAND",
                    "name": "Marca",
                    "value_name": "Genérica",
                    "source_evidence": "Fonte",
                }
            ],
            confidence_score=92,
            status=ProposalStatus.PENDING_REVIEW,
        )

        queue_url = reverse("dashboard:queue")
        detail_url = reverse("dashboard:proposal_detail", args=[proposal.pk])

        # 1. Usuário anônimo é redirecionado para login do admin
        anon_res = self.client.get(queue_url)
        self.assertEqual(anon_res.status_code, 302)

        # 2. Usuário staff acessa fila e tela lado a lado
        User = get_user_model()
        staff_user = User.objects.create_superuser(
            username="revisor_staff",
            email="staff@example.com",
            password="password123",
        )
        self.client.force_login(staff_user)

        queue_res = self.client.get(queue_url)
        self.assertEqual(queue_res.status_code, 200)
        self.assertContains(queue_res, "MLB9000001")
        self.assertContains(queue_res, "WD-PROC-250")

        detail_res = self.client.get(detail_url)
        self.assertEqual(detail_res.status_code, 200)
        self.assertContains(detail_res, "Mini Processador Eletrico Usb 250ml")
        self.assertContains(detail_res, "Mini Processador Alimentos Elétrico USB 250ml Inox")

        # 3. POST Dry-Run
        dry_res = self.client.post(
            detail_url,
            {
                "action": "dry_run",
                "proposed_title": "Mini Processador Triturador USB 250ml 3 Lâminas Inox",
                "proposed_description": "Descrição editada para Dry-Run.",
                "attr_id": ["BRAND"],
                "attr_name": ["Marca"],
                "attr_value": ["Genérica"],
                "attr_evidence": ["Fonte"],
            },
        )
        self.assertEqual(dry_res.status_code, 200)
        self.assertContains(dry_res, "Simulação Dry-Run")
        self.assertContains(dry_res, "Mini Processador Triturador USB 250ml 3 Lâminas Inox")

        # 4. POST Salvar e Aprovar
        approve_res = self.client.post(
            detail_url,
            {
                "action": "save_approve",
                "proposed_title": "Mini Processador Triturador USB 250ml 3 Lâminas Inox",
                "proposed_description": "Descrição aprovada final.",
                "attr_id": ["BRAND"],
                "attr_name": ["Marca"],
                "attr_value": ["Genérica"],
                "attr_evidence": ["Fonte"],
            },
        )
        self.assertEqual(approve_res.status_code, 302)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ProposalStatus.APPROVED)
        self.assertEqual(
            proposal.proposed_title,
            "Mini Processador Triturador USB 250ml 3 Lâminas Inox",
        )
