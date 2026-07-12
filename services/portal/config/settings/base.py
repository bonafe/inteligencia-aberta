import os
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables from the root .env file
env_path = BASE_DIR.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "inseguro-apenas-para-dev")

DEBUG = False

ALLOWED_HOSTS = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "apps.accounts.apps.AccountsConfig",
    "apps.artifacts.apps.ArtifactsConfig",
    "apps.infrastructure.apps.InfrastructureConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Exige sessão autenticada em toda URL fora da allowlist (secure-by-default).
    # Precisa vir DEPOIS do AuthenticationMiddleware (usa request.user).
    "apps.accounts.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "inteligencia_aberta"),
        "USER": os.environ.get("POSTGRES_USER", "postgres"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/entrar/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/entrar/"

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "America/Sao_Paulo"
CELERY_TASK_TRACK_STARTED = True
QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))
FRAGMENT_CHUNK_SIZE = int(os.environ.get("FRAGMENT_CHUNK_SIZE", "1000"))
FRAGMENT_OVERLAP = int(os.environ.get("FRAGMENT_OVERLAP", "100"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_CLASSIFIER_MODEL = os.environ.get("LLM_CLASSIFIER_MODEL", "claude-haiku-4-5")
LLM_EXTRACTOR_MODEL = os.environ.get("LLM_EXTRACTOR_MODEL", "claude-sonnet-5")

# ── Segurança / Autenticação ─────────────────────────────────────────────────
# JWT_SIGNING_KEY: segredo compartilhado com o orchestrator — o portal assina os
# tokens da extensão, o orchestrator valida. Cai para SECRET_KEY se não definido
# (ok em dev; em produção deve ser explícito e igual nos dois serviços).
JWT_SIGNING_KEY = os.environ.get("JWT_SIGNING_KEY", SECRET_KEY)

# INTERNAL_API_TOKEN: segredo do canal serviço-a-serviço. O orchestrator o envia
# no header X-Internal-Token ao criar artefatos; a API interna do portal valida.
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")

SIMPLE_JWT = {
    "SIGNING_KEY": JWT_SIGNING_KEY,
    "ALGORITHM": "HS256",
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# DRF é usado APENAS nas rotas de token (/api/v1/token/*). As demais views do
# portal continuam sendo django.views.View puras com autenticação de sessão.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

CELERY_BEAT_SCHEDULE = {
    "scan-unprocessed-documents": {
        "task": "apps.artifacts.tasks.scan_unprocessed_documents",
        "schedule": 120.0,  # a cada 2 minutos
    },
}
