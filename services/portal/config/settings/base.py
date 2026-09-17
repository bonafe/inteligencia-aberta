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
    # daphne precisa vir ANTES de staticfiles: ele substitui o runserver por um
    # servidor ASGI de desenvolvimento, e é o que faz o WebSocket funcionar em dev
    # sem mudar o comando do docker-compose.override.yml.
    "daphne",
    "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "apps.accounts.apps.AccountsConfig",
    "apps.artifacts.apps.ArtifactsConfig",
    "apps.infrastructure.apps.InfrastructureConfig",
    "apps.events.apps.EventsConfig",
    "apps.cluster.apps.ClusterConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serve os estáticos a partir do próprio processo ASGI. Sem isto, o daphne
    # de produção (ver Dockerfile) não entregaria o CSS/JS do painel de eventos
    # e da galeria — o runserver de desenvolvimento os serve sozinho, o que
    # esconderia o problema até o deploy.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
# Ativos próprios do projeto (árvore JSON compartilhada entre a galeria e o
# painel de eventos). Em produção, `collectstatic` os leva para STATIC_ROOT.
STATICFILES_DIRS = [BASE_DIR / "static"]

# WhiteNoise com hash no nome do arquivo: permite cache longo sem servir
# versão velha depois de um deploy. Exige `collectstatic` (feito no Dockerfile).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

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

# ── Tempo real (Django Channels) ─────────────────────────────────────────────
# O painel de eventos recebe cada etapa do pipeline por WebSocket. O channel
# layer usa o db 1 do Redis, separado do broker do Celery (db 0), para que a
# fila de tarefas e o fanout do painel não compartilhem keyspace.
ASGI_APPLICATION = "config.asgi.application"

CHANNEL_LAYER_URL = os.environ.get("CHANNEL_LAYER_URL", REDIS_URL.rsplit("/", 1)[0] + "/1")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [CHANNEL_LAYER_URL], "capacity": 2000, "expiry": 30},
    },
}

QDRANT_HOST = os.environ.get("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))
FRAGMENT_CHUNK_SIZE = int(os.environ.get("FRAGMENT_CHUNK_SIZE", "1000"))
FRAGMENT_OVERLAP = int(os.environ.get("FRAGMENT_OVERLAP", "100"))

# Quantos artefatos-documento mais recentes entram na carga inicial do Mapa
# Vivo (apps/artifacts graph.py) — o restante é acessível via paginação "carregar mais antigos".
MAPA_VIVO_LIMITE_ARTEFATOS = int(os.environ.get("MAPA_VIVO_LIMITE_ARTEFATOS", "150"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_CLASSIFIER_MODEL = os.environ.get("LLM_CLASSIFIER_MODEL", "claude-haiku-4-5")
LLM_EXTRACTOR_MODEL = os.environ.get("LLM_EXTRACTOR_MODEL", "claude-sonnet-5")

# Ollama roda nativo no host do usuário, fora do Docker — não é um serviço do
# compose. host.docker.internal exige `extra_hosts: host-gateway` no Linux
# (não é automático como no Docker Desktop de Mac/Windows; ver docker-compose.yml).
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_TIMEOUT_S = int(os.environ.get("OLLAMA_TIMEOUT_S", "120"))
# Sem isto, o Ollama usa o default de 4096 tokens de contexto mesmo em modelos
# que suportam muito mais — em modelos "thinking" o raciocínio sozinho estoura
# 4096 antes de sobrar espaço para a resposta final, e a chamada volta vazia.
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "4096"))
# Threads de CPU que o Ollama usa para inferência. Sem isto (e sem um Modelfile
# próprio que fixe `num_thread`), o Ollama subestima a máquina em alguns casos
# e roda com uma fração da capacidade disponível. Aplicado em toda chamada,
# para todo modelo — não depende do usuário lembrar de escolher um modelo
# "-max" específico na UI (ver infra/ollama/Modelfile-qwen-max, que faz o
# mesmo ajuste só para um modelo, para uso fora do app via `ollama run`).
# Default: todas as CPUs visíveis para o processo (o container, tipicamente
# igual ao host, salvo cgroup limitando).
OLLAMA_NUM_THREAD = int(os.environ.get("OLLAMA_NUM_THREAD") or os.cpu_count() or 4)

# ── Segurança / Autenticação ─────────────────────────────────────────────────
# JWT_SIGNING_KEY: segredo compartilhado com o orchestrator — o portal assina os
# tokens da extensão, o orchestrator valida. Cai para SECRET_KEY se não definido
# (ok em dev; em produção deve ser explícito e igual nos dois serviços).
JWT_SIGNING_KEY = os.environ.get("JWT_SIGNING_KEY", SECRET_KEY)

# INTERNAL_API_TOKEN: segredo do canal serviço-a-serviço. O orchestrator o envia
# no header X-Internal-Token ao criar artefatos; a API interna do portal valida.
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")

# ── Cluster multi-máquina (apps.cluster) ─────────────────────────────────────
# Não existe conceito de "máquina primária" no vocabulário do cluster — só
# capacidade: uma máquina hospeda (ou não) a infraestrutura compartilhada
# (Postgres/Redis/MinIO/Qdrant). Nada além disso muda o tratamento dela (o
# roteador de LLM, por exemplo, já trata toda `Maquina` como igual). Ver
# ADR-006 (docs/arquitetura/decisoes/006-cluster-adaptativo-multiproprietario.md).
#
# CLUSTER_MACHINE_ID: id da `Maquina` que ESTA instância representa (gerado
# por `scripts/entrar_no_cluster.py` ou `manage.py registrar_maquina` numa
# máquina que entrou no cluster de outra). Vazio numa instalação de máquina
# única — o heartbeat simplesmente não roda (ver apps/cluster/tasks.py).
CLUSTER_MACHINE_ID = os.environ.get("CLUSTER_MACHINE_ID", "")
# CLUSTER_HOSPEDA_INFRA=true (default): esta máquina roda o catch-up scan do
# pipeline (scan_unprocessed_documents) — só precisa rodar uma vez por
# cluster, não uma vez por máquina, e quem hospeda a infra compartilhada é o
# lugar natural pra isso. Também é o que POST /cluster/api/v1/status/
# (StatusPublicoView) reporta — é assim que uma máquina nova
# (scripts/entrar_no_cluster.py) descobre qual peer do tailnet tem a infra,
# sem endereço fixo configurado à mão.
CLUSTER_HOSPEDA_INFRA = os.environ.get("CLUSTER_HOSPEDA_INFRA", "true").lower() == "true"
# CLUSTER_JOIN_SECRET: segredo único do cluster (definido uma vez no nó que
# hospeda a infra, distribuído pra quem for adicionar máquina) — autentica
# POST /cluster/api/v1/join/, o autorregistro usado por
# scripts/entrar_no_cluster.py. Vazio = endpoint desligado (404).
CLUSTER_JOIN_SECRET = os.environ.get("CLUSTER_JOIN_SECRET", "")
# LLM_GATEWAY_TOKEN: segredo do gateway compatível com OpenAI em
# POST /v1/chat/completions (apps.cluster.gateway) — só fala com Ollama das
# máquinas do cluster, nunca com provider externo. Vazio = gateway desligado
# (404), fail-closed por padrão.
LLM_GATEWAY_TOKEN = os.environ.get("LLM_GATEWAY_TOKEN", "")

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
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# drf-spectacular: gera o schema OpenAPI e o Swagger UI em /api/docs/.
# SERVE_PERMISSIONS=AllowAny é deliberado: a página de documentação (descrição
# dos endpoints, sem dados reais) fica pública para fins didáticos e para
# desenvolvedores novos explorarem a API sem precisar de conta. Isso não afeta
# a segurança dos endpoints em si — cada um continua exigindo sua própria
# credencial (JWT, sessão ou token de serviço) quando de fato chamado.
SPECTACULAR_SETTINGS = {
    "TITLE": "Inteligência Aberta — API do Portal",
    "DESCRIPTION": (
        "Endpoints REST do portal: emissão/renovação de JWT usado pela extensão "
        "Chrome. A criação de artefatos (canal orchestrator→portal) é uma view "
        "Django simples protegida por token de serviço e não aparece aqui — "
        "ver docs/seguranca/autenticacao.md."
    ),
    "VERSION": "1.0.0",
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVE_AUTHENTICATION": [],
}

CELERY_BEAT_SCHEDULE = {
    # Heartbeat de recursos da máquina — inofensivo rodar em toda máquina do
    # cluster, hospede infra ou não; sem CLUSTER_MACHINE_ID a task roda e
    # não faz nada (ver apps/cluster/tasks.py:emitir_heartbeat_maquina).
    "emitir-heartbeat-maquina": {
        "task": "apps.cluster.tasks.emitir_heartbeat_maquina",
        "schedule": 30.0,
    },
}
if CLUSTER_HOSPEDA_INFRA:
    # Catch-up scan só precisa rodar uma vez por cluster — rodar em toda
    # máquina duplicaria a varredura sem ganho nenhum.
    CELERY_BEAT_SCHEDULE["scan-unprocessed-documents"] = {
        "task": "apps.artifacts.tasks.scan_unprocessed_documents",
        "schedule": 120.0,  # a cada 2 minutos
    }
