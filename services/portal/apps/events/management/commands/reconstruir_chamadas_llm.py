"""Reconstrói `ChamadaLLM` a partir do log de eventos (`llm.chamada` unificado
e `llm.chamada_ollama` legado, de antes desta tabela existir).

Mesma prova de derivação que `reconstruir_projecoes`/`reconstruir_status_maquinas`:
apagar a tabela e reaplicar o log tem que devolver o mesmo estado. Linhas
reconstruídas a partir de `llm.chamada_ollama` legado ficam com campos que
aquele payload nunca gravou (tokens_entrada/tokens_saida, stop_reason,
finalidade, request_id) vazios — não há como recuperar um dado que a origem
nunca capturou.
"""

from django.core.management.base import BaseCommand

from apps.events.models import ChamadaLLM, PipelineEvent
from apps.events.projecao import aplicar_chamada_llm

STAGES = ("llm.chamada", "llm.chamada_ollama")


class Command(BaseCommand):
    help = "Reconstrói ChamadaLLM a partir do log de eventos (llm.chamada / llm.chamada_ollama)."

    def handle(self, *args, **opts):
        apagadas, _ = ChamadaLLM.objects.all().delete()
        self.stdout.write(f"{apagadas} chamadas apagadas.")

        eventos = PipelineEvent.objects.filter(stage__in=STAGES).order_by("sequence")
        total = eventos.count()
        self.stdout.write(f"Reaplicando {total} eventos…")

        for evento in eventos.iterator():
            aplicar_chamada_llm(evento)

        self.stdout.write(self.style.SUCCESS(
            f"Pronto: {ChamadaLLM.objects.count()} chamadas reconstruídas."
        ))
