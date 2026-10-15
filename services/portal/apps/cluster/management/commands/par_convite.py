"""Cria um convite de pareamento (lado A). O código sai em stdout, uma única vez.

    manage.py par_convite [--tipo proprio --confirmo-proprio] [--json]
"""
from django.core.management.base import BaseCommand

from apps.cluster import pares

from ._pares_cli import adicionar_comuns, contexto, emitir, erro, tipo_proprio


class Command(BaseCommand):
    help = "Cria um convite de pareamento e imprime o código (vale 24 h, uso único)."

    def add_arguments(self, parser):
        adicionar_comuns(parser, tipo=True)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        org, admin = contexto(opts["usuario"])
        try:
            convite, codigo = pares.criar_convite(org, admin, **tipo_proprio(opts))
        except pares.ErroEnrolamento as exc:
            raise erro(exc) from None
        emitir(self, {"codigo": codigo, "expira_em": convite.expira_em.isoformat(), "tipo": convite.tipo}, codigo, opts["json"])
