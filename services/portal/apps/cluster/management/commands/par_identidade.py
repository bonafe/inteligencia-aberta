"""Identidade pública desta instância para o pareamento: DID, impressão digital e endereço.

    manage.py par_identidade [--json]

Cria a chave se faltar (idempotente). Nada secreto é impresso.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.cluster import pares
from apps.federacao.chaves import garantir_chave_ativa
from apps.federacao.did import impressao_digital

from ._pares_cli import emitir


class Command(BaseCommand):
    help = "Mostra DID, impressão digital e endereço anunciado desta instância."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        chave, _ = garantir_chave_ativa()
        dados = {
            "did": chave.did, "impressao_digital": impressao_digital(chave.did),
            "endpoint": settings.FEDERACAO_ENDPOINT_ANUNCIADO, "nome": pares.nome_da_instancia(),
        }
        emitir(self, dados, f"{dados['nome']}  {dados['impressao_digital']}  {dados['endpoint']}\n{dados['did']}", opts["json"])
