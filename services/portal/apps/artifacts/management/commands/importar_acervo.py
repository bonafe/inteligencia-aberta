"""Importa uma pasta (ou arquivo) para o acervo pessoal — Fluxo A.

    manage.py importar_acervo /acervo/fotos --organizacao minha-org [--nivel restrito] [--simular]

Guarda cada arquivo no Garage (nomeado pelo hash) e cria um artefato `arquivo` por conteúdo.
Pode ser interrompido e repetido: o que já foi importado e não mudou é pulado sem reler.
Uma pasta com `.git` vira um artefato único (`--sem-projetos` para tratar como arquivos soltos).

Em Docker, monte a pasta em modo somente leitura:
    docker compose run --rm -v /meus/dados:/acervo:ro portal python manage.py importar_acervo /acervo --organizacao minha-org
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Organization, User
from apps.artifacts import acervo
from apps.artifacts.models import Artifact


class Command(BaseCommand):
    help = "Importa uma pasta ou arquivo para o acervo pessoal (retomável, deduplicado)."

    def add_arguments(self, parser):
        parser.add_argument("caminho")
        parser.add_argument("--organizacao", required=True, help="slug da organização dona do acervo")
        parser.add_argument("--usuario", help="username de quem importa (opcional)")
        parser.add_argument("--nivel", default="restrito", choices=Artifact.ClassificationLevel.values)
        parser.add_argument("--simular", action="store_true", help="só conta; não grava nada")
        parser.add_argument("--sem-projetos", action="store_true", help="não empacota pastas com .git")

    def handle(self, *args, **o):
        alvo = Path(o["caminho"]).resolve()
        if not alvo.exists():
            raise CommandError(f"não existe: {alvo}")
        try:
            org = Organization.objects.get(slug=o["organizacao"])
        except Organization.DoesNotExist:
            raise CommandError("organização não encontrada") from None
        usuario = None
        if o["usuario"]:
            try:
                usuario = User.objects.get(username=o["usuario"])
            except User.DoesNotExist:
                raise CommandError("usuário não encontrado") from None
        raiz = alvo.parent if alvo.is_file() else alvo

        def progresso(r, relativo):
            if r.arquivos % 500 == 0:
                self.stdout.write(f"  {r.arquivos} vistos · {r.novos} novos · {r.duplicados} duplicados · "
                                  f"{r.pulados} pulados · {r.total_erros} erros  ({relativo[-60:]})")

        r = acervo.importar_pasta(organizacao=org, usuario=usuario, base=alvo, raiz=str(raiz),
                                  nivel=o["nivel"], simular=o["simular"],
                                  projetos=not o["sem_projetos"], progresso=progresso)
        modo = " (simulação)" if o["simular"] else ""
        self.stdout.write(self.style.SUCCESS(
            f"Concluído{modo}: {r.arquivos} vistos, {r.novos} novos ({r.bytes_novos / 1e9:.2f} GB), "
            f"{r.duplicados} duplicados, {r.pulados} pulados, {r.projetos} projeto(s), {r.total_erros} erro(s)."))
        for e in r.erros:
            self.stderr.write(f"  erro: {e}")
