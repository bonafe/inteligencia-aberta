#!/usr/bin/env bash
# Copia o runtime do Ultima Agora para o portal (mesmo regime do UltimaJS no projeto Agora: cópia versionada,
# sem gerenciador de pacotes). O código do Agora NÃO se edita aqui: muda-se no repositório dele e re-sincroniza.
#
#   scripts/sincronizar_agora.sh [caminho-do-repositorio-ultima-agora]
set -euo pipefail
cd "$(dirname "$0")/.."

ORIGEM="${1:-${AGORA_SRC:-../ultima-agora}}"
DESTINO="services/portal/static/agora"
[ -f "$ORIGEM/core/components/agora_component.js" ] || { echo "Repositório do Ultima Agora não encontrado em $ORIGEM" >&2; exit 2; }

rm -rf "$DESTINO"
mkdir -p "$DESTINO"
# Só o que roda no navegador: sem testes, docs, servidor, ferramentas nem o hospedeiro de desenvolvimento
for pasta in core collaboration components ui vendor; do
    cp -r "$ORIGEM/$pasta" "$DESTINO/$pasta"
done

# O serviço de tempo real (agora-sync) vai para o contexto de build do compose
SERVIDOR="services/agora-sync"
rm -rf "$SERVIDOR"
mkdir -p "$SERVIDOR"
cp -r "$ORIGEM/server/agora-sync/src" "$SERVIDOR/src"
cp "$ORIGEM/server/agora-sync/package.json" "$ORIGEM/server/agora-sync/package-lock.json" "$ORIGEM/server/agora-sync/Dockerfile" "$ORIGEM/server/agora-sync/docker-entrypoint.sh" "$SERVIDOR/"

cp "$ORIGEM/sw.js" "$DESTINO/sw.js"
cp "$ORIGEM/tools/gerar_precache.py" scripts/gerar_precache.py      # o teste do front confere que a lista está em dia

# Lista do que o service worker guarda para o Agora abrir sem rede. GERADA (nunca à mão): esquecer um arquivo = ele some
# offline. A versão é o hash do conteúdo, então um deploy novo troca o cache sozinho.
python3 "$ORIGEM/tools/gerar_precache.py" --raiz services/portal/static --saida services/portal/static/agora-ia/precache.json \
    --prefixo /static/ --pastas agora agora-ia --extras --sem-pagina-raiz

COMMIT="$(git -C "$ORIGEM" rev-parse --short HEAD 2>/dev/null || echo desconhecido)"
SUJO="$(git -C "$ORIGEM" status --porcelain 2>/dev/null | head -1)"
cat > "$DESTINO/ORIGEM.txt" <<TXT
Cópia do Ultima Agora (runtime do navegador).
commit: $COMMIT${SUJO:+ (com alterações não commitadas na origem)}
gerado por scripts/sincronizar_agora.sh — não edite estes arquivos; altere o projeto de origem e sincronize de novo.
TXT
cp "$DESTINO/ORIGEM.txt" "$SERVIDOR/ORIGEM.txt"
echo "Agora sincronizado de $ORIGEM ($COMMIT) para $DESTINO e $SERVIDOR"
