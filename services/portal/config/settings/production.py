import os
from .base import *
from config.segredos import validar_segredos


def _env_lista(nome: str) -> list[str]:
    """Lista separada por vírgula; ignora itens vazios (`""` → `[]`, não `['']`)."""
    return [item.strip() for item in os.environ.get(nome, "").split(",") if item.strip()]


DEBUG = False

# Falha cedo, com mensagem clara, em vez de subir com segredo vazio/de exemplo.
# ANTHROPIC_API_KEY é opcional e fica de fora de propósito.
validar_segredos([
    "DJANGO_SECRET_KEY", "JWT_SIGNING_KEY", "INTERNAL_API_TOKEN", "MCP_API_TOKEN",
    "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD",
])

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# Nomes pelos quais o portal é acessado (ex.: host.tailnet.ts.net,ia.exemplo.com.br).
ALLOWED_HOSTS = _env_lista("ALLOWED_HOSTS")
# Hosts internos que sempre precisam passar: o orchestrator e o MCP chamam
# `portal:8000`, e o healthcheck do Docker chama `localhost`. Sem isso, o
# Django devolveria 400 (DisallowedHost) a chamadas legítimas do próprio stack.
ALLOWED_HOSTS += [h for h in ("portal", "localhost", "127.0.0.1") if h not in ALLOWED_HOSTS]
# Origens completas, com esquema (ex.: https://ia.exemplo.com.br). Necessário para
# POST de formulário (login, registro) quando o acesso é por HTTPS atrás de proxy.
CSRF_TRUSTED_ORIGINS = _env_lista("CSRF_TRUSTED_ORIGINS")

# TLS_MODE descreve o que existe NA FRENTE do portal:
#   proxy → um proxy termina o HTTPS (tailscale serve, Caddy) e repassa
#           X-Forwarded-Proto. É o padrão: HTTPS em todos os hosts.
#   none  → HTTP puro (teste local, ou rede fechada onde isso é decisão consciente).
TLS_MODE = os.environ.get("TLS_MODE", "proxy")
if TLS_MODE not in ("proxy", "none"):
    raise RuntimeError(f"TLS_MODE inválido: {TLS_MODE!r} (use 'proxy' ou 'none')")

_https = TLS_MODE == "proxy"

if _https:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _https
SESSION_COOKIE_SECURE = _https
CSRF_COOKIE_SECURE = _https
# HSTS é "pegajoso" no navegador: fica 0 até o operador ligar de propósito.
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0")) if _https else 0

# Chamadas que chegam por HTTP simples dentro da rede do compose não passam pelo
# proxy e não carregam X-Forwarded-Proto — com o redirect ligado receberiam 301
# (e um POST viraria GET). Healthcheck do Docker e canal serviço-a-serviço.
SECURE_REDIRECT_EXEMPT = [
    r"^health/?$",
    r"^artifacts/api/v1/artefatos/",
    r"^eventos/api/v1/ingest/",
]
