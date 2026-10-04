"""Mostra (e, se preciso, cria) a chave Ed25519 da instância.

    manage.py chave_instancia            # só mostra; sai com erro se não houver
    manage.py chave_instancia --criar    # cria se não houver (idempotente)

Imprime apenas o `did:key` (público). A chave privada nunca é exibida.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.federacao.chaves import chave_ativa, garantir_chave_ativa


class Command(BaseCommand):
    help = "Mostra o did:key da instância; --criar gera a chave se ainda não existir."

    def add_arguments(self, parser):
        parser.add_argument("--criar", action="store_true")

    def handle(self, *args, **opts):
        if opts["criar"]:
            chave, criada = garantir_chave_ativa()
            self.stdout.write(f"{chave.did}{'  (criada agora)' if criada else ''}")
            return
        chave = chave_ativa()
        if chave is None:
            raise CommandError("A instância ainda não tem chave. Use --criar.")
        self.stdout.write(chave.did)
