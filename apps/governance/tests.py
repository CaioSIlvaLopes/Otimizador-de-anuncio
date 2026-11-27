"""Testes unitários do app governance (PromptConfig fallback, seed e DailyQuotaLog)."""
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import (
    AgentNameChoices,
    DailyQuotaLog,
    PromptChannelChoices,
    PromptConfig,
)


class GovernanceTests(TestCase):
    """Valida fallback de PromptConfig por canal e controle atômico de cota diária LLM."""

    def test_prompt_config_get_active_channel_fallback_to_all(self) -> None:
        """Deve buscar primeiro o prompt específico do canal e fazer fallback para ALL."""
        all_prompt = PromptConfig.objects.create(
            agent_name=AgentNameChoices.SEO_COPY,
            channel=PromptChannelChoices.ALL,
            version=1,
            system_prompt="Prompt genérico ALL",
            is_active=True,
        )

        # Sem prompt específico de SHOPEE, faz fallback para ALL
        resolved_fallback = PromptConfig.get_active(
            AgentNameChoices.SEO_COPY,
            PromptChannelChoices.SHOPEE,
        )
        self.assertEqual(resolved_fallback, all_prompt)

        # Criando prompt específico para MERCADO_LIVRE, deve priorizar o específico
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
