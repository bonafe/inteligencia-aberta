#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Subindo containers (modo desenvolvimento com hot-reload)..."
docker compose -f docker-compose.yml -f docker-compose.override.yml up --build -d

echo "==> Aguardando o portal ficar saudável..."
until docker compose exec portal python manage.py check --deploy 2>/dev/null | grep -q "System check identified no issues" || \
      curl -sf http://localhost:8000/health/ > /dev/null 2>&1; do
    sleep 2
done

echo "==> Rodando migrations..."
docker compose exec portal python manage.py migrate

echo ""
echo "Sistema no ar:"
echo "  Portal Web     → http://localhost:8000"
echo "  Galeria MHTML  → http://localhost:8000/artifacts/gallery/"
echo "  Django Admin   → http://localhost:8000/admin"
echo "  Orquestrador   → http://localhost:8001/docs"
echo "  MinIO Console  → http://localhost:9001"
echo ""
echo "Para ver os logs: docker compose logs -f"
