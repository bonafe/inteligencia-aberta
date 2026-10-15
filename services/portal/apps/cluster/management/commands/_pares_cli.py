"""Apoio dos comandos `par_*` (pareamento sem tela, para a automação do cluster).

A segurança do enrolamento continua a mesma: quem chama informa a **impressão digital
esperada** do outro lado, lida por um canal que já é confiável (o SSH do Ansible), e o
comando recusa se o DID não bater. Sem `--impressao-esperada` não há aceite nem confirmação.
"""
import json

from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.cluster import pares
from apps.cluster.models import Maquina
from apps.federacao.did import impressao_digital


def contexto(usuario: str | None):
    """`(organização, administrador)` desta instância; `CommandError` se não der para saber."""
    org = pares.organizacao_da_instancia()
    if org is None:
        raise CommandError("não sei qual é a organização desta instância (crie o administrador antes)")
    if usuario:
        admin = User.objects.filter(username=usuario).first()
        if admin is None:
            raise CommandError(f"usuário {usuario!r} não existe")
    else:
        admin = org.owner
    return org, admin


def conferir_impressao(did: str, esperada: str) -> None:
    obtida = impressao_digital(did)
    if (esperada or "").strip().lower() != obtida:
        raise CommandError(f"impressão digital divergente: esperada {esperada!r}, o DID tem {obtida}")


def tipo_proprio(opts) -> dict:
    tipo = opts["tipo"]
    if tipo == Maquina.Tipo.PROPRIO and not opts["confirmo_proprio"]:
        raise CommandError("tipo 'proprio' exige --confirmo-proprio")
    return {"tipo": tipo, "confirmou_proprio": opts["confirmo_proprio"]}


def adicionar_comuns(parser, *, tipo: bool = False):
    parser.add_argument("--usuario", help="administrador que age (padrão: dono da organização da instância)")
    if tipo:
        parser.add_argument("--tipo", choices=Maquina.Tipo.values, default=Maquina.Tipo.TERCEIRO)
        parser.add_argument("--confirmo-proprio", action="store_true", dest="confirmo_proprio")


def emitir(comando, dados: dict, texto: str, json_saida: bool) -> None:
    comando.stdout.write(json.dumps(dados, sort_keys=True) if json_saida else texto)


def erro(exc: Exception) -> CommandError:
    return CommandError(str(exc))
