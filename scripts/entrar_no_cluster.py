#!/usr/bin/env python3
"""Adiciona esta máquina a um cluster já existente, sem digitar IP nem token
à mão — descobre o nó que hospeda a infraestrutura compartilhada (Postgres/
Redis/MinIO/Qdrant) perguntando aos peers do Tailscale/Headscale (já
autenticados, com nome DNS estável via MagicDNS), se autorregistra nele,
escreve o `.env` desta máquina e sobe o worker.

Não existe "máquina primária" no vocabulário do cluster (ver ADR-006,
docs/arquitetura/decisoes/006-cluster-adaptativo-multiproprietario.md) — só
capacidade: alguma máquina hospeda a infra compartilhada, e as demais
apontam pra ela. Isso não muda como ela é tratada em nada além disso (o
roteador de LLM, por exemplo, trata toda máquina como igual).

Por isso este script também não tenta "promover" ninguém sozinho se nenhum
peer responder: decidir qual máquina hospeda a infra continua manual (ver
docs/operacao/escala-multimaquina.md) — automatizar isso sem replicação real
do banco só criaria uma falsa sensação de resiliência.

Só usa a biblioteca padrão do Python (funciona em qualquer máquina com
Python 3 + `tailscale`, sem instalar nada extra) — roda no HOST, fora do
Docker, porque é o host que tem acesso ao `tailscale`.

Uso:
    python3 scripts/entrar_no_cluster.py --secret <segredo-do-cluster> \\
        --organizacao <slug> --modo compute [--ollama-endpoint http://localhost:11434]
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PORTA_PORTAL = 8000
TIMEOUT_PROBE_S = 2.0


def _tailscale_status() -> dict:
    try:
        saida = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10, check=True,
        )
    except FileNotFoundError:
        sys.exit("`tailscale` não encontrado nesta máquina — instale antes de rodar este script.")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"`tailscale status` falhou: {exc.stderr.strip()}")
    return json.loads(saida.stdout)


def _dns_name(peer: dict) -> str:
    """Nome DNS do MagicDNS, sem o ponto final (`antares.headscale.internal.` -> sem o último ponto)."""
    return (peer.get("DNSName") or "").rstrip(".")


def _hospeda_infra(dns_name: str) -> bool:
    """GET /cluster/api/v1/status/ nesse peer — False se não respondeu
    (recusado, timeout, não é este projeto — tudo isso é esperado, a maioria
    dos peers do tailnet não roda o portal nesta porta)."""
    url = f"http://{dns_name}:{PORTA_PORTAL}/cluster/api/v1/status/"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_PROBE_S) as resp:
            return bool(json.loads(resp.read()).get("hospeda_infra_compartilhada"))
    except Exception:
        return False


def descobrir_no_infraestrutura() -> str | None:
    status = _tailscale_status()
    peers = list(status.get("Peer", {}).values())
    print(f"==> {len(peers)} peer(s) conhecido(s) no tailnet, perguntando quem hospeda a infra…")
    for peer in peers:
        if not peer.get("Online"):
            continue
        dns_name = _dns_name(peer)
        if not dns_name:
            continue
        if _hospeda_infra(dns_name):
            print(f"==> nó de infraestrutura encontrado: {dns_name}")
            return dns_name
    return None


def entrar(no_infra: str, secret: str, apelido: str, organizacao: str, modo: str, ollama_endpoint: str) -> dict:
    corpo = json.dumps({
        "apelido": apelido, "organizacao": organizacao, "modo": modo, "ollama_endpoint": ollama_endpoint,
    }).encode()
    req = urllib.request.Request(
        f"http://{no_infra}:{PORTA_PORTAL}/cluster/api/v1/join/", data=corpo, method="POST",
        headers={"Content-Type": "application/json", "X-Cluster-Join-Secret": secret},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        corpo_erro = exc.read().decode(errors="replace")
        sys.exit(f"join recusado pelo nó de infraestrutura ({exc.code}): {corpo_erro}")


def escrever_env(no_infra: str, machine_id: str, machine_token: str) -> str:
    """Gera `.env` a partir de `.env.example` (molde), sobrescrevendo só as
    chaves que dependem desta máquina/deste join."""
    molde = os.path.join(REPO_ROOT, ".env.example")
    destino = os.path.join(REPO_ROOT, ".env")

    overrides = {
        "POSTGRES_HOST": no_infra,
        "REDIS_URL": f"redis://{no_infra}:6379/0",
        "MINIO_ENDPOINT": f"{no_infra}:9000",
        "QDRANT_HOST": no_infra,
        "CLUSTER_MACHINE_ID": machine_id,
        "CLUSTER_MACHINE_TOKEN": machine_token,
        "CLUSTER_HOSPEDA_INFRA": "false",
    }

    with open(molde, encoding="utf-8") as f:
        linhas = f.readlines()

    escritas = set()
    saida = []
    for linha in linhas:
        chave = linha.split("=", 1)[0].strip()
        if chave in overrides:
            saida.append(f"{chave}={overrides[chave]}\n")
            escritas.add(chave)
        else:
            saida.append(linha)

    faltando = set(overrides) - escritas
    if faltando:
        sys.exit(f".env.example não tem as chaves esperadas: {', '.join(sorted(faltando))} — atualize o molde.")

    if os.path.exists(destino):
        sys.exit(f"{destino} já existe — apague ou renomeie antes de rodar este script, pra não sobrescrever à toa.")

    with open(destino, "w", encoding="utf-8") as f:
        f.writelines(saida)
    return destino


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--secret", default=os.environ.get("CLUSTER_JOIN_SECRET", ""),
                        help="Segredo do cluster (ou variável de ambiente CLUSTER_JOIN_SECRET).")
    parser.add_argument("--organizacao", required=True, help="Slug da organização dona do cluster.")
    parser.add_argument("--modo", choices=["compute", "replica"], default="compute")
    parser.add_argument("--apelido", default=None, help="Nome desta máquina. Default: hostname do Tailscale.")
    parser.add_argument("--ollama-endpoint", default="",
                        help="URL do Ollama nesta máquina, se houver (ex.: http://localhost:11434).")
    parser.add_argument("--sem-subir", action="store_true",
                        help="Só descobre/registra/escreve o .env — não roda docker compose.")
    args = parser.parse_args()

    if not args.secret:
        sys.exit(
            "--secret (ou CLUSTER_JOIN_SECRET) é obrigatório — é o mesmo segredo "
            "definido no .env do nó que hospeda a infra compartilhada."
        )

    status = _tailscale_status()
    apelido = args.apelido or status.get("Self", {}).get("HostName") or "maquina-sem-nome"

    no_infra = descobrir_no_infraestrutura()
    if not no_infra:
        sys.exit(
            "Nenhuma máquina respondeu que hospeda a infra compartilhada. Configure uma "
            "manualmente antes (docs/operacao/escala-multimaquina.md) — este script não faz isso sozinho."
        )

    print(f"==> registrando '{apelido}' ({args.modo}) na organização '{args.organizacao}'…")
    resposta = entrar(no_infra, args.secret, apelido, args.organizacao, args.modo, args.ollama_endpoint)

    destino = escrever_env(no_infra, resposta["machine_id"], resposta["machine_token"])
    print(f"==> {destino} escrito (CLUSTER_MACHINE_ID={resposta['machine_id']}).")

    if args.sem_subir:
        print("==> --sem-subir: rode `docker compose -f docker-compose.worker-node.yml up -d --build` quando quiser.")
        return

    print("==> subindo o worker…")
    subprocess.run(
        ["docker", "compose", "-f", "docker-compose.worker-node.yml", "up", "-d", "--build"],
        cwd=REPO_ROOT, check=True,
    )
    print("==> pronto — em ~30s esta máquina aparece em MaquinaStatus no nó de infraestrutura.")


if __name__ == "__main__":
    main()
