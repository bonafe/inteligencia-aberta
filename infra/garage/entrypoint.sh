#!/bin/sh
# Sobe o Garage e, de forma idempotente, deixa-o pronto para uso:
# layout de nó único, chave S3 (S3_ACCESS_KEY/S3_SECRET_KEY) e buckets (S3_BUCKETS).
# O healthcheck só passa depois disso (arquivo /tmp/pronto), então quem depende
# do serviço com service_healthy já o encontra com buckets e chave criados.
set -eu

: "${GARAGE_RPC_SECRET:?defina GARAGE_RPC_SECRET (64 hex)}"
: "${S3_ACCESS_KEY:?defina S3_ACCESS_KEY (GK + 24 hex)}"
: "${S3_SECRET_KEY:?defina S3_SECRET_KEY (64 hex)}"
S3_BUCKETS="${S3_BUCKETS:-inteligencia-aberta-mhtml}"
GARAGE_CAPACITY="${GARAGE_CAPACITY:-100GB}"

mkdir -p /var/lib/garage/meta /var/lib/garage/data
rm -f /tmp/pronto

garage -c /etc/garage.toml server &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null' TERM INT

g() { garage -c /etc/garage.toml "$@"; }

echo "aguardando o daemon..."
i=0
until g status >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 60 ] && { echo "daemon não respondeu" >&2; exit 1; }
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "daemon encerrou" >&2; exit 1; }
  sleep 1
done

if g status | grep -q "NO ROLE ASSIGNED"; then
  NODE_ID=$(g node id -q | cut -d@ -f1)
  echo "atribuindo layout de nó único..."
  g layout assign -z dc1 -c "$GARAGE_CAPACITY" "$NODE_ID"
  g layout apply --version 1
fi

if ! g key info "$S3_ACCESS_KEY" >/dev/null 2>&1; then
  g key import --yes -n instancia "$S3_ACCESS_KEY" "$S3_SECRET_KEY"
fi
g key allow --create-bucket "$S3_ACCESS_KEY"

for bucket in $S3_BUCKETS; do
  g bucket info "$bucket" >/dev/null 2>&1 || g bucket create "$bucket"
  g bucket allow --read --write --owner "$bucket" --key "$S3_ACCESS_KEY"
done

touch /tmp/pronto
echo "garage pronto"
wait "$SERVER_PID"
