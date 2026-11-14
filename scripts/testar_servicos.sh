#!/usr/bin/env bash
# Roda os testes do MCP e do orchestrator, cada um na própria imagem (instala pytest só no container descartável).
# O portal tem a suíte própria: docker compose exec portal python -m pytest tests/ -q
set -euo pipefail
cd "$(dirname "$0")/.."
for servico in mcp orchestrator; do
    echo "== $servico"
    docker compose run --rm --no-deps -v "$PWD/services/$servico:/app" -w /app "$servico" \
        sh -c "pip install -q -r requirements-dev.txt 2>&1 | grep -viE 'warning|notice' || true; python -m pytest tests -q -p no:cacheprovider"
done
