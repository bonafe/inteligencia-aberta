#!/bin/bash
# Executor do IPED — ADR 019. Roda DENTRO do contêiner `iped` (sem rede, entrada somente leitura).
#
# Protocolo por arquivos no volume /trabalho (o `worker-iped` escreve; este script só lê pedidos):
#   /trabalho/pedidos/<id>.pedido   linhas `chave=valor`: perfil=<perfil>  entrada=<caminho relativo a /entrada>
#   /trabalho/estado/<id>           executando | concluido | falhou | cancelado   (escrito aqui)
#   /trabalho/codigo/<id>           código de saída do IPED
#   /trabalho/logs/<id>.log         saída do IPED
#   /trabalho/cancelar/<id>         se existir enquanto executa, o IPED é interrompido
# O caso gerado vai para /saida/<id>.
#
# Nada do pedido passa por `eval`: cada valor é validado por padrão e só então usado como argumento.
# O mesmo protocolo vale para um IPED nativo no host (ver docs/deploy.md): basta rodar este script lá.
set -u

TRABALHO="${IPED_TRABALHO:-/trabalho}"
ENTRADA="${IPED_ENTRADA:-/entrada}"
SAIDA="${IPED_SAIDA:-/saida}"
IPED_DIR="${IPED_DIR:-$(dirname "$(find / -maxdepth 4 -name iped.jar -not -path '*/proc/*' 2>/dev/null | head -n1)")}"
TIMEOUT_S="${IPED_TIMEOUT_S:-86400}"
PERFIS=" forensic triage fastmode pedo blind "
RE_ID='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

mkdir -p "$TRABALHO"/{pedidos,estado,codigo,logs,cancelar} "$SAIDA"

estado() { printf '%s\n' "$2" > "$TRABALHO/estado/$1.tmp" && mv "$TRABALHO/estado/$1.tmp" "$TRABALHO/estado/$1"; }

processar() {
    local id="$1" perfil="" entrada="" k v
    while IFS='=' read -r k v; do
        case "$k" in
            perfil) perfil="$v" ;;
            entrada) entrada="$v" ;;
        esac
    done < "$TRABALHO/pedidos/$id.pedido"

    # Validação: perfil de uma lista fechada; entrada relativa, sem `..`, sem começar por `-` ou `/`.
    if [[ "$PERFIS" != *" $perfil "* ]] || [[ -z "$entrada" ]] || [[ "$entrada" == /* ]] || [[ "$entrada" == -* ]] \
       || [[ "/$entrada/" == *"/../"* ]]; then
        echo "pedido inválido" > "$TRABALHO/logs/$id.log"; echo 2 > "$TRABALHO/codigo/$id"; estado "$id" falhou; return
    fi
    # Resolve links simbólicos e exige que o alvo continue dentro de /entrada.
    local alvo; alvo="$(realpath -e -- "$ENTRADA/$entrada" 2>/dev/null)"
    if [[ -z "$alvo" ]] || [[ "$alvo" != "$ENTRADA" && "$alvo" != "$ENTRADA"/* ]]; then
        echo "entrada fora de $ENTRADA ou inexistente" > "$TRABALHO/logs/$id.log"; echo 2 > "$TRABALHO/codigo/$id"; estado "$id" falhou; return
    fi

    estado "$id" executando
    ( cd "$IPED_DIR" && exec timeout --signal=TERM --kill-after=30 "$TIMEOUT_S" \
        java -Duser.home="${HOME:-/tmp}" -jar iped.jar --nogui -d "$alvo" -o "$SAIDA/$id" -profile "$perfil" ) \
        > "$TRABALHO/logs/$id.log" 2>&1 &
    local pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        if [[ -e "$TRABALHO/cancelar/$id" ]]; then kill -TERM "$pid" 2>/dev/null; sleep 5; kill -KILL "$pid" 2>/dev/null; break; fi
        sleep 2
    done
    wait "$pid"; local cod=$?
    echo "$cod" > "$TRABALHO/codigo/$id"
    if [[ "$cod" -ne 0 ]]; then   # o IPED manda o detalhe do erro para um log em /tmp; traz o final dele
        local lg; lg="$(ls -t /tmp/IPED-*.log 2>/dev/null | head -n1)"
        [[ -n "$lg" ]] && { echo "--- $lg (final) ---"; tail -n 40 "$lg"; } >> "$TRABALHO/logs/$id.log"
    fi
    if [[ -e "$TRABALHO/cancelar/$id" ]]; then estado "$id" cancelado
    elif [[ "$cod" -eq 0 ]]; then estado "$id" concluido
    else estado "$id" falhou; fi
}

echo "iped-runner: aguardando pedidos em $TRABALHO/pedidos (IPED em $IPED_DIR)"
while true; do
    for arq in "$TRABALHO"/pedidos/*.pedido; do
        [[ -e "$arq" ]] || continue
        id="$(basename "$arq" .pedido)"
        [[ "$id" =~ $RE_ID ]] || continue
        [[ -e "$TRABALHO/estado/$id" ]] && continue
        processar "$id"
    done
    sleep 2
done
