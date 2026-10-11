"""Importa um pacote offline assinado (F1b).

    manage.py importar_pacote --organizacao <slug> --arquivo pacote.zip

Confere o pacote inteiro (assinaturas, hashes, par confirmado, destino) antes de aplicar
qualquer coisa; depois cada objeto passa pelas regras de *receber* desta organização. O que
as regras recusam é contado aqui, sem explicação ao emissor.
"""

import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.federacao import pacote


class Command(BaseCommand):
    help = "Importa um pacote offline assinado de um par confirmado."

    def add_arguments(self, parser):
        parser.add_argument("--organizacao", required=True, help="Slug da organização que tem o par.")
        parser.add_argument("--arquivo", required=True)

    def handle(self, *args, **o):
        try:
            org = Organization.objects.get(slug=o["organizacao"])
        except Organization.DoesNotExist:
            raise CommandError("organização não encontrada") from None
        if os.path.getsize(o["arquivo"]) > pacote.LIMITE_TOTAL:
            raise CommandError("arquivo grande demais")
        with open(o["arquivo"], "rb") as f:
            dados = f.read()
        try:
            r = pacote.importar(organizacao=org, dados=dados)
        except pacote.ErroPacote as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            f"{r.importados} importado(s), {r.associados} só associado(s) ao espaço, {r.ja_existiam} já existiam, "
            f"{r.recusados} recusado(s) pelas regras, {r.alegacoes} alegação(ões)."
        )
        if r.lacuna:
            self.stdout.write(self.style.WARNING("  Atenção: há pacotes anteriores deste emissor que não foram importados aqui."))
        for erro in r.erros:
            self.stdout.write(self.style.ERROR(f"  ERRO {erro}"))
