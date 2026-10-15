"""Aceita o convite de outra instância (lado B), conferindo a impressão digital esperada.

    manage.py par_aceitar <codigo> --impressao-esperada a1b2-... [--tipo proprio --confirmo-proprio]

Idempotente: se o par já está cadastrado, não faz nada e sai com sucesso.
"""
from django.core.management.base import BaseCommand

from apps.cluster import pares
from apps.cluster.models import Maquina

from ._pares_cli import adicionar_comuns, conferir_impressao, contexto, emitir, erro, tipo_proprio


class Command(BaseCommand):
    help = "Aceita um convite depois de conferir a impressão digital do DID do código."

    def add_arguments(self, parser):
        parser.add_argument("codigo")
        parser.add_argument("--impressao-esperada", required=True, dest="impressao")
        adicionar_comuns(parser, tipo=True)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        org, admin = contexto(opts["usuario"])
        try:
            dados = pares.decodificar_codigo(opts["codigo"])
            conferir_impressao(dados["did"], opts["impressao"])
            if Maquina.objects.filter(organizacao=org, did=dados["did"]).exists():
                emitir(self, {"mudou": False, "did": dados["did"]}, "sem mudança: o par já está cadastrado", opts["json"])
                return
            par = pares.aceitar_convite(org, admin, opts["codigo"], **tipo_proprio(opts))
        except pares.ErroEnrolamento as exc:
            raise erro(exc) from None
        emitir(self, {"mudou": True, "did": par.did, "estado": par.estado}, f"par {par.apelido} cadastrado ({par.estado})", opts["json"])
