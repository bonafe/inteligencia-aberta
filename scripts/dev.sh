#!/usr/bin/env bash
# docker compose do ambiente de desenvolvimento do nó (override de dev + docker-compose.dev.local.yml).
# Uso: scripts/dev.sh up -d --build | logs -f portal | restart worker | exec portal python manage.py migrate
# Perfis ligados por padrão: agora (tempo real) e ollama; troque com COMPOSE_PROFILES=agora ./scripts/dev.sh ...
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-agora,ollama}"
arquivos=(-f docker-compose.yml -f docker-compose.override.yml -f docker-compose.dev.local.yml)
# GPU NVIDIA: só quando o Docker tem o runtime `nvidia` (NVIDIA Container Toolkit). Sem ele o
# overlay faria o Ollama nem subir. Force com USAR_GPU=1 ou desligue com USAR_GPU=0.
if [ "${USAR_GPU:-auto}" = "1" ] || { [ "${USAR_GPU:-auto}" = "auto" ] && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; }; then
  arquivos+=(-f docker-compose.gpu.yml)
fi
exec docker compose "${arquivos[@]}" "$@"
