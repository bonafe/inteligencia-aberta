"""Reconstrói `PipelineRun` inteiramente a partir do log de eventos.

É a prova prática de que a projeção é derivada: apagar a tabela e rodar este
comando tem de devolver exatamente o mesmo estado. Se um dia a regra de
projeção mudar, este comando é o caminho de migração — não há escrita a
preservar em `PipelineRun`.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.events.models import PipelineEvent, PipelineRun
from apps.events.projecao import aplicar_evento

LOTE = 2000


class Command(BaseCommand):
    help = "Reconstrói a projeção PipelineRun a partir de PipelineEvent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--desde-seq", type=int, default=0,
            help="Reaplica apenas eventos com sequence maior que este valor "
                 "(aplicação incremental, sem apagar nada).",
        )
        parser.add_argument(
            "--manter", action="store_true",
            help="Não apaga as projeções existentes antes de reaplicar. "
                 "Exige --desde-seq, porque reaplicar um evento já projetado o "
                 "contaria duas vezes — `aplicar_evento` não deduplica.",
        )

    def handle(self, *args, **opts):
        desde = opts["desde_seq"]

        if opts["manter"] and not desde:
            raise CommandError(
                "--manter exige --desde-seq: sem ele, os eventos já projetados "
                "seriam contados de novo."
            )

        if not opts["manter"]:
            apagadas, _ = PipelineRun.objects.all().delete()
            self.stdout.write(f"{apagadas} projeções apagadas.")

        # `avulso=False` é a mesma condição que o caminho incremental aplica.
        # Sem isto, a reconstrução criaria uma "execução" para cada worker que
        # subiu, e deixaria de reproduzir o estado que ela deveria reproduzir.
        total = PipelineEvent.objects.filter(sequence__gt=desde, avulso=False).count()
        self.stdout.write(f"Reaplicando {total} eventos (sequence > {desde})…")

        processados = 0
        ultimo = desde
        while True:
            lote = list(
                PipelineEvent.objects.filter(sequence__gt=ultimo, avulso=False)
                .order_by("sequence")[:LOTE]
            )
            if not lote:
                break
            with transaction.atomic():
                for evento in lote:
                    aplicar_evento(evento)
            processados += len(lote)
            ultimo = lote[-1].sequence
            self.stdout.write(f"  {processados}/{total}…")

        runs = PipelineRun.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"Pronto: {processados} eventos reaplicados, {runs} execuções reconstruídas."
        ))
