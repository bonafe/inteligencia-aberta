#!/usr/bin/env bash
# Pasta da instância: tudo de uma instância do Inteligência Aberta (segredos + dados) numa pasta só.
#
#   scripts/instancia.sh <pasta> criar     [--entrada <pasta-de-evidencias>] [--migrar-de <data-antigo>] [--env-de <arquivo>]
#   scripts/instancia.sh <pasta> subir     [--prod] [args do `docker compose up`...]
#   scripts/instancia.sh <pasta> parar     [--prod]
#   scripts/instancia.sh <pasta> estado    [--prod]
#   scripts/instancia.sh <pasta> backup    [--completo] [--prod]
#   scripts/instancia.sh <pasta> restaurar <pasta-de-backup> [--forcar]
#
# A pasta tem: `.env` (segredos e caminhos), `dados/` (Postgres, Garage, Qdrant, IPED...) e `backups/`.
# Copiar a pasta (com os serviços PARADOS) e rodar `subir` apontando para a cópia restaura a instância.
# Atenção: a pasta carrega a IDENTIDADE da instância (a chave `did:key` está no banco). Não suba a mesma
# pasta em duas máquinas ao mesmo tempo: para uma segunda máquina, crie uma instância nova e enrole-a como par.
#
# Salvaguardas: recusa uma pasta que esteja no disco do sistema (use --permitir-disco-do-sistema) e uma pasta
# sem a marca `.ia-instancia` — se o disco grande não estiver montado, o Docker não cria os dados no disco errado.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AJUDANTE="${IA_IMAGEM_AJUDANTE:-alpine:3.20}"   # contêiner root descartável: copia preservando donos (uid do Postgres etc.)

uso() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
[[ $# -ge 2 ]] || uso
PASTA="$(realpath -m -- "$1")"; CMD="$2"; shift 2
DADOS="$PASTA/dados"; ENVF="$PASTA/.env"; MARCA="$PASTA/.ia-instancia"

PROD=0; COMPLETO=0; FORCAR=0; PERMITE_SISTEMA=0; ENTRADA=""; MIGRAR_DE=""; ENV_DE=""; RESTO=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --prod) PROD=1 ;;
        --completo) COMPLETO=1 ;;
        --forcar) FORCAR=1 ;;
        --permitir-disco-do-sistema) PERMITE_SISTEMA=1 ;;
        --entrada) ENTRADA="$2"; shift ;;
        --migrar-de) MIGRAR_DE="$2"; shift ;;
        --env-de) ENV_DE="$2"; shift ;;
        *) RESTO+=("$1") ;;
    esac
    shift
done

ARQS=(-f docker-compose.yml)
if [[ $PROD -eq 1 ]]; then ARQS+=(-f docker-compose.prod.yml); else ARQS+=(-f docker-compose.override.yml); fi

die() { echo "instancia: $*" >&2; exit 1; }
compose() { (cd "$REPO" && docker compose --env-file "$ENVF" "${ARQS[@]}" "$@"); }

# `definir_env CHAVE VALOR`: troca (ou acrescenta) a linha no .env, com o valor entre aspas.
definir_env() {
    local k="$1" v="$2" tmp; tmp="$(mktemp)"
    { grep -v -E "^${k}=" "$ENVF" 2>/dev/null || true; printf '%s="%s"\n' "$k" "$v"; } > "$tmp"
    cat "$tmp" > "$ENVF"; rm -f "$tmp"
}

ponto_de_montagem() { # do ancestral existente mais próximo
    local p="$1"; while [[ ! -e "$p" ]]; do p="$(dirname "$p")"; done; findmnt -no TARGET -T "$p"
}

checar_disco() {
    local alvo; alvo="$(ponto_de_montagem "$PASTA")"
    if [[ "$alvo" == "/" && $PERMITE_SISTEMA -eq 0 ]]; then
        die "a pasta está no disco do sistema (/). Use um disco de dados ou --permitir-disco-do-sistema."
    fi
}

exigir_instancia() {
    [[ -e "$MARCA" ]] || die "não há uma instância em '$PASTA' (falta .ia-instancia). O disco está montado? Use 'criar'."
    [[ -f "$ENVF" ]] || die "falta $ENVF"
    checar_disco
    definir_env DATA_DIR "$DADOS"   # é isto que faz "copiar a pasta e subir" funcionar em outro caminho
    mkdir -p "$DADOS"/iped/{trabalho,saida,home,tmp,entrada}
}

ajudante() { docker run --rm "$@"; }   # contêiner root descartável

case "$CMD" in
criar)
    checar_disco
    [[ ! -e "$MARCA" ]] || die "já existe uma instância em '$PASTA'"
    mkdir -p "$DADOS"/iped/{trabalho,saida,home,tmp,entrada} "$PASTA/backups"
    if [[ ! -f "$ENVF" ]]; then
        origem="${ENV_DE:-}"; [[ -n "$origem" ]] || { [[ -f "$REPO/.env" ]] && origem="$REPO/.env" || origem="$REPO/.env.example"; }
        cp -- "$origem" "$ENVF"
    fi
    chmod 600 "$ENVF"
    definir_env DATA_DIR "$DADOS"
    [[ -z "$ENTRADA" ]] || definir_env IPED_ENTRADA_HOST "$(realpath -- "$ENTRADA")"
    if [[ -n "$MIGRAR_DE" ]]; then
        [[ -d "$MIGRAR_DE" ]] || die "origem da migração inexistente: $MIGRAR_DE"
        [[ ! -e "$DADOS/postgres" ]] || die "já há um Postgres em $DADOS; não vou sobrescrever"
        ajudante -v "$(realpath -- "$MIGRAR_DE")":/o:ro -v "$DADOS":/d "$AJUDANTE" sh -c 'cd /o && tar -cpf - . | tar -xpf - -C /d'
        echo "dados migrados de $MIGRAR_DE"
    fi
    date -u +%FT%TZ > "$MARCA"
    echo "instância criada em $PASTA — próximos passos: revise $ENVF e rode: $0 '$PASTA' subir"
    ;;
subir)
    exigir_instancia; compose up -d --build "${RESTO[@]}" ;;
parar)
    exigir_instancia; compose stop ;;
estado)
    exigir_instancia
    compose ps
    df -h "$PASTA" | tail -1
    ajudante -v "$DADOS":/d:ro "$AJUDANTE" sh -c 'cd /d && du -sh * 2>/dev/null' ;;
backup)
    exigir_instancia
    rodando="$(compose ps --services --status running 2>/dev/null | tr '\n' ' ')"
    ts="$(date +%Y%m%d-%H%M%S)"; dest="$PASTA/backups/$ts"; mkdir -p "$dest"
    religar() { [[ -z "${rodando// }" ]] || compose start $rodando; }
    trap religar EXIT
    echo "parando os serviços para uma cópia consistente..."
    compose stop
    excl=()
    if [[ $COMPLETO -eq 0 ]]; then   # o que se refaz: modelos, caches, casos do IPED
        excl=(--exclude=./ollama --exclude=./fastembed-cache --exclude=./redis --exclude=./iped/saida --exclude=./iped/tmp --exclude=./iped/home)
    fi
    ajudante -v "$DADOS":/d:ro -v "$dest":/b "$AJUDANTE" tar -cpzf /b/dados.tar.gz -C /d "${excl[@]}" .
    cp -- "$ENVF" "$dest/env"; chmod 600 "$dest/env"
    echo "backup em $dest ($(du -sh "$dest" | cut -f1))"
    ;;
restaurar)
    [[ ${#RESTO[@]} -eq 1 ]] || die "informe a pasta do backup"
    origem="$(realpath -- "${RESTO[0]}")"
    [[ -f "$origem/dados.tar.gz" && -f "$origem/env" ]] || die "'$origem' não é um backup (falta dados.tar.gz ou env)"
    checar_disco
    if [[ -d "$DADOS" && -n "$(ls -A "$DADOS" 2>/dev/null)" ]]; then
        [[ $FORCAR -eq 1 ]] || die "'$DADOS' não está vazia; use --forcar para guardar a atual ao lado e restaurar"
        mv -- "$DADOS" "$DADOS.antes-$(date +%Y%m%d-%H%M%S)"
    fi
    mkdir -p "$DADOS" "$PASTA/backups"
    ajudante -v "$DADOS":/d -v "$origem":/b:ro "$AJUDANTE" tar -xpzf /b/dados.tar.gz -C /d
    cp -- "$origem/env" "$ENVF"; chmod 600 "$ENVF"
    definir_env DATA_DIR "$DADOS"
    mkdir -p "$DADOS"/iped/{trabalho,saida,home,tmp,entrada}
    date -u +%FT%TZ > "$MARCA"
    echo "restaurado em $PASTA — confira IPED_ENTRADA_HOST em $ENVF e rode: $0 '$PASTA' subir"
    ;;
*) uso ;;
esac
