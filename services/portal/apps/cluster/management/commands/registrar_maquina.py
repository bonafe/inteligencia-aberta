"""Registra uma nova máquina no cluster e imprime o token de autenticação
uma única vez — depois disso, só o hash fica guardado (`Maquina.token_hash`)
e o token em claro é irrecuperável.

Uso (no nó que vai conhecer a máquina, para ela entrar no roteamento de LLM):

    manage.py registrar_maquina --apelido notebook --organizacao minha-org \
        --ollama-endpoint http://100.x.y.z:11434 --gateway-endpoint http://100.x.y.z:8000

Cada instância tem o seu próprio banco; não há registro automático entre elas
(o cadastro de pares da federação virá com o ADR 010).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.cluster.provisionamento import ProvisionamentoError, criar_maquina


class Command(BaseCommand):
    help = "Registra uma máquina no cluster e imprime seu token de autenticação (uma vez só)."

    def add_arguments(self, parser):
        parser.add_argument("--apelido", required=True, help="Nome amigável da máquina.")
        parser.add_argument("--organizacao", required=True, help="Slug da organização dona da máquina.")
        parser.add_argument(
            "--dono", help="Username do dono da máquina. Sem isto, usa o owner da organização.",
        )
        parser.add_argument("--hostname", default="", help="Hostname declarado (opcional, só documentação).")
        parser.add_argument(
            "--ollama-endpoint", default="",
            help="URL do Ollama nesta máquina (ex.: http://<ip-vpn>:11434). "
                 "Vazio = a máquina não entra no roteamento de LLM (apps.cluster.llm_router).",
        )

        parser.add_argument(
            "--gateway-endpoint", default="",
            help="Base do gateway autenticado desta máquina (ex.: http://<ip-vpn>:8000), "
                 "por onde os peers chamam o LLM dela em vez de falar direto com o Ollama.",
        )

    def handle(self, *args, **opts):
        try:
            maquina, token = criar_maquina(
                apelido=opts["apelido"], organizacao_slug=opts["organizacao"],
                dono_username=opts.get("dono"), hostname=opts["hostname"],
                ollama_endpoint=opts["ollama_endpoint"], gateway_endpoint=opts["gateway_endpoint"],
            )
        except ProvisionamentoError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(f"Máquina '{maquina.apelido}' registrada."))
        self.stdout.write("")
        self.stdout.write("Guarde estas duas linhas agora — o token não aparece de novo:")
        self.stdout.write(f"CLUSTER_MACHINE_ID={maquina.id}")
        self.stdout.write(f"CLUSTER_MACHINE_TOKEN={token}")
