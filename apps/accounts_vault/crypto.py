"""Serviço de criptografia simétrica (Fernet) para segredos e tokens em repouso."""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _get_fernet() -> Fernet:
    """Instancia o objeto Fernet validando a presença e formato de settings.FERNET_KEY."""
    raw_key = getattr(settings, "FERNET_KEY", "")
    if not raw_key or not str(raw_key).strip():
        raise ImproperlyConfigured(
            "A variável FERNET_KEY não está configurada. Defina uma chave Fernet válida de "
            "32 bytes (base64 url-safe) no arquivo .env."
        )
    key_str = str(raw_key).strip()
    try:
        return Fernet(key_str.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "A variável FERNET_KEY configurada é inválida. Gere uma chave válida com: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc


def encrypt_secret(value: str) -> str:
    """Criptografa uma string em texto plano usando Fernet e retorna o token base64."""
    fernet = _get_fernet()
    if value is None:
        raise ValueError("O valor a ser criptografado não pode ser None.")
    token_bytes = fernet.encrypt(str(value).encode("utf-8"))
    return token_bytes.decode("utf-8")


def decrypt_secret(token: str) -> str:
    """Descriptografa um token Fernet e retorna a string original."""
    fernet = _get_fernet()
    if not token:
        raise ValueError("O token a ser descriptografado não pode ser vazio.")
    try:
        plain_bytes = fernet.decrypt(str(token).encode("utf-8"))
        return plain_bytes.decode("utf-8")
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "Falha ao descriptografar o token: chave FERNET_KEY incorreta ou token corrompido."
        ) from exc
