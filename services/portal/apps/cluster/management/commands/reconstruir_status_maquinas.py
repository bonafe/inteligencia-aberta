"""Reconstrói `MaquinaStatus`/`MaquinaModeloOllama` a partir do log de eventos
(`maquina.heartbeat`, `llm.chamada_ollama` legado e `llm.chamada` unificado —
ver `apps.events.llm_telemetria`).

Mesma lógica de `manage.py reconstruir_projecoes`, aplicada ao cluster em vez
de ao pipeline: apagar as projeções e reaplicar o log tem que devolver o
mesmo estado, senão elas não seriam de fato derivadas. Os dois tipos de
evento são reaplicados juntos, em ordem de `sequence` — `MaquinaModeloOllama`
é escrita por ambos (heartbeat marca "visto", chamada real atualiza a média
de tokens/segundo), e a ordem entre eles importa para a média bater com o
caminho incremental.
"""

from django.core.management.base import BaseCommand

from apps.cluster.models import MaquinaModeloOllama, MaquinaStatus
from apps.cluster.projecao import aplicar_heartbeat, aplicar_metrica_llm
from apps.events.models import PipelineEvent

PROJETORES = {
    "maquina.heartbeat": aplicar_heartbeat,
    "llm.chamada_ollama": aplicar_metrica_llm,
    "llm.chamada": aplicar_metrica_llm,
}


class Command(BaseCommand):
    help = "Reconstrói MaquinaStatus/MaquinaModeloOllama a partir do log de eventos do cluster."

    def handle(self, *args, **opts):
        apagados_status, _ = MaquinaStatus.objects.all().delete()
        apagados_modelos, _ = MaquinaModeloOllama.objects.all().delete()
        self.stdout.write(f"{apagados_status} status e {apagados_modelos} capacidades de modelo apagados.")

        eventos = PipelineEvent.objects.filter(stage__in=PROJETORES).order_by("sequence")
        total = eventos.count()
        self.stdout.write(f"Reaplicando {total} eventos…")

        for evento in eventos.iterator():
            PROJETORES[evento.stage](evento)

        self.stdout.write(self.style.SUCCESS(
            f"Pronto: {MaquinaStatus.objects.count()} máquinas com status, "
            f"{MaquinaModeloOllama.objects.count()} capacidades de modelo reconstruídas."
        ))
