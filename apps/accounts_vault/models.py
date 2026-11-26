"""Model MarketplaceAccount com cofre Fernet de tokens OAuth e Circuit-Breaker."""
from datetime import timedelta

from django.db import models
from django.utils import timezone

from .crypto import decrypt_secret, encrypt_secret


class ChannelChoices(models.TextChoices):
    """Canais de marketplace suportados pelo motor."""

    MERCADO_LIVRE = "MERCADO_LIVRE", "Mercado Livre"
    SHOPEE = "SHOPEE", "Shopee"
    TIKTOK_SHOP = "TIKTOK_SHOP", "TikTok Shop"


class MarketplaceAccount(models.Model):
    """Conta de loja conectada a um marketplace com tokens criptografados e circuit-breaker."""

    CIRCUIT_BREAKER_THRESHOLD = 3

    name = models.CharField(
        "Apelido da Loja",
        max_length=120,
        help_text="Ex: ML Principal",
    )
    channel = models.CharField(
        "Canal",
        max_length=30,
        choices=ChannelChoices.choices,
        default=ChannelChoices.MERCADO_LIVRE,
    )
    seller_id = models.CharField(
        "Seller IDExterno",
        max_length=100,
        blank=True,
        help_text="Preenchido automaticamente com o user_id do marketplace após OAuth.",
    )
    encrypted_access_token = models.TextField(
        "Access Token (Criptografado)",
        blank=True,
    )
    encrypted_refresh_token = models.TextField(
        "Refresh Token (Criptografado)",
        blank=True,
    )
    token_expires_at = models.DateTimeField(
        "Expiração do Token",
        null=True,
        blank=True,
    )
    max_daily_patches = models.PositiveIntegerField(
        "Cota Diária de Patches",
        default=50,
    )
    consecutive_errors = models.PositiveIntegerField(
        "Erros Consecutivos de API",
        default=0,
    )
    is_paused_by_circuit_breaker = models.BooleanField(
        "Pausado por Circuit-Breaker",
        default=False,
    )
    is_active = models.BooleanField(
        "Ativa",
        default=True,
    )
    created_at = models.DateTimeField(
        "Criado em",
        auto_now_add=True,
    )
    updated_at = models.DateTimeField(
        "Atualizado em",
        auto_now=True,
    )

    class Meta:
        verbose_name = "Conta de Marketplace"
        verbose_name_plural = "Contas de Marketplace"
        ordering = ["name"]

    def __str__(self) -> str:
        seller_suffix = f" ({self.seller_id})" if self.seller_id else ""
        return f"{self.name} [{self.get_channel_display()}]{seller_suffix}"

    def set_tokens(
        self,
        access_token: str,
        refresh_token: str,
        expires_in_seconds: int,
    ) -> None:
        """Criptografa os tokens OAuth, calcula token_expires_at e persiste se a conta já existir."""
        self.encrypted_access_token = encrypt_secret(access_token)
        if refresh_token:
            self.encrypted_refresh_token = encrypt_secret(refresh_token)
        self.token_expires_at = timezone.now() + timedelta(
            seconds=int(expires_in_seconds)
        )
        if self.pk is not None:
            self.save(
                update_fields=[
                    "encrypted_access_token",
                    "encrypted_refresh_token",
                    "token_expires_at",
                    "updated_at",
                ]
            )

    def get_access_token(self) -> str:
        """Retorna o access_token descriptografado (ou string vazia se ausente)."""
        if not self.encrypted_access_token:
            return ""
        return decrypt_secret(self.encrypted_access_token)

    def get_refresh_token(self) -> str:
        """Retorna o refresh_token descriptografado (ou string vazia se ausente)."""
        if not self.encrypted_refresh_token:
            return ""
        return decrypt_secret(self.encrypted_refresh_token)

    def is_token_expiring_soon(self, margin_seconds: int = 600) -> bool:
        """Indica se o token já expirou ou expira dentro da margem de segurança (padrão: 10 min)."""
        if not self.encrypted_access_token or not self.token_expires_at:
            return True
        threshold = timezone.now() + timedelta(seconds=int(margin_seconds))
        return threshold >= self.token_expires_at

    def record_api_error(self) -> None:
        """Incrementa erros consecutivos e aciona o circuit-breaker ao atingir 3 falhas."""
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.CIRCUIT_BREAKER_THRESHOLD:
            self.is_paused_by_circuit_breaker = True
        if self.pk is not None:
            self.save(
                update_fields=[
                    "consecutive_errors",
                    "is_paused_by_circuit_breaker",
                    "updated_at",
                ]
            )

    def reset_circuit_breaker(self) -> None:
        """Zera a contagem de erros consecutivos e reativa a conta do circuit-breaker."""
        self.consecutive_errors = 0
        self.is_paused_by_circuit_breaker = False
        if self.pk is not None:
            self.save(
                update_fields=[
                    "consecutive_errors",
                    "is_paused_by_circuit_breaker",
                    "updated_at",
                ]
            )
