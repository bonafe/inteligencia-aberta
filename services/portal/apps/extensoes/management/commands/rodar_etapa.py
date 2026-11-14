"""Roda uma etapa de extensão sobre os artefatos que já existem (ADR 023).

    manage.py rodar_etapa email.cabecalhos --organizacao minha-org [--simular]

Para artefatos criados antes de a extensão ser instalada, ou depois de corrigir a etapa. Enfileira uma
execução por artefato que combina com o filtro; a etapa precisa ser idempotente.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization
from apps.artifacts.models import Artifact
from apps.extensoes import registro
from apps.extensoes.tasks import executar_etapa


class Command(BaseCommand):
    help = "Enfileira uma etapa de extensão para os artefatos existentes."

    def add_arguments(self, parser):
        parser.add_argument("chave", nargs="?")
        parser.add_argument("--listar", action="store_true")
        parser.add_argument("--organizacao")
        parser.add_argument("--simular", action="store_true")

    def handle(self, *args, **o):
        if o["listar"]:
            for t in registro.etapas():
                self.stdout.write(f"{t.chave}\t{t.rotulo}" + (f"  (fila {t.fila})" if t.fila else ""))
            return
        definicao = registro.etapa(o["chave"] or "")
        if definicao is None or not o["organizacao"]:
            raise CommandError("informe uma etapa instalada e --organizacao (ou --listar)")
        try:
            org = Organization.objects.get(slug=o["organizacao"])
        except Organization.DoesNotExist:
            raise CommandError("organização não encontrada") from None
        candidatos = Artifact.objects.filter(tenant=org, artifact_type__in=definicao.tipos)
        total = 0
        for artefato in candidatos.iterator():
            if registro.casa(definicao, artefato):
                total += 1
                if not o["simular"]:
                    executar_etapa.apply_async(args=[definicao.chave, str(artefato.id)], queue=definicao.fila or None)
        self.stdout.write(self.style.SUCCESS(f"{total} artefato(s) {'encontrado(s)' if o['simular'] else 'enfileirado(s)'}."))
