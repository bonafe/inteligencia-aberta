#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "ATENÇÃO: Este script apaga todos os containers, imagens e dados persistentes."
echo "Isso remove artefatos, usuários e embeddings gravados em ./data/"
echo ""
read -rp "Confirma? (s/N) " resposta
if [[ "${resposta,,}" != "s" ]]; then
    echo "Cancelado."
    exit 0
fi

echo "==> Parando e removendo containers..."
docker compose down --remove-orphans

echo "==> Removendo dados persistentes (postgres, minio, qdrant, redis)..."
sudo rm -rf "$REPO_ROOT/data/"

echo "==> Removendo imagens buildadas do projeto..."
docker compose images -q 2>/dev/null | xargs -r docker rmi -f || true

echo ""
echo "Ambiente limpo. Para subir novamente: ./scripts/subir_containers.sh"
