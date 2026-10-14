"""Importa para o acervo os itens escolhidos de um caso do IPED — Fluxo B.

    manage.py importar_caso_iped <id-do-processamento> --consulta 'category:"Other Images"' [--simular]
    manage.py importar_caso_iped <id-do-processamento> --bookmark fotos-da-familia

Exige `--bookmark` OU `--consulta` (sintaxe Lucene do IPED): **não existe "importar o caso inteiro"**.
Rode dentro do `worker-iped`, que enxerga a entrada (somente leitura) e o volume de trabalho:

    docker compose exec worker-iped python manage.py importar_caso_iped <id> --consulta '...' --simular
"""

from django.core.management.base import BaseCommand, CommandError

from apps.artifacts.models import Artifact
from apps.forense import importacao
from apps.forense.models import ProcessamentoIPED


class Command(BaseCommand):
    help = "Importa para o acervo os itens (bookmark ou consulta) de um processamento IPED concluído."

    def add_arguments(self, parser):
        parser.add_argument("processamento")
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument("--bookmark")
        grupo.add_argument("--consulta")
        parser.add_argument("--nivel", default="restrito", choices=Artifact.ClassificationLevel.values)
        parser.add_argument("--simular", action="store_true", help="só conta; não grava nada")

    def handle(self, *args, **o):
        try:
            pedido = ProcessamentoIPED.objects.select_related("organizacao").get(pk=o["processamento"])
        except (ProcessamentoIPED.DoesNotExist, ValueError):
            raise CommandError("processamento não encontrado") from None
        try:
            arquivo = importacao.exportar_itens(pedido, bookmark=o["bookmark"] or "", consulta=o["consulta"] or "")
            r = importacao.importar_itens(pedido, arquivo, nivel=o["nivel"], simular=o["simular"])
        except importacao.ErroImportacao as exc:
            raise CommandError(str(exc)) from None
        a = r.do_acervo
        modo = " (simulação)" if o["simular"] else ""
        self.stdout.write(self.style.SUCCESS(
            f"Concluído{modo}: {r.selecionados} selecionados → {a.novos} novos, {a.duplicados} duplicados, "
            f"{a.pulados} pulados, {r.pastas} pastas, {r.sem_arquivo_no_disco} sem arquivo no disco, "
            f"{a.total_erros} erro(s)."))
        for e in a.erros:
            self.stderr.write(f"  erro: {e}")
