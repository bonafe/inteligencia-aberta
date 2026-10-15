"""Confirma um par pendente depois de conferir a impressão digital esperada.

    manage.py par_confirmar --did did:key:... --impressao-esperada a1b2-...

Idempotente: par já confirmado sai com sucesso e sem mudança.
"""
from django.core.management.base import BaseCommand

from apps.cluster import pares
from apps.cluster.models import Maquina

from ._pares_cli import adicionar_comuns, conferir_impressao, contexto, emitir, erro


class Command(BaseCommand):
    help = "Confirma o par do DID informado, se a impressão digital esperada bater."

    def add_arguments(self, parser):
        parser.add_argument("--did", required=True)
        parser.add_argument("--impressao-esperada", required=True, dest="impressao")
        adicionar_comuns(parser)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        org, admin = contexto(opts["usuario"])
        conferir_impressao(opts["did"], opts["impressao"])
        par = Maquina.objects.filter(organizacao=org, did=opts["did"], eh_local=False).first()
        if par is None:
            raise erro(ValueError("não há par com esse DID (aceite o convite antes)"))
        ja = par.estado == Maquina.Estado.CONFIRMADO
        try:
            par = pares.confirmar(par, admin)
        except pares.ErroEnrolamento as exc:
            raise erro(exc) from None
        emitir(self, {"mudou": not ja, "did": par.did, "estado": par.estado},
               f"par {par.apelido}: {'sem mudança, ' if ja else ''}{par.estado}", opts["json"])
