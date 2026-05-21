import os
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

CELERY_BEAT_SCHEDULE = {
    "scan-unprocessed-documents": {
        "task": "apps.artifacts.tasks.scan_unprocessed_documents",
        "schedule": 120.0,  # a cada 2 minutos
    },
}
