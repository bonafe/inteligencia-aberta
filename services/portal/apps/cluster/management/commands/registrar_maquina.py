"""Registra uma nova máquina no cluster e imprime o token de autenticação
uma única vez — depois disso, só o hash fica guardado (`Maquina.token_hash`)
e o token em claro é irrecuperável.

Uso:

    manage.py registrar_maquina --apelido notebook-trabalho --organizacao minha-org --modo compute
    manage.py registrar_maquina --apelido servidor-casa --organizacao minha-org --modo replica

O apelido e o token resultante vão para o `.env` da máquina nova:

    CLUSTER_MACHINE_ID=<id impresso>
    CLUSTER_MACHINE_TOKEN=<token impresso — guarde agora, não aparece de novo>

Registro manual — útil quando `CLUSTER_JOIN_SECRET` não está configurado, ou
quando você quer controlar `--dono` explicitamente. Para uma máquina nova se
autorregistrar sozinha (descoberta automática do nó de infraestrutura via
Tailscale/MagicDNS), ver `scripts/entrar_no_cluster.py`, que fala com
`POST /cluster/api/v1/join/` — mesma lógica de criação (`provisionamento.py`),
caminho diferente.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.cluster.models import Maquina
from apps.cluster.provisionamento import ProvisionamentoError, criar_maquina


class Command(BaseCommand):
    help = "Registra uma máquina no cluster e imprime seu token de autenticação (uma vez só)."

    def add_arguments(self, parser):
        parser.add_argument("--apelido", required=True, help="Nome amigável da máquina.")
        parser.add_argument("--organizacao", required=True, help="Slug da organização dona da máquina.")
        parser.add_argument(
            "--modo", required=True, choices=[c.value for c in Maquina.Modo],
            help="'compute' (banco compartilhado) ou 'replica' (stack própria, sincroniza por eventos).",
        )
        parser.add_argument(
            "--dono", help="Username do dono da máquina. Sem isto, usa o owner da organização.",
        )
        parser.add_argument("--hostname", default="", help="Hostname declarado (opcional, só documentação).")
        parser.add_argument(
            "--ollama-endpoint", default="",
            help="URL do Ollama nesta máquina (ex.: http://<ip-vpn>:11434). "
                 "Vazio = a máquina não entra no roteamento de LLM (apps.cluster.llm_router).",
        )

    def handle(self, *args, **opts):
        try:
            maquina, token = criar_maquina(
                apelido=opts["apelido"], organizacao_slug=opts["organizacao"], modo=opts["modo"],
                dono_username=opts.get("dono"), hostname=opts["hostname"],
                ollama_endpoint=opts["ollama_endpoint"],
            )
        except ProvisionamentoError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(f"Máquina '{maquina.apelido}' registrada ({maquina.modo})."))
        self.stdout.write("")
        self.stdout.write("Guarde estas duas linhas agora — o token não aparece de novo:")
        self.stdout.write(f"CLUSTER_MACHINE_ID={maquina.id}")
        self.stdout.write(f"CLUSTER_MACHINE_TOKEN={token}")
