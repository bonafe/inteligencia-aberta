"""Importa de uma fonte de ingestão declarada por uma extensão (ADR 023).

    manage.py importar_fonte email.pasta /dados/emails --organizacao minha-org [--nivel restrito] [--simular] [--opcao k=v]

`manage.py importar_fonte --listar` mostra as fontes instaladas.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from apps.accounts.models import Organization, User
from apps.artifacts.models import Artifact
from apps.extensoes import registro


class Command(BaseCommand):
    help = "Importa de uma fonte de ingestão de extensão."

    def add_arguments(self, parser):
        parser.add_argument("chave", nargs="?")
        parser.add_argument("caminho", nargs="?")
        parser.add_argument("--listar", action="store_true")
        parser.add_argument("--organizacao")
        parser.add_argument("--usuario")
        parser.add_argument("--nivel", default="restrito", choices=Artifact.ClassificationLevel.values)
        parser.add_argument("--simular", action="store_true")
        parser.add_argument("--opcao", action="append", default=[], metavar="K=V")

    def handle(self, *args, **o):
        if o["listar"]:
            for f in registro.fontes():
                self.stdout.write(f"{f.chave}\t{f.rotulo} — {f.descricao}")
            return
        if not o["chave"] or not o["caminho"] or not o["organizacao"]:
            raise CommandError("informe a chave, o caminho e --organizacao (ou --listar)")
        fonte = registro.fonte(o["chave"])
        if fonte is None:
            raise CommandError(f"fonte desconhecida: {o['chave']} (veja --listar)")
        caminho = Path(o["caminho"]).resolve()
        if not caminho.exists():
            raise CommandError(f"não existe: {caminho}")
        try:
            org = Organization.objects.get(slug=o["organizacao"])
        except Organization.DoesNotExist:
            raise CommandError("organização não encontrada") from None
        usuario = User.objects.filter(username=o["usuario"]).first() if o["usuario"] else None
        opcoes = dict(item.split("=", 1) for item in o["opcao"] if "=" in item)
        resumo = import_string(fonte.importar)(organizacao=org, usuario=usuario, caminho=caminho,
                                               nivel=o["nivel"], simular=o["simular"], opcoes=opcoes)
        self.stdout.write(self.style.SUCCESS(f"{fonte.rotulo}: " + json.dumps(resumo, ensure_ascii=False, default=str)))
