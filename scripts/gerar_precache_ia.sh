#!/usr/bin/env bash
# Regenera a lista do que o service worker do Agora guarda (static/agora-ia/precache.json).
# Rode depois de mexer em qualquer arquivo de services/portal/static/agora ou agora-ia
# (scripts/testar_agora_ia.sh falha, de propósito, se a lista estiver desatualizada).
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$RAIZ/scripts/gerar_precache.py" --raiz "$RAIZ/services/portal/static" --saida "$RAIZ/services/portal/static/agora-ia/precache.json" \
    --prefixo /static/ --pastas agora agora-ia --extras --sem-pagina-raiz "$@"
