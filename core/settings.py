"""Configurações principais do projeto Django (Motor de Otimização de Anúncios)."""
import os
from pathlib import Path

from dotenv import load_dotenv
from kombu import Queue

# Diretório raiz do projeto
BASE_DIR = Path(__file__).resolve().parent.parent

# Carrega variáveis de ambiente do arquivo .env (se existir)
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    """Converte variável de ambiente textual para booleano de forma estrita."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


# ==============================================================================
# SEGURANÇA E AMBIENTE
# ==============================================================================
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-dev-only-key-change-in-production",
)

DEBUG = _env_bool("DEBUG", default=False)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

# Origens HTTPS confiáveis para túneis locais (ngrok / cloudflared) durante OAuth2
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# Trava global de segurança (Regra Inviolável #2 - CONTEXTO_SISTEMA.md):
# Enquanto False, nenhuma requisição POST/PUT/PATCH/DELETE de edição de anúncio pode ser enviada.
MARKETPLACE_WRITE_ENABLED: bool = _env_bool("MARKETPLACE_WRITE_ENABLED", default=False)

# Credenciais e chaves externas
FERNET_KEY = os.getenv("FERNET_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DAILY_LLM_TOKEN_LIMIT = int(os.getenv("DAILY_LLM_TOKEN_LIMIT", "500000"))
ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "http://localhost:8000/vault/ml/callback/",
)


# ==============================================================================
# APLICAÇÕES INSTALADAS
# ==============================================================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Bibliotecas de infraestrutura
    "django_celery_beat",
    # Apps modulares do domínio
    "apps.accounts_vault.apps.AccountsVaultConfig",
    "apps.marketplaces.apps.MarketplacesConfig",
    "apps.catalog.apps.CatalogConfig",
    "apps.agents.apps.AgentsConfig",
    "apps.media_studio.apps.MediaStudioConfig",
    "apps.governance.apps.GovernanceConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"


# ==============================================================================
# BANCO DE DADOS (PostgreSQL padrão com fallback opcional para SQLite)
# ==============================================================================
USE_SQLITE = _env_bool("USE_SQLITE", default=False)

if USE_SQLITE:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "catalog_engine_db"),
            "USER": os.getenv("DB_USER", "postgres"),
            "PASSWORD": os.getenv("DB_PASSWORD", "postgres"),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }


# ==============================================================================
# VALIDAÇÃO DE SENHAS
# ==============================================================================
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# ==============================================================================
# INTERNACIONALIZAÇÃO E FUSO HORÁRIO
# ==============================================================================
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# ==============================================================================
# ARQUIVOS ESTÁTICOS E MÍDIA
# ==============================================================================
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ==============================================================================
# CELERY & ISOLAMENTO DE FILAS (queue_fast e queue_media)
# ==============================================================================
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

CELERY_TASK_DEFAULT_QUEUE = "queue_fast"
CELERY_TASK_QUEUES = (
    Queue("queue_fast"),
    Queue("queue_media"),
)
CELERY_TASK_ROUTES = {
    "apps.media_studio.*": {"queue": "queue_media"},
    "*": {"queue": "queue_fast"},
}
