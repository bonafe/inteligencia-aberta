#!/usr/bin/env bash
# docker compose do ambiente de desenvolvimento do nó (override de dev + docker-compose.dev.local.yml).
# Uso: scripts/dev.sh up -d --build | logs -f portal | restart worker | exec portal python manage.py migrate
# Perfis ligados por padrão: agora (tempo real) e ollama; troque com COMPOSE_PROFILES=agora ./scripts/dev.sh ...
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-agora,ollama}"
exec docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.dev.local.yml "$@"
