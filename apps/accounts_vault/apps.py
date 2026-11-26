"""Configuração do app accounts_vault."""
from django.apps import AppConfig


class AccountsVaultConfig(AppConfig):
    """Configuração do módulo de cofre de contas e credenciais."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts_vault"
    verbose_name = "Cofre de Contas (Accounts Vault)"
